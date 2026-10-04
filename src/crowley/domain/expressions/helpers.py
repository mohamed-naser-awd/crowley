"""Expression helpers (docs/SPEC.md §10.4) and the registry they live in."""

import base64
import binascii
import functools
import hashlib
import html
import json
import math
import re
import unicodedata
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta, timezone, tzinfo
from decimal import ROUND_HALF_UP, Decimal
from typing import Protocol
from urllib.parse import parse_qs, quote, unquote, urljoin, urlsplit

from crowley.domain.errors import ExecutionError, LimitError
from crowley.domain.services import Clock, RandomSource, RegexEngine
from crowley.domain.values import (
    Value,
    compare,
    is_number,
    to_bool,
    to_float,
    to_int,
    to_json_text,
    to_str,
    type_of,
    values_equal,
)

MAX_GENERATED_ITEMS = 100_000


class Function(Protocol):
    """A lambda value passed to a helper."""

    params: tuple[str, ...]

    def __call__(self, *args: Value) -> Value: ...


class HelperContext(Protocol):
    """What a helper may use besides its arguments."""

    clock: Clock | None
    random: RandomSource | None
    regex: RegexEngine | None

    def tick(self, steps: int = 1) -> None:
        """Charge ``steps`` against the evaluation budget (``E703`` when exceeded)."""
        ...


HelperFn = Callable[..., Value]


@dataclass(frozen=True, slots=True, kw_only=True)
class HelperSpec:
    name: str
    fn: HelperFn
    min_args: int
    max_args: int | None  # None = variadic
    lambda_args: frozenset[int] = frozenset()
    """Argument positions that take a lambda (``x => ...``)."""

    def accepts(self, count: int) -> bool:
        return count >= self.min_args and (self.max_args is None or count <= self.max_args)

    @property
    def arity_text(self) -> str:
        """E.g. ``"1 argument"``, ``"2 to 3 arguments"``, ``"at least 1 argument"``."""
        if self.max_args is None:
            count, last = f"at least {self.min_args}", self.min_args
        elif self.min_args == self.max_args:
            count, last = str(self.min_args), self.min_args
        else:
            count, last = f"{self.min_args} to {self.max_args}", self.max_args
        return f"{count} argument{'' if last == 1 else 's'}"


@dataclass(slots=True)
class HelperTable:
    """Name → helper. ``HelperTable.builtin()`` has every SPEC §10.4 helper."""

    helpers: dict[str, HelperSpec] = field(default_factory=dict)

    @classmethod
    def builtin(cls) -> "HelperTable":
        return cls(dict(_BUILTINS))

    def get(self, name: str) -> HelperSpec | None:
        return self.helpers.get(name)

    def register(self, spec: HelperSpec, *, replace: bool = False) -> None:
        if spec.name in self.helpers and not replace:
            raise ValueError(f"helper {spec.name!r} is already registered")
        self.helpers[spec.name] = spec

    def __contains__(self, name: object) -> bool:
        return name in self.helpers

    def copy(self) -> "HelperTable":
        return HelperTable(dict(self.helpers))


# ── argument checks ───────────────────────────────────────────────────────────


def fail(message: str, value: object = None, hint: str | None = None) -> ExecutionError:
    return ExecutionError("E501", message, value=value, hint=hint)


def _expect_str(name: str, value: Value, what: str = "argument") -> str:
    if not isinstance(value, str):
        raise fail(f"{name}(): {what} must be a string, got {type_of(value)}", value)
    return value


def _expect_list(name: str, value: Value) -> list[Value]:
    if not isinstance(value, list):
        raise fail(f"{name}(): expected a list, got {type_of(value)}", value)
    return value


def _expect_object(name: str, value: Value) -> dict[str, Value]:
    if not isinstance(value, dict):
        raise fail(f"{name}(): expected an object, got {type_of(value)}", value)
    return value


def _expect_int(name: str, value: Value, what: str = "argument") -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise fail(f"{name}(): {what} must be an integer, got {type_of(value)}", value)
    return value


def _expect_number(name: str, value: Value) -> int | float:
    if not is_number(value):
        raise fail(f"{name}(): expected a number, got {type_of(value)}", value)
    return value  # type: ignore[return-value]


def _expect_bool_result(name: str, value: Value) -> bool:
    if not isinstance(value, bool):
        raise fail(f"{name}(): the function must return true or false, got {type_of(value)}", value)
    return value


def _regex(ctx: HelperContext) -> RegexEngine:
    if ctx.regex is None:  # pragma: no cover - always wired by the runtime
        raise fail("regex helpers are not available in this context")
    return ctx.regex


def _canonical(value: Value) -> object:
    """Hashable key so that values_equal(a, b) ⇔ _canonical(a) == _canonical(b)."""
    if isinstance(value, bool):
        return ("b", value)
    if is_number(value):
        number = value
        if isinstance(number, float) and number.is_integer():
            number = int(number)
        return ("n", number)
    if isinstance(value, list):
        return ("l", tuple(_canonical(v) for v in value))
    if isinstance(value, dict):
        return ("o", tuple(sorted((k, _canonical(v)) for k, v in value.items())))
    if isinstance(value, str | bytes) or value is None:
        return (type_of(value), value)
    return ("h", id(value))


# ── types & null handling ─────────────────────────────────────────────────────


def h_str(ctx: HelperContext, x: Value) -> Value:
    return to_str(x)


def h_int(ctx: HelperContext, x: Value) -> Value:
    return to_int(x)


def h_float(ctx: HelperContext, x: Value) -> Value:
    return to_float(x)


def h_bool(ctx: HelperContext, x: Value) -> Value:
    return to_bool(x)


def h_type_of(ctx: HelperContext, x: Value) -> Value:
    return type_of(x)


def h_is_null(ctx: HelperContext, x: Value) -> Value:
    return x is None


def h_default(ctx: HelperContext, x: Value, fallback: Value) -> Value:
    return fallback if x is None else x


def h_coalesce(ctx: HelperContext, *values: Value) -> Value:
    return next((v for v in values if v is not None), None)


# ── strings ───────────────────────────────────────────────────────────────────


def h_len(ctx: HelperContext, x: Value) -> Value:
    if isinstance(x, str | list | dict):
        return len(x)
    raise fail(f"len(): expected a string, list or object, got {type_of(x)}", x)


def h_trim(ctx: HelperContext, s: Value) -> Value:
    return _expect_str("trim", s).strip()


def h_lower(ctx: HelperContext, s: Value) -> Value:
    return _expect_str("lower", s).lower()


def h_upper(ctx: HelperContext, s: Value) -> Value:
    return _expect_str("upper", s).upper()


def h_replace(ctx: HelperContext, s: Value, old: Value, new: Value) -> Value:
    return _expect_str("replace", s).replace(
        _expect_str("replace", old), _expect_str("replace", new)
    )


def h_split(ctx: HelperContext, s: Value, sep: Value) -> Value:
    separator = _expect_str("split", sep, "separator")
    if not separator:
        raise fail("split(): separator must not be empty")
    return list(_expect_str("split", s).split(separator))


def _join_part(x: Value) -> str:
    if isinstance(x, str):
        return x
    if is_number(x) or isinstance(x, bool):
        return to_str(x)
    raise fail(f"join(): items must be strings, numbers or booleans, got {type_of(x)}", x)


def h_join(ctx: HelperContext, xs: Value, sep: Value) -> Value:
    return _expect_str("join", sep, "separator").join(
        _join_part(x) for x in _expect_list("join", xs)
    )


def h_starts_with(ctx: HelperContext, s: Value, prefix: Value) -> Value:
    return _expect_str("starts_with", s).startswith(_expect_str("starts_with", prefix))


def h_ends_with(ctx: HelperContext, s: Value, suffix: Value) -> Value:
    return _expect_str("ends_with", s).endswith(_expect_str("ends_with", suffix))


def h_contains(ctx: HelperContext, container: Value, x: Value) -> Value:
    if isinstance(container, str):
        return _expect_str("contains", x) in container
    if isinstance(container, list):
        return any(values_equal(item, x) for item in container)
    if isinstance(container, dict):
        return _expect_str("contains", x) in container
    raise fail(
        f"contains(): expected a string, list or object, got {type_of(container)}", container
    )


def _pad_char(name: str, ch: Value) -> str:
    text = _expect_str(name, ch, "fill character")
    if len(text) != 1:
        raise fail(f"{name}(): fill character must be exactly one character", ch)
    return text


def h_pad_left(ctx: HelperContext, s: Value, width: Value, ch: Value = " ") -> Value:
    return _expect_str("pad_left", s).rjust(
        _expect_int("pad_left", width), _pad_char("pad_left", ch)
    )


def h_pad_right(ctx: HelperContext, s: Value, width: Value, ch: Value = " ") -> Value:
    return _expect_str("pad_right", s).ljust(
        _expect_int("pad_right", width), _pad_char("pad_right", ch)
    )


def h_slugify(ctx: HelperContext, s: Value) -> Value:
    text = (
        unicodedata.normalize("NFKD", _expect_str("slugify", s)).encode("ascii", "ignore").decode()
    )
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


# ── regex ─────────────────────────────────────────────────────────────────────


def h_regex_match(ctx: HelperContext, s: Value, pattern: Value) -> Value:
    return _regex(ctx).search(
        _expect_str("regex_match", pattern, "pattern"), _expect_str("regex_match", s)
    )


def h_regex_find(ctx: HelperContext, s: Value, pattern: Value, group: Value = 0) -> Value:
    if not (isinstance(group, str) or (isinstance(group, int) and not isinstance(group, bool))):
        raise fail("regex_find(): group must be a number or a name", group)
    return _regex(ctx).find(
        _expect_str("regex_find", pattern, "pattern"), _expect_str("regex_find", s), group
    )


def h_regex_find_all(ctx: HelperContext, s: Value, pattern: Value) -> Value:
    found = _regex(ctx).find_all(
        _expect_str("regex_find_all", pattern, "pattern"), _expect_str("regex_find_all", s)
    )
    ctx.tick(len(found))
    return list(found)  # type: ignore[arg-type]


def h_regex_replace(ctx: HelperContext, s: Value, pattern: Value, replacement: Value) -> Value:
    return _regex(ctx).replace(
        _expect_str("regex_replace", pattern, "pattern"),
        _expect_str("regex_replace", s),
        _expect_str("regex_replace", replacement, "replacement"),
    )


# ── numbers ───────────────────────────────────────────────────────────────────


def h_round(ctx: HelperContext, x: Value, digits: Value = 0) -> Value:
    """Round half away from zero. Returns an int when ``digits`` is 0."""
    number = _expect_number("round", x)
    places = _expect_int("round", digits, "digits")
    if isinstance(number, float) and not math.isfinite(number):
        raise fail("round(): cannot round a non-finite number", x)
    quantum = Decimal(1).scaleb(-places)
    result = Decimal(str(number)).quantize(quantum, rounding=ROUND_HALF_UP)
    return int(result) if places <= 0 else float(result)


def h_abs(ctx: HelperContext, x: Value) -> Value:
    return abs(_expect_number("abs", x))


def _extreme(name: str, values: tuple[Value, ...], pick: int) -> Value:
    items = list(_expect_list(name, values[0])) if len(values) == 1 else list(values)
    if not items:
        raise fail(f"{name}(): no values")
    best = items[0]
    for item in items[1:]:
        if compare(item, best) == pick:
            best = item
    return best


def h_min(ctx: HelperContext, *values: Value) -> Value:
    return _extreme("min", values, -1)


def h_max(ctx: HelperContext, *values: Value) -> Value:
    return _extreme("max", values, 1)


def h_sum(ctx: HelperContext, xs: Value) -> Value:
    total: int | float = 0
    for item in _expect_list("sum", xs):
        total += _expect_number("sum", item)
    return total


_NUMBER_SUFFIX = {"k": 1_000, "m": 1_000_000, "b": 1_000_000_000}
_NUMBER_TEXT = re.compile(r"([+-]?)(\d[\d.,'\s]*)([kmb](?![a-z]))?", re.IGNORECASE)


def h_parse_number(ctx: HelperContext, s: Value, locale: Value = "en") -> Value:
    """Parse '1,234.5' (en), '1.234,5' (de/eu), '$12k', '3.4M'. Integral results are ints."""
    text = _expect_str("parse_number", s).strip()
    loc = _expect_str("parse_number", locale, "locale").lower()
    if loc not in ("en", "de", "eu"):
        raise fail("parse_number(): locale must be 'en', 'de' or 'eu'", locale)
    m = _NUMBER_TEXT.search(text)
    if not m:
        raise fail(f"parse_number(): no number in {text!r}", s)
    sign, digits, suffix = m.group(1), m.group(2), m.group(3)
    digits = re.sub(r"[\s']", "", digits).rstrip(".,")
    thousands, decimal = (",", ".") if loc == "en" else (".", ",")
    digits = digits.replace(thousands, "").replace(decimal, ".")
    try:
        number = Decimal(sign + digits)
    except ArithmeticError:
        raise fail(f"parse_number(): no number in {text!r}", s) from None
    if suffix:
        number *= _NUMBER_SUFFIX[suffix.lower()]
    return int(number) if number == number.to_integral_value() else float(number)


# ── lists ─────────────────────────────────────────────────────────────────────


def _flatten(items: Iterable[Value], depth: int) -> list[Value]:
    out: list[Value] = []
    for item in items:
        if isinstance(item, list) and depth > 0:
            out.extend(_flatten(item, depth - 1))
        else:
            out.append(item)
    return out


def h_flatten(ctx: HelperContext, xs: Value, depth: Value = 1) -> Value:
    levels = _expect_int("flatten", depth, "depth")
    if levels < 0:
        raise fail("flatten(): depth must not be negative", depth)
    result = _flatten(_expect_list("flatten", xs), levels)
    ctx.tick(len(result))
    return result


def h_unique(ctx: HelperContext, xs: Value) -> Value:
    seen: set[object] = set()
    out: list[Value] = []
    for item in _expect_list("unique", xs):
        key = _canonical(item)
        if key not in seen:
            seen.add(key)
            out.append(item)
    ctx.tick(len(out))
    return out


def _sorted(items: list[Value], key: Callable[[Value], Value]) -> list[Value]:
    decorated = [(key(item), item) for item in items]

    def by_key(a: tuple[Value, Value], b: tuple[Value, Value]) -> int:
        return compare(a[0], b[0])

    decorated.sort(key=functools.cmp_to_key(by_key))
    return [item for _, item in decorated]


def h_sort(ctx: HelperContext, xs: Value) -> Value:
    items = _expect_list("sort", xs)
    ctx.tick(len(items))
    return _sorted(items, lambda v: v)


def h_sort_by(ctx: HelperContext, xs: Value, fn: Function) -> Value:
    return _sorted(_expect_list("sort_by", xs), fn)


def _call_with_index(fn: Function, item: Value, index: int) -> Value:
    return fn(item, index) if len(fn.params) > 1 else fn(item)


def h_map(ctx: HelperContext, xs: Value, fn: Function) -> Value:
    return [_call_with_index(fn, item, i) for i, item in enumerate(_expect_list("map", xs))]


def h_filter(ctx: HelperContext, xs: Value, fn: Function) -> Value:
    return [
        item
        for i, item in enumerate(_expect_list("filter", xs))
        if _expect_bool_result("filter", _call_with_index(fn, item, i))
    ]


def h_find(ctx: HelperContext, xs: Value, fn: Function) -> Value:
    for i, item in enumerate(_expect_list("find", xs)):
        if _expect_bool_result("find", _call_with_index(fn, item, i)):
            return item
    return None


def h_any(ctx: HelperContext, xs: Value, fn: Function | None = None) -> Value:
    items = _expect_list("any", xs)
    if fn is None:
        return any(_expect_bool_result("any", item) for item in items)
    return any(_expect_bool_result("any", _call_with_index(fn, x, i)) for i, x in enumerate(items))


def h_all(ctx: HelperContext, xs: Value, fn: Function | None = None) -> Value:
    items = _expect_list("all", xs)
    if fn is None:
        return all(_expect_bool_result("all", item) for item in items)
    return all(_expect_bool_result("all", _call_with_index(fn, x, i)) for i, x in enumerate(items))


def h_first(ctx: HelperContext, xs: Value) -> Value:
    items = _expect_list("first", xs)
    return items[0] if items else None


def h_last(ctx: HelperContext, xs: Value) -> Value:
    items = _expect_list("last", xs)
    return items[-1] if items else None


def h_range(ctx: HelperContext, a: Value, b: Value, step: Value = 1) -> Value:
    start, stop, by = (_expect_int("range", v) for v in (a, b, step))
    if by == 0:
        raise fail("range(): step must not be zero")
    size = len(range(start, stop, by))
    if size > MAX_GENERATED_ITEMS:
        raise LimitError(
            "E703", f"range(): {size} items exceeds the limit of {MAX_GENERATED_ITEMS}"
        )
    ctx.tick(size)
    return list(range(start, stop, by))


def h_chunk(ctx: HelperContext, xs: Value, n: Value) -> Value:
    size = _expect_int("chunk", n, "size")
    if size <= 0:
        raise fail("chunk(): size must be positive", n)
    items = _expect_list("chunk", xs)
    return [items[i : i + size] for i in range(0, len(items), size)]


def h_zip(ctx: HelperContext, a: Value, b: Value) -> Value:
    return [[x, y] for x, y in zip(_expect_list("zip", a), _expect_list("zip", b), strict=False)]


def h_group_by(ctx: HelperContext, xs: Value, fn: Function) -> Value:
    groups: dict[str, Value] = {}
    for i, item in enumerate(_expect_list("group_by", xs)):
        key = _call_with_index(fn, item, i)
        label = key if isinstance(key, str) else to_str(key)
        bucket = groups.setdefault(label, [])
        assert isinstance(bucket, list)
        bucket.append(item)
    return groups


# ── objects ───────────────────────────────────────────────────────────────────


def h_keys(ctx: HelperContext, o: Value) -> Value:
    return list(_expect_object("keys", o).keys())


def h_values(ctx: HelperContext, o: Value) -> Value:
    return list(_expect_object("values", o).values())


def h_entries(ctx: HelperContext, o: Value) -> Value:
    return [[k, v] for k, v in _expect_object("entries", o).items()]


def h_merge(ctx: HelperContext, *objects: Value) -> Value:
    merged: dict[str, Value] = {}
    for obj in objects:
        merged.update(_expect_object("merge", obj))
    return merged


def _key_list(name: str, keys: Value) -> list[str]:
    return [_expect_str(name, k, "key") for k in _expect_list(name, keys)]


def h_pick(ctx: HelperContext, o: Value, keys: Value) -> Value:
    obj = _expect_object("pick", o)
    return {k: obj[k] for k in _key_list("pick", keys) if k in obj}


def h_omit(ctx: HelperContext, o: Value, keys: Value) -> Value:
    drop = set(_key_list("omit", keys))
    return {k: v for k, v in _expect_object("omit", o).items() if k not in drop}


_PATH_PART = re.compile(r"([^.\[\]]+)|\[(-?\d+)\]")


def h_get(ctx: HelperContext, o: Value, path: Value, fallback: Value = None) -> Value:
    """``get(o, 'a.b[0].c', default)``: safe deep lookup; ``path`` may also be a list of keys."""
    parts: list[str | int]
    if isinstance(path, list):
        parts = []
        for p in path:
            if isinstance(p, str) or (isinstance(p, int) and not isinstance(p, bool)):
                parts.append(p)
            else:
                raise fail("get(): path items must be strings or integers", path)
    else:
        text = _expect_str("get", path, "path")
        parts = [
            m.group(1) if m.group(1) is not None else int(m.group(2))
            for m in _PATH_PART.finditer(text)
        ]
    current: Value = o
    for part in parts:
        in_object = isinstance(part, str) and isinstance(current, dict) and part in current
        in_list = (
            isinstance(part, int)
            and isinstance(current, list)
            and -len(current) <= part < len(current)
        )
        if not (in_object or in_list):
            return fallback
        current = current[part]  # type: ignore[index]
    return current


# ── URLs ──────────────────────────────────────────────────────────────────────


def h_url_join(ctx: HelperContext, base: Value, rel: Value) -> Value:
    return urljoin(_expect_str("url_join", base), _expect_str("url_join", rel))


def h_url_parse(ctx: HelperContext, u: Value) -> Value:
    parts = urlsplit(_expect_str("url_parse", u))
    query: dict[str, Value] = {}
    for key, values in parse_qs(parts.query, keep_blank_values=True).items():
        query[key] = values[0] if len(values) == 1 else list(values)
    try:
        port = parts.port
    except ValueError:
        raise fail(f"url_parse(): invalid port in {u!r}", u) from None
    return {
        "scheme": parts.scheme,
        "host": parts.hostname,
        "port": port,
        "path": parts.path,
        "query": query,
        "fragment": parts.fragment,
    }


def h_url_encode(ctx: HelperContext, s: Value) -> Value:
    return quote(_expect_str("url_encode", s), safe="")


def h_url_decode(ctx: HelperContext, s: Value) -> Value:
    return unquote(_expect_str("url_decode", s))


def h_query_get(ctx: HelperContext, u: Value, name: Value) -> Value:
    values = parse_qs(urlsplit(_expect_str("query_get", u)).query, keep_blank_values=True)
    found = values.get(_expect_str("query_get", name, "name"))
    return found[0] if found else None


# ── encoding ──────────────────────────────────────────────────────────────────


def _from_json(obj: object) -> Value:
    if isinstance(obj, float) and not math.isfinite(obj):
        raise fail("json_parse(): non-finite numbers are not allowed")
    return obj  # type: ignore[return-value]


def h_json_parse(ctx: HelperContext, s: Value) -> Value:
    try:
        return _from_json(
            json.loads(_expect_str("json_parse", s), parse_constant=lambda c: _from_json(float(c)))
        )
    except json.JSONDecodeError as exc:
        raise fail(f"json_parse(): invalid JSON: {exc.msg} at position {exc.pos}", s) from None


def h_json_dump(ctx: HelperContext, x: Value) -> Value:
    return to_json_text(x)


def h_base64_encode(ctx: HelperContext, s: Value) -> Value:
    return base64.b64encode(_expect_str("base64_encode", s).encode()).decode()


def h_base64_decode(ctx: HelperContext, s: Value) -> Value:
    try:
        return base64.b64decode(_expect_str("base64_decode", s), validate=True).decode()
    except (binascii.Error, UnicodeDecodeError):
        raise fail("base64_decode(): not valid base64-encoded UTF-8 text", s) from None


def h_html_unescape(ctx: HelperContext, s: Value) -> Value:
    return html.unescape(_expect_str("html_unescape", s))


def h_md5(ctx: HelperContext, s: Value) -> Value:
    return hashlib.md5(_expect_str("md5", s).encode(), usedforsecurity=False).hexdigest()


def h_sha256(ctx: HelperContext, s: Value) -> Value:
    return hashlib.sha256(_expect_str("sha256", s).encode()).hexdigest()


# ── dates ─────────────────────────────────────────────────────────────────────

_OFFSET = re.compile(r"^([+-])(\d{2}):?(\d{2})$")


def _clock(ctx: HelperContext) -> Clock:
    if ctx.clock is None:  # pragma: no cover - always wired by the runtime
        raise fail("date helpers are not available in this context")
    return ctx.clock


def _timezone(ctx: HelperContext, name: str) -> tzinfo:
    if name.upper() in ("UTC", "Z"):
        return UTC
    m = _OFFSET.match(name)
    if m:
        minutes = int(m.group(2)) * 60 + int(m.group(3))
        return timezone(timedelta(minutes=-minutes if m.group(1) == "-" else minutes))
    try:
        return _clock(ctx).tz(name)
    except ValueError:
        raise fail(f"unknown timezone {name!r}", name) from None


def iso_utc(moment: datetime) -> str:
    text = moment.astimezone(UTC).isoformat()
    return text.removesuffix("+00:00") + "Z"


def h_now(ctx: HelperContext) -> Value:
    return iso_utc(_clock(ctx).now())


def h_parse_date(ctx: HelperContext, s: Value, fmt: Value = None, tz: Value = "UTC") -> Value:
    """Parse ``s`` (ISO-8601, or ``fmt`` as strftime) → UTC ISO-8601. Naive times use ``tz``."""
    text = _expect_str("parse_date", s).strip()
    zone = _timezone(ctx, _expect_str("parse_date", tz, "tz"))
    try:
        if fmt is None:
            moment = datetime.fromisoformat(text)
        else:
            moment = datetime.strptime(text, _expect_str("parse_date", fmt, "format"))
    except ValueError as exc:
        raise fail(f"parse_date(): cannot parse {text!r}: {exc}", s) from None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=zone)
    return iso_utc(moment)


def h_format_date(ctx: HelperContext, iso: Value, fmt: Value, tz: Value = "UTC") -> Value:
    """Format an ISO-8601 timestamp with a strftime pattern, in ``tz`` (default UTC)."""
    text = _expect_str("format_date", iso)
    try:
        moment = datetime.fromisoformat(text)
    except ValueError:
        raise fail(f"format_date(): not an ISO-8601 timestamp: {text!r}", iso) from None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    zone = _timezone(ctx, _expect_str("format_date", tz, "tz"))
    return moment.astimezone(zone).strftime(_expect_str("format_date", fmt, "format"))


# ── misc ──────────────────────────────────────────────────────────────────────


def h_uuid(ctx: HelperContext) -> Value:
    if ctx.random is None:  # pragma: no cover - always wired by the runtime
        raise fail("uuid() is not available in this context")
    return ctx.random.uuid4()


def _spec(
    name: str, fn: HelperFn, min_args: int, max_args: int | None = -1, lambdas: Iterable[int] = ()
) -> HelperSpec:
    return HelperSpec(
        name=name,
        fn=fn,
        min_args=min_args,
        max_args=min_args if max_args == -1 else max_args,
        lambda_args=frozenset(lambdas),
    )


_BUILTINS: Mapping[str, HelperSpec] = {
    s.name: s
    for s in (
        # types & null handling
        _spec("str", h_str, 1),
        _spec("int", h_int, 1),
        _spec("float", h_float, 1),
        _spec("bool", h_bool, 1),
        _spec("type_of", h_type_of, 1),
        _spec("is_null", h_is_null, 1),
        _spec("default", h_default, 2),
        _spec("coalesce", h_coalesce, 1, None),
        # strings
        _spec("len", h_len, 1),
        _spec("trim", h_trim, 1),
        _spec("lower", h_lower, 1),
        _spec("upper", h_upper, 1),
        _spec("replace", h_replace, 3),
        _spec("split", h_split, 2),
        _spec("join", h_join, 2),
        _spec("starts_with", h_starts_with, 2),
        _spec("ends_with", h_ends_with, 2),
        _spec("contains", h_contains, 2),
        _spec("pad_left", h_pad_left, 2, 3),
        _spec("pad_right", h_pad_right, 2, 3),
        _spec("slugify", h_slugify, 1),
        # regex
        _spec("regex_match", h_regex_match, 2),
        _spec("regex_find", h_regex_find, 2, 3),
        _spec("regex_find_all", h_regex_find_all, 2),
        _spec("regex_replace", h_regex_replace, 3),
        # numbers
        _spec("round", h_round, 1, 2),
        _spec("abs", h_abs, 1),
        _spec("min", h_min, 1, None),
        _spec("max", h_max, 1, None),
        _spec("sum", h_sum, 1),
        _spec("parse_number", h_parse_number, 1, 2),
        # lists
        _spec("flatten", h_flatten, 1, 2),
        _spec("unique", h_unique, 1),
        _spec("sort", h_sort, 1),
        _spec("sort_by", h_sort_by, 2, lambdas=[1]),
        _spec("map", h_map, 2, lambdas=[1]),
        _spec("filter", h_filter, 2, lambdas=[1]),
        _spec("find", h_find, 2, lambdas=[1]),
        _spec("any", h_any, 1, 2, lambdas=[1]),
        _spec("all", h_all, 1, 2, lambdas=[1]),
        _spec("first", h_first, 1),
        _spec("last", h_last, 1),
        _spec("range", h_range, 2, 3),
        _spec("chunk", h_chunk, 2),
        _spec("zip", h_zip, 2),
        _spec("group_by", h_group_by, 2, lambdas=[1]),
        # objects
        _spec("keys", h_keys, 1),
        _spec("values", h_values, 1),
        _spec("entries", h_entries, 1),
        _spec("merge", h_merge, 1, None),
        _spec("pick", h_pick, 2),
        _spec("omit", h_omit, 2),
        _spec("get", h_get, 2, 3),
        # URLs
        _spec("url_join", h_url_join, 2),
        _spec("url_parse", h_url_parse, 1),
        _spec("url_encode", h_url_encode, 1),
        _spec("url_decode", h_url_decode, 1),
        _spec("query_get", h_query_get, 2),
        # encoding
        _spec("json_parse", h_json_parse, 1),
        _spec("json_dump", h_json_dump, 1),
        _spec("base64_encode", h_base64_encode, 1),
        _spec("base64_decode", h_base64_decode, 1),
        _spec("html_unescape", h_html_unescape, 1),
        _spec("md5", h_md5, 1),
        _spec("sha256", h_sha256, 1),
        # dates
        _spec("now", h_now, 0),
        _spec("parse_date", h_parse_date, 1, 3),
        _spec("format_date", h_format_date, 2, 3),
        # misc
        _spec("uuid", h_uuid, 0),
    )
}

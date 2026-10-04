"""Strict conversions used by expression helpers, interpolation and output checks."""

import json
import math
import re

from crowley.domain.common import TemplatePath
from crowley.domain.errors import ExecutionError, ValidationError
from crowley.domain.values.model import Handle, Value
from crowley.domain.values.types import is_number, type_of

_INT_TEXT = re.compile(r"^[+-]?\d+$")
_TRUE = frozenset({"true", "yes", "1"})
_FALSE = frozenset({"false", "no", "0"})


def _fail(message: str, value: object, hint: str | None = None) -> ExecutionError:
    return ExecutionError("E501", message, value=value, hint=hint)


def to_json_text(value: Value) -> str:
    """Compact JSON for lists and objects (used by interpolation and ``str()``)."""

    def default(obj: object) -> object:
        raise _fail(f"{type_of(obj)} cannot be converted to text", obj)

    if isinstance(value, float) and not math.isfinite(value):
        raise _fail("non-finite number cannot be converted to text", value)
    return json.dumps(value, separators=(",", ":"), ensure_ascii=False, default=default)


def to_str(value: Value) -> str:
    """``str(x)``: strings unchanged, everything else as JSON (``null``, ``true``, ``[1,2]``)."""
    if isinstance(value, str):
        return value
    return to_json_text(value)


def to_int(value: Value) -> int:
    """``int(x)``: ints unchanged, floats truncated toward zero, integer strings parsed."""
    if isinstance(value, bool) or value is None:
        raise _fail(f"cannot convert {type_of(value)} to int", value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise _fail("cannot convert a non-finite number to int", value)
        return int(value)
    if isinstance(value, str) and _INT_TEXT.match(value.strip()):
        return int(value.strip())
    raise _fail(
        f"cannot convert {type_of(value)} {value!r} to int",
        value,
        hint="use parse_number() for text like '1,234' or '12k'",
    )


def to_float(value: Value) -> float:
    """``float(x)``: numbers and numeric strings."""
    if is_number(value):
        return float(value)  # type: ignore[arg-type]
    if isinstance(value, str):
        try:
            return float(value.strip())
        except ValueError:
            pass
    raise _fail(f"cannot convert {type_of(value)} {value!r} to float", value)


def to_bool(value: Value) -> bool:
    """``bool(x)``: booleans, ``0``/``1``, and the strings true/false/yes/no/1/0."""
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in (0, 1):
        return bool(value)
    if isinstance(value, str):
        text = value.strip().lower()
        if text in _TRUE:
            return True
        if text in _FALSE:
            return False
    raise _fail(f"cannot convert {type_of(value)} {value!r} to bool", value)


def interpolate_text(value: Value) -> str:
    """Text inserted for ``${{ }}`` inside a larger string (docs/SPEC.md §10.1).

    ``null`` is an error: use ``default(x, '')`` to make it explicit.
    """
    if value is None:
        raise _fail(
            "cannot interpolate null into a string", value, hint="use default(x, '') if expected"
        )
    return to_str(value)


def assert_output_safe(value: object, path: TemplatePath | None = None) -> None:
    """Raise ``E405`` if ``value`` contains bytes, handles or non-JSON data."""
    _check_output(value, path or TemplatePath.of("output"))


def _check_output(value: object, path: TemplatePath) -> None:
    if value is None or isinstance(value, bool | int | str):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValidationError("E405", "output contains a non-finite number", path=str(path))
        return
    if isinstance(value, list):
        for i, item in enumerate(value):
            _check_output(item, path.index(i))
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValidationError("E405", "output object keys must be strings", path=str(path))
            _check_output(item, path.key(key))
        return
    kind = "handle" if isinstance(value, Handle) else type(value).__name__
    raise ValidationError(
        "E405",
        f"output must be JSON data; found {kind}",
        path=str(path),
        hint="convert bytes and handles to plain values before returning them",
    )

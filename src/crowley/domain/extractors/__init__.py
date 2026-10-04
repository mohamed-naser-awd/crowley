"""Format-agnostic extraction model (docs/SPEC.md §13.8, §13.9). The domain never names HTML."""

from dataclasses import dataclass

from crowley.domain.common import SemVer, TemplatePath, is_identifier
from crowley.domain.errors import ConfigurationError
from crowley.domain.values import Value

FIELD_KEYS = frozenset({"selector", "attr", "all", "required", "default", "fields"})
"""Reserved keys of a field spec; any other key names a query language (e.g. ``xpath:``)."""


@dataclass(frozen=True, slots=True, kw_only=True)
class ExtractorSpec:
    name: str
    version: SemVer
    media_types: tuple[str, ...] = ()
    query_languages: tuple[str, ...] = ()
    default_language: str = ""
    attributes: tuple[str, ...] = ()
    """Readable attributes; ``"*"`` means any attribute name is accepted."""
    default_attribute: str = "text"
    builtin: bool = False

    def __post_init__(self) -> None:
        if not is_identifier(self.name):
            raise ConfigurationError("E903", f"invalid extractor name {self.name!r}")
        if not self.query_languages:
            raise ConfigurationError(
                "E903", f"extractor {self.name!r} must declare query languages"
            )
        if self.default_language not in self.query_languages:
            raise ConfigurationError(
                "E903",
                f"extractor {self.name!r}: default language must be one of its query languages",
            )
        clash = FIELD_KEYS & set(self.query_languages)
        if clash:
            raise ConfigurationError(
                "E903",
                f"extractor {self.name!r}: query language names {sorted(clash)} are reserved",
            )

    def supports_attribute(self, attr: str) -> bool:
        """``attributes`` may list exact names, ``"*"`` (any name) or prefixes like ``"group:"``."""
        if "*" in self.attributes or (attr in self.attributes and not attr.endswith(":")):
            return True
        prefix, sep, rest = attr.partition(":")
        return bool(sep and rest) and f"{prefix}:" in self.attributes


@dataclass(frozen=True, slots=True, kw_only=True)
class Query:
    language: str
    """A query language name, or ``""`` for the extractor's default language (``selector:``)."""
    source: str


@dataclass(frozen=True, slots=True, kw_only=True)
class FieldSpec:
    """One field of ``fields:``: queries tried in order (fallbacks), what to read, nested fields."""

    name: str
    queries: tuple[Query, ...] = ()
    attr: str | None = None
    all: bool = False
    required: bool = False
    default: Value = None
    has_default: bool = False
    fields: tuple["FieldSpec", ...] | None = None
    path: TemplatePath = TemplatePath()


class FieldSpecError(ValueError):
    def __init__(self, message: str, path: TemplatePath) -> None:
        super().__init__(message)
        self.path = path


def parse_fields(raw: Value, path: TemplatePath) -> tuple[FieldSpec, ...]:
    """Parse a literal ``fields:`` mapping. Raises ``FieldSpecError`` with the offending path.

    Language support is not checked here (that needs the extractor; see E326).
    """
    if not isinstance(raw, dict) or not raw:
        raise FieldSpecError(
            "fields must be a non-empty mapping of field names to field specs", path
        )
    return tuple(_parse_field(name, spec, path.key(name)) for name, spec in raw.items())


def _parse_field(name: str, raw: Value, path: TemplatePath) -> FieldSpec:
    if isinstance(raw, str):  # shorthand: `title: "h1"`
        return FieldSpec(name=name, queries=(Query(language="", source=raw),), path=path)
    if not isinstance(raw, dict):
        raise FieldSpecError(f"field {name!r} must be a mapping or a selector string", path)
    query_keys = [k for k in raw if k not in FIELD_KEYS or k == "selector"]
    if len(query_keys) > 1:
        raise FieldSpecError(
            f"field {name!r} has more than one query ({', '.join(query_keys)}); "
            "use a list for fallbacks",
            path,
        )
    queries: tuple[Query, ...] = ()
    if query_keys:
        key = query_keys[0]
        language = "" if key == "selector" else key
        value = raw[key]
        sources = value if isinstance(value, list) else [value]
        if not sources or not all(isinstance(s, str) and s for s in sources):
            raise FieldSpecError(
                f"{key} of field {name!r} must be a non-empty string or list of strings",
                path.key(key),
            )
        queries = tuple(Query(language=language, source=s) for s in sources)  # type: ignore[arg-type]
    attr = raw.get("attr")
    if attr is not None and not (isinstance(attr, str) and attr):
        raise FieldSpecError(f"attr of field {name!r} must be a non-empty string", path.key("attr"))
    for flag in ("all", "required"):
        if flag in raw and not isinstance(raw[flag], bool):
            raise FieldSpecError(f"{flag} of field {name!r} must be true or false", path.key(flag))
    nested = None
    if "fields" in raw:
        nested = parse_fields(raw["fields"], path.key("fields"))
        if attr is not None:
            raise FieldSpecError(f"field {name!r} cannot have both attr and fields", path)
    if raw.get("required") is True and "default" in raw:
        raise FieldSpecError(f"field {name!r} cannot be both required and have a default", path)
    return FieldSpec(
        name=name,
        queries=queries,
        attr=attr,
        all=bool(raw.get("all", False)),
        required=bool(raw.get("required", False)),
        default=raw.get("default"),
        has_default="default" in raw,
        fields=nested,
        path=path,
    )


def iter_fields(fields: tuple[FieldSpec, ...]) -> list[FieldSpec]:
    """All fields, depth-first."""
    out: list[FieldSpec] = []
    for f in fields:
        out.append(f)
        if f.fields:
            out.extend(iter_fields(f.fields))
    return out


__all__ = [
    "FIELD_KEYS",
    "ExtractorSpec",
    "FieldSpec",
    "FieldSpecError",
    "Query",
    "iter_fields",
    "parse_fields",
]

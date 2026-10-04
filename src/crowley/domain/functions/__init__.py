"""Function contract (docs/SPEC.md §11): FunctionSpec, FunctionKind, Requirement and Outcome."""

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum
from types import MappingProxyType
from typing import Any, Literal

from crowley.domain.common import SemVer, SemVerRange, is_function_name
from crowley.domain.errors import ConfigurationError
from crowley.domain.values import Value

LAZY = "x-crowley-lazy"
FROM_PREV = "x-crowley-from-prev"
TARGET = "x-crowley-target"
BINDINGS = "x-crowley-bindings"


class FunctionKind(Enum):
    PLAIN = "plain"
    BLOCK = "block"


def _flagged(properties: Mapping[str, Any], keyword: str) -> frozenset[str]:
    return frozenset(
        name
        for name, schema in properties.items()
        if isinstance(schema, Mapping) and schema.get(keyword) is True
    )


@dataclass(frozen=True, slots=True, kw_only=True)
class FunctionSpec:
    """Everything the validator and runtime need to know about a function (SPEC §11.1).

    Argument markers are read from the input schema's properties:
    ``x-crowley-lazy`` (expression passed unevaluated), ``x-crowley-from-prev``
    (filled from ``prev`` when omitted), ``x-crowley-target`` (an exchange target URL,
    checked against ``permissions.hosts``) and ``x-crowley-bindings`` (names a lazy
    argument's expression may use, e.g. ``["result", "page"]``).
    """

    name: str
    version: SemVer
    kind: FunctionKind = FunctionKind.PLAIN
    input: Mapping[str, Any] = field(default_factory=lambda: {"type": "object"})
    output: Mapping[str, Any] = field(default_factory=dict)
    prev: Mapping[str, Any] | None = None
    description: str = ""
    events: tuple[str, ...] = ()
    pure: bool = False
    """No side effects and no I/O; an unused result is reported as W002."""
    body_bindings: tuple[str, ...] = ()
    """Names a block function adds to its ``do:`` body (e.g. ``page``)."""
    adapter: str | None = None
    """For adapter-provided functions: the adapter whose exchanges this function performs."""
    extractor: str | None = None
    """For extractor-generated functions (``<name>.extract`` …): the extractor they use."""
    builtin: bool = False

    def __post_init__(self) -> None:
        if not is_function_name(self.name):
            raise ConfigurationError("E903", f"invalid function name {self.name!r}")
        if self.input.get("type") != "object":
            raise ConfigurationError("E903", f"{self.name}: input schema must have type 'object'")
        if "prev" in self.properties:
            raise ConfigurationError(
                "E903",
                f"{self.name}: 'prev' is reserved and cannot be declared as an argument",
                hint="declare a 'prev' schema on the function instead",
            )
        for prop, schema in self.properties.items():
            bindings = schema.get(BINDINGS) if isinstance(schema, Mapping) else None
            if bindings is not None and (
                not isinstance(bindings, list) or not all(isinstance(b, str) for b in bindings)
            ):
                raise ConfigurationError(
                    "E903", f"{self.name}: {BINDINGS} of {prop!r} must be a list of names"
                )
        if self.body_bindings and self.kind is not FunctionKind.BLOCK:
            raise ConfigurationError(
                "E903", f"{self.name}: only block functions can declare body bindings"
            )

    @property
    def namespace(self) -> str:
        return self.name.partition(".")[0]

    @property
    def properties(self) -> Mapping[str, Any]:
        props = self.input.get("properties", {})
        return props if isinstance(props, Mapping) else {}

    @property
    def required_args(self) -> frozenset[str]:
        return frozenset(self.input.get("required", ()))

    @property
    def lazy_args(self) -> frozenset[str]:
        return _flagged(self.properties, LAZY)

    @property
    def from_prev_args(self) -> frozenset[str]:
        return _flagged(self.properties, FROM_PREV)

    @property
    def target_args(self) -> frozenset[str]:
        return _flagged(self.properties, TARGET)

    @property
    def arg_bindings(self) -> Mapping[str, tuple[str, ...]]:
        """Lazy argument → names its expression may use."""
        return MappingProxyType(
            {
                name: tuple(schema.get(BINDINGS, ()))
                for name, schema in self.properties.items()
                if isinstance(schema, Mapping) and schema.get(LAZY) is True
            }
        )


# ── requirements (SPEC §11.6) ─────────────────────────────────────────────────

RequirementKind = Literal["function", "adapter", "extractor"]
_REQUIREMENT = re.compile(
    r"^(?:(adapter|extractor):)?([a-z_][a-z0-9_]*(?:\.(?:[a-z_][a-z0-9_]*|\*))?)(?:@(.+))?$"
)


@dataclass(frozen=True, slots=True, kw_only=True)
class Requirement:
    """An entry of ``requires``: ``mycorp.fn@^1``, ``mycorp.*@^2``, ``adapter:ws@^1``."""

    kind: RequirementKind
    name: str
    version: SemVerRange | None = None

    @classmethod
    def parse(cls, text: str) -> "Requirement":
        m = _REQUIREMENT.match(text.strip())
        if not m:
            raise ValueError(
                f"invalid requirement {text!r}; expected 'ns.fn@range', 'ns.*@range', "
                "'adapter:name@range' or 'extractor:name@range'"
            )
        prefix, name, version = m.groups()
        kind: RequirementKind = prefix or "function"  # type: ignore[assignment]
        if kind == "function" and "." not in name:
            raise ValueError(
                f"invalid requirement {text!r}; functions are written 'namespace.name'"
            )
        if kind != "function" and "." in name:
            raise ValueError(f"invalid requirement {text!r}; {kind} names have no namespace")
        try:
            rng = SemVerRange.parse(version) if version else None
        except ValueError as exc:
            raise ValueError(f"invalid requirement {text!r}: {exc}") from None
        return cls(kind=kind, name=name, version=rng)

    @property
    def is_wildcard(self) -> bool:
        return self.name.endswith(".*")

    def matches_name(self, name: str) -> bool:
        if self.is_wildcard:
            return name.partition(".")[0] == self.name[:-2]
        return name == self.name

    def __str__(self) -> str:
        prefix = "" if self.kind == "function" else f"{self.kind}:"
        return f"{prefix}{self.name}" + (f"@{self.version}" if self.version else "")


# ── block-body outcomes (SPEC §11.2) ──────────────────────────────────────────


@dataclass(frozen=True, slots=True, kw_only=True)
class Outcome:
    """Result of running a block function's ``do:`` body once."""

    value: Value = None
    has_value: bool = True
    is_break: bool = False

    @property
    def is_continue(self) -> bool:
        return not self.has_value and not self.is_break


__all__ = [
    "BINDINGS",
    "FROM_PREV",
    "LAZY",
    "TARGET",
    "FunctionKind",
    "FunctionSpec",
    "Outcome",
    "Requirement",
    "RequirementKind",
]

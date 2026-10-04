"""Ports: what the application needs from outside, implemented by infrastructure providers."""

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol

from crowley.domain.common import PathPart, TemplatePath
from crowley.domain.errors import SourceLocation
from crowley.domain.values import Value

# ── template sources ──────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True, kw_only=True)
class Origin:
    """Where a template came from. ``file`` is an absolute path for file-based templates."""

    name: str
    file: str | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class TemplateText:
    text: str
    origin: Origin


class TemplateSource(Protocol):
    def can_resolve(self, ref: str) -> bool: ...

    async def resolve(self, ref: str, *, relative_to: Origin | None = None) -> TemplateText:
        """Return the template text. Raises ``E104`` if it cannot be found or read."""
        ...


# ── parsing ───────────────────────────────────────────────────────────────────


@dataclass(slots=True)
class PositionMap:
    """Source locations of every mapping key and value in a parsed document."""

    file: str | None = None
    values: dict[tuple[PathPart, ...], tuple[int, int]] = field(default_factory=dict)
    keys: dict[tuple[PathPart, ...], tuple[int, int]] = field(default_factory=dict)

    def _loc(self, line_col: tuple[int, int]) -> SourceLocation:
        return SourceLocation(file=self.file, line=line_col[0], column=line_col[1])

    def value_at(self, path: TemplatePath) -> SourceLocation | None:
        """Location of the value at ``path``, or of its nearest ancestor."""
        parts = path.parts
        while True:
            if parts in self.values:
                return self._loc(self.values[parts])
            if not parts:
                return None
            parts = parts[:-1]

    def key_at(self, path: TemplatePath) -> SourceLocation | None:
        """Location of the mapping key that ends ``path`` (falls back to the value location)."""
        if path.parts in self.keys:
            return self._loc(self.keys[path.parts])
        return self.value_at(path)


@dataclass(frozen=True, slots=True, kw_only=True)
class RawDocument:
    """A parsed template: plain JSON-compatible data plus source positions."""

    data: Value
    positions: PositionMap
    origin: Origin


class TemplateParser(Protocol):
    def parse(self, text: str, origin: Origin) -> RawDocument:
        """Parse text into plain data. Raises ``E101``/``E102``, and ``E205`` for non-plain data."""
        ...


# ── JSON Schema ───────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True, kw_only=True)
class SchemaViolation:
    """One schema problem. ``path`` points into the validated instance (or the schema itself)."""

    path: TemplatePath
    message: str
    keyword: str
    """The failing keyword, e.g. ``required``, ``additionalProperties``, ``type``, ``$ref``."""


class CompiledSchema(Protocol):
    def validate(self, instance: Any) -> list[SchemaViolation]: ...


class SchemaValidator(Protocol):
    def check_schema(
        self, schema: Mapping[str, Any] | bool, *, definitions: Mapping[str, Any] | None = None
    ) -> list[SchemaViolation]:
        """Problems with the schema itself (bad keywords, unknown ``#/schemas/<name>`` refs)."""
        ...

    def compile(
        self, schema: Mapping[str, Any] | bool, *, definitions: Mapping[str, Any] | None = None
    ) -> CompiledSchema:
        """``definitions``: the template's ``schemas:`` table (``$ref: "#/schemas/<name>"``)."""
        ...


__all__ = [
    "CompiledSchema",
    "Origin",
    "PositionMap",
    "RawDocument",
    "SchemaValidator",
    "SchemaViolation",
    "TemplateParser",
    "TemplateSource",
    "TemplateText",
]

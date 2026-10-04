"""BaseExtractor: the public base class for content-format plugins (docs/SPEC.md §13.8).

An extractor implements three methods: ``parse`` (raw content → node), ``select`` (node + query
→ nodes) and ``read`` (node + attribute → value). Everything else is shared: the field engine,
the generated ``<name>.*`` functions, fallbacks, validation and events. Nodes are whatever the
extractor likes; templates see them as opaque ``handle<<name>.node>`` values.
"""

from typing import Any, ClassVar

from crowley.domain.common import SemVer
from crowley.domain.errors import ValidationError
from crowley.domain.extractors import ExtractorSpec
from crowley.domain.values import Value, type_of


class BaseExtractor:
    """Subclass to add a content format: set the class attributes, implement the three methods."""

    name: ClassVar[str]
    version: ClassVar[str]
    media_types: ClassVar[tuple[str, ...]] = ()
    query_languages: ClassVar[tuple[str, ...]] = ()
    default_language: ClassVar[str] = ""
    attributes: ClassVar[tuple[str, ...]] = ("text",)
    default_attribute: ClassVar[str] = "text"
    builtin: ClassVar[bool] = False

    @property
    def spec(self) -> ExtractorSpec:
        return ExtractorSpec(
            name=self.name,
            version=SemVer.parse(self.version),
            media_types=self.media_types,
            query_languages=self.query_languages,
            default_language=self.default_language,
            attributes=self.attributes,
            default_attribute=self.default_attribute,
            builtin=self.builtin,
        )

    @property
    def node_type(self) -> str:
        """The handle type of this extractor's nodes, e.g. ``html.node``."""
        return f"{self.name}.node"

    def check_query(self, language: str, source: str) -> str | None:
        """An error message if ``source`` is not a valid ``language`` query, else ``None``."""
        return None

    # ── the contract ───────────────────────────────────────────────────────────

    def parse(self, raw: str | bytes, *, base_url: str | None, media_type: str | None) -> Any:
        """Parse raw content into a root node."""
        raise NotImplementedError(f"extractor {self.name!r} does not implement parse()")

    def select(self, node: Any, query: str, language: str) -> list[Any]:
        """Nodes matching ``query`` (in ``language``) under ``node``, in document order."""
        raise NotImplementedError(f"extractor {self.name!r} does not implement select()")

    def read(self, node: Any, attr: str) -> Value:
        """The value of attribute ``attr`` of ``node`` (``None`` if it has none)."""
        raise NotImplementedError(f"extractor {self.name!r} does not implement read()")

    # ── hooks with defaults ────────────────────────────────────────────────────

    def load(self, value: Value, *, base_url: str | None, media_type: str | None) -> Any:
        """Turn a template value into a root node. Default: parse strings and bytes."""
        if isinstance(value, str | bytes):
            return self.parse(value, base_url=base_url, media_type=media_type)
        raise ValidationError(
            "E403",
            f"{self.name} can't read a {type_of(value)}; pass a string, bytes, a response with "
            f"a body, or a {self.node_type} handle",
        )

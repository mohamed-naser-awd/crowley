"""The field extraction engine shared by every extractor (ARCHITECTURE §4.6, SPEC §13.9)."""

from collections.abc import Awaitable, Callable, Mapping, Sequence
from typing import Any

from crowley.application.extractors.base import BaseExtractor
from crowley.domain.adapters import media_type_essence, media_type_rank
from crowley.domain.common import TemplatePath
from crowley.domain.errors import CrowleyError, ExecutionError, ValidationError
from crowley.domain.extractors import FieldSpec, FieldSpecError, Query, parse_fields
from crowley.domain.values import Handle, Value

FallbackHook = Callable[[str, int, Query], Awaitable[None]]
"""Called when a fallback query (not the first) matched: field name, index, query."""


async def _no_fallback(field: str, index: int, query: Query) -> None:
    return None


class ExtractionEngine:
    """Walks field specs and calls only ``parse``/``select``/``read`` of one extractor."""

    def __init__(
        self, extractor: BaseExtractor, *, on_fallback: FallbackHook = _no_fallback
    ) -> None:
        self.extractor = extractor
        self._on_fallback = on_fallback

    # ── sources and nodes ──────────────────────────────────────────────────────

    def root(self, source: Value) -> Any:
        """Resolve ``source`` (SPEC §13.9) to a root node."""
        extractor = self.extractor
        if isinstance(source, Handle):
            if source.type_name != extractor.node_type:
                raise ValidationError(
                    "E403", f"{extractor.name} can't read a {source.type_name} handle"
                )
            return source.payload
        if isinstance(source, dict) and "body" in source:
            url = source.get("url")
            media = source.get("media_type")
            return self._call(
                "parse",
                extractor.load,
                source["body"],
                base_url=url if isinstance(url, str) else None,
                media_type=media if isinstance(media, str) else None,
            )
        return self._call("parse", extractor.load, source, base_url=None, media_type=None)

    def handle(self, node: Any) -> Handle:
        return Handle(self.extractor.node_type, node)

    def select_nodes(self, node: Any, query: Query) -> list[Any]:
        language = query.language or self.extractor.default_language
        if language not in self.extractor.query_languages:
            raise ValidationError(
                "E403",
                f"{self.extractor.name} does not support {language!r} queries "
                f"(supported: {', '.join(self.extractor.query_languages)})",
            )
        found = self._call("select", self.extractor.select, node, query.source, language)
        return list(found)

    def read(self, node: Any, attr: str | None) -> Value:
        name = attr or self.extractor.default_attribute
        if not self.extractor.spec.supports_attribute(name):
            raise ValidationError("E403", f"{self.extractor.name} can't read attribute {name!r}")
        value: Value = self._call("read", self.extractor.read, node, name)
        return value

    # ── functions ──────────────────────────────────────────────────────────────

    def select(self, source: Value, query: Query, attr: str | None, *, every: bool) -> Value:
        """``<name>.select`` (first match or null) and ``<name>.select_all`` (list)."""
        nodes = self.select_nodes(self.root(source), query)
        if every:
            return [self.read(node, attr) for node in nodes]
        return self.read(nodes[0], attr) if nodes else None

    async def extract(
        self, source: Value, fields: Value, root: Value = None, *, path: str | None = None
    ) -> Value:
        """``<name>.extract``: one object, or one object per ``root`` match."""
        specs = self._fields(fields, path)
        node = self.root(source)
        if root is None:
            return await self._object(node, specs)
        queries = _queries(root)
        matches: list[Any] = []
        for query in queries:
            matches = self.select_nodes(node, query)
            if matches:
                break
        return [await self._object(match, specs) for match in matches]

    def _fields(self, fields: Value, path: str | None) -> tuple[FieldSpec, ...]:
        try:
            return parse_fields(fields, TemplatePath.of("fields"))
        except FieldSpecError as exc:
            raise ValidationError("E403", f"invalid fields: {exc}", path=path) from None

    async def _object(self, node: Any, fields: Sequence[FieldSpec]) -> dict[str, Value]:
        return {field.name: await self._field(node, field) for field in fields}

    async def _field(self, node: Any, field: FieldSpec) -> Value:
        matches = [node] if not field.queries else []
        for index, query in enumerate(field.queries):
            matches = self.select_nodes(node, query)
            if matches:
                if index > 0:
                    await self._on_fallback(field.name, index, query)
                break
        if not matches:
            if field.required:
                tried = ", ".join(repr(q.source) for q in field.queries)
                raise ExecutionError(
                    "E502",
                    f"required field {field.name!r} matched nothing ({tried})",
                    hint="the page layout may have changed; add a fallback selector",
                )
            if field.has_default:
                return field.default
            return [] if field.all else None
        targets = matches if field.all else matches[:1]
        values: list[Value] = []
        for match in targets:
            if field.fields:
                values.append(await self._object(match, field.fields))
            else:
                values.append(self.read(match, field.attr))
        return values if field.all else values[0]

    def _call(self, label: str, method: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        try:
            return method(*args, **kwargs)
        except CrowleyError:
            raise
        except Exception as exc:  # extractor failures surface as function errors
            raise ExecutionError(
                "E502",
                f"{self.extractor.name}.{label} failed: {type(exc).__name__}: {exc}",
                cause=exc,
            ) from exc


def _queries(value: Value) -> list[Query]:
    sources = value if isinstance(value, list) else [value]
    queries: list[Query] = []
    for source in sources:
        if not isinstance(source, str) or not source:
            raise ValidationError("E403", "root must be a query string or a list of them")
        queries.append(Query(language="", source=source))
    return queries


def make_query(selector: str, language: str | None) -> Query:
    return Query(language=language or "", source=selector)


def route(extractors: Mapping[str, BaseExtractor], media_type: str | None) -> BaseExtractor | None:
    """Pick the extractor for ``media_type`` (SPEC §13.10): exact, then ``+suffix``/``type/*``,
    then wildcard. Ties keep registration order."""
    essence = media_type_essence(media_type)
    if essence is None:
        return None
    best: tuple[int, BaseExtractor] | None = None
    for extractor in extractors.values():
        rank = max((media_type_rank(p, essence) for p in extractor.media_types), default=0)
        if rank and (best is None or rank > best[0]):
            best = (rank, extractor)
    return best[1] if best else None

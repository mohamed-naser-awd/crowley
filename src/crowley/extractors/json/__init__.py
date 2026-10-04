"""Built-in ``json`` extractor (python-jsonpath): jsonpath queries (docs/SPEC.md §13.11).

Nodes are plain JSON values, so an already-parsed response body is used as-is.
"""

import json
from typing import Any, ClassVar

import jsonpath

from crowley.application.extractors import BaseExtractor
from crowley.domain.values import Value


class JsonExtractor(BaseExtractor):
    name: ClassVar[str] = "json"
    version: ClassVar[str] = "1.0.0"
    media_types: ClassVar[tuple[str, ...]] = ("application/json", "*+json")
    query_languages: ClassVar[tuple[str, ...]] = ("jsonpath",)
    default_language: ClassVar[str] = "jsonpath"
    attributes: ClassVar[tuple[str, ...]] = ("value", "keys", "length")
    default_attribute: ClassVar[str] = "value"
    builtin: ClassVar[bool] = True

    def __init__(self) -> None:
        self._compiled: dict[str, Any] = {}

    def check_query(self, language: str, source: str) -> str | None:
        try:
            jsonpath.compile(source)
        except jsonpath.JSONPathError as exc:
            return f"invalid JSONPath {source!r}: {exc}"
        return None

    def load(self, value: Value, *, base_url: str | None, media_type: str | None) -> Any:
        if isinstance(value, str | bytes):
            return self.parse(value, base_url=base_url, media_type=media_type)
        return value  # already JSON data (e.g. a parsed response body)

    def parse(self, raw: str | bytes, *, base_url: str | None, media_type: str | None) -> Any:
        return json.loads(raw)

    def select(self, node: Any, query: str, language: str) -> list[Any]:
        compiled = self._compiled.get(query)
        if compiled is None:
            compiled = self._compiled[query] = jsonpath.compile(query)
        if not isinstance(node, dict | list):
            return []
        return list(compiled.findall(node))

    def read(self, node: Any, attr: str) -> Value:
        if attr == "keys":
            return list(node) if isinstance(node, dict) else []
        if attr == "length":
            return len(node) if isinstance(node, dict | list | str) else None
        result: Value = node
        return result


__all__ = ["JsonExtractor"]

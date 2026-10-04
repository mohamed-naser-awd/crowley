"""Built-in ``json`` extractor (python-jsonpath): jsonpath queries (docs/SPEC.md §13.11).

M1 declares the extractor and validates queries; selection arrives in M3.
"""

from typing import ClassVar

import jsonpath

from crowley.application.extractors import BaseExtractor


class JsonExtractor(BaseExtractor):
    name: ClassVar[str] = "json"
    version: ClassVar[str] = "1.0.0"
    media_types: ClassVar[tuple[str, ...]] = ("application/json", "*+json")
    query_languages: ClassVar[tuple[str, ...]] = ("jsonpath",)
    default_language: ClassVar[str] = "jsonpath"
    attributes: ClassVar[tuple[str, ...]] = ("value", "keys", "length")
    default_attribute: ClassVar[str] = "value"
    builtin: ClassVar[bool] = True

    def check_query(self, language: str, source: str) -> str | None:
        try:
            jsonpath.compile(source)
        except jsonpath.JSONPathError as exc:
            return f"invalid JSONPath {source!r}: {exc}"
        return None


__all__ = ["JsonExtractor"]

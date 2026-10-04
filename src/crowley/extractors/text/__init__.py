"""Built-in ``text`` extractor (regex): regex queries; matches are nodes (docs/SPEC.md §13.11).

M1 declares the extractor and validates queries; matching arrives in M3.
"""

from typing import ClassVar

import regex

from crowley.application.extractors import BaseExtractor


class TextExtractor(BaseExtractor):
    name: ClassVar[str] = "text"
    version: ClassVar[str] = "1.0.0"
    media_types: ClassVar[tuple[str, ...]] = ("text/plain", "*")
    query_languages: ClassVar[tuple[str, ...]] = ("regex",)
    default_language: ClassVar[str] = "regex"
    attributes: ClassVar[tuple[str, ...]] = ("text", "group:")
    default_attribute: ClassVar[str] = "text"
    builtin: ClassVar[bool] = True

    def check_query(self, language: str, source: str) -> str | None:
        try:
            regex.compile(source)
        except regex.error as exc:
            return f"invalid regex {source!r}: {exc}"
        return None


__all__ = ["TextExtractor"]

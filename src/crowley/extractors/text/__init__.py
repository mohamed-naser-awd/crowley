"""Built-in ``text`` extractor (regex): regex queries; matches are nodes (docs/SPEC.md §13.11)."""

from dataclasses import dataclass
from typing import Any, ClassVar

import regex

from crowley.application.extractors import BaseExtractor
from crowley.domain.values import Value

MATCH_TIMEOUT_SECONDS = 1.0


@dataclass(frozen=True, slots=True)
class TextNode:
    """The whole text (``match is None``) or one regex match within it."""

    text: str
    match: Any = None

    @property
    def value(self) -> str:
        return str(self.match.group(0)) if self.match is not None else self.text


class TextExtractor(BaseExtractor):
    name: ClassVar[str] = "text"
    version: ClassVar[str] = "1.0.0"
    media_types: ClassVar[tuple[str, ...]] = ("text/plain", "*")
    query_languages: ClassVar[tuple[str, ...]] = ("regex",)
    default_language: ClassVar[str] = "regex"
    attributes: ClassVar[tuple[str, ...]] = ("text", "group:")
    default_attribute: ClassVar[str] = "text"
    builtin: ClassVar[bool] = True

    def __init__(self) -> None:
        self._compiled: dict[str, Any] = {}

    def check_query(self, language: str, source: str) -> str | None:
        try:
            regex.compile(source)
        except regex.error as exc:
            return f"invalid regex {source!r}: {exc}"
        return None

    def parse(self, raw: str | bytes, *, base_url: str | None, media_type: str | None) -> Any:
        text = raw.decode("utf-8", errors="replace") if isinstance(raw, bytes) else raw
        return TextNode(text)

    def select(self, node: Any, query: str, language: str) -> list[Any]:
        if not isinstance(node, TextNode):
            return []
        pattern = self._compiled.get(query)
        if pattern is None:
            pattern = self._compiled[query] = regex.compile(query)
        base = node.value
        return [
            TextNode(base, match) for match in pattern.finditer(base, timeout=MATCH_TIMEOUT_SECONDS)
        ]

    def read(self, node: Any, attr: str) -> Value:
        if not isinstance(node, TextNode):
            return None
        if attr == "text":
            return node.value
        key: int | str = attr.removeprefix("group:")
        if node.match is None:
            return None
        if isinstance(key, str) and key.isdigit():
            key = int(key)
        try:
            group = node.match.group(key)
        except (IndexError, KeyError):
            return None
        return None if group is None else str(group)


__all__ = ["TextExtractor", "TextNode"]

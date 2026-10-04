"""Built-in ``html`` extractor (lxml + cssselect): css and xpath queries (docs/SPEC.md §13.11).

M1 declares the extractor and validates queries; parsing and selection arrive in M3.
"""

from typing import ClassVar

from cssselect import GenericTranslator, SelectorError
from lxml import etree

from crowley.application.extractors import BaseExtractor


def check_css(source: str) -> str | None:
    try:
        GenericTranslator().css_to_xpath(source)
    except SelectorError as exc:
        return f"invalid CSS selector {source!r}: {exc}"
    return None


def check_xpath(source: str) -> str | None:
    try:
        etree.XPath(source)
    except etree.XPathSyntaxError as exc:
        return f"invalid XPath {source!r}: {exc}"
    return None


class HtmlExtractor(BaseExtractor):
    name: ClassVar[str] = "html"
    version: ClassVar[str] = "1.0.0"
    media_types: ClassVar[tuple[str, ...]] = ("text/html", "application/xhtml+xml")
    query_languages: ClassVar[tuple[str, ...]] = ("css", "xpath")
    default_language: ClassVar[str] = "css"
    attributes: ClassVar[tuple[str, ...]] = ("*",)
    default_attribute: ClassVar[str] = "text"
    builtin: ClassVar[bool] = True

    def check_query(self, language: str, source: str) -> str | None:
        return check_css(source) if language == "css" else check_xpath(source)


__all__ = ["HtmlExtractor"]

"""Built-in ``xml`` extractor (lxml): xpath and css queries (docs/SPEC.md §13.11).

M1 declares the extractor and validates queries; parsing and selection arrive in M3.
"""

from typing import ClassVar

from cssselect import GenericTranslator, SelectorError
from lxml import etree

from crowley.application.extractors import BaseExtractor


class XmlExtractor(BaseExtractor):
    name: ClassVar[str] = "xml"
    version: ClassVar[str] = "1.0.0"
    media_types: ClassVar[tuple[str, ...]] = ("application/xml", "text/xml", "*+xml")
    query_languages: ClassVar[tuple[str, ...]] = ("xpath", "css")
    default_language: ClassVar[str] = "xpath"
    attributes: ClassVar[tuple[str, ...]] = ("*",)
    default_attribute: ClassVar[str] = "text"
    builtin: ClassVar[bool] = True

    def check_query(self, language: str, source: str) -> str | None:
        try:
            if language == "css":
                GenericTranslator().css_to_xpath(source)
            else:
                etree.XPath(source)
        except (SelectorError, etree.XPathSyntaxError) as exc:
            return f"invalid {language} query {source!r}: {exc}"
        return None


__all__ = ["XmlExtractor"]

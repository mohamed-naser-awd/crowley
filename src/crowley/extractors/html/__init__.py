"""Built-in ``html`` extractor (lxml + cssselect): css and xpath queries (docs/SPEC.md §13.11)."""

import html as html_lib
from typing import Any, ClassVar

import lxml.html
from cssselect import GenericTranslator, SelectorError
from lxml import etree
from lxml.cssselect import CSSSelector

from crowley.application.extractors import BaseExtractor
from crowley.domain.values import Value


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


def xpath_results(result: Any) -> list[Any]:
    """XPath can return nodes, strings, numbers or booleans; selections are always lists."""
    if isinstance(result, list):
        return [str(item) if isinstance(item, str) else item for item in result]
    return [result]


class HtmlExtractor(BaseExtractor):
    name: ClassVar[str] = "html"
    version: ClassVar[str] = "1.0.0"
    media_types: ClassVar[tuple[str, ...]] = ("text/html", "application/xhtml+xml")
    query_languages: ClassVar[tuple[str, ...]] = ("css", "xpath")
    default_language: ClassVar[str] = "css"
    attributes: ClassVar[tuple[str, ...]] = ("text", "raw_text", "html", "inner_html", "*")
    default_attribute: ClassVar[str] = "text"
    builtin: ClassVar[bool] = True

    def __init__(self) -> None:
        self._css: dict[str, CSSSelector] = {}
        self._xpath: dict[str, etree.XPath] = {}

    def check_query(self, language: str, source: str) -> str | None:
        return check_css(source) if language == "css" else check_xpath(source)

    def parse(self, raw: str | bytes, *, base_url: str | None, media_type: str | None) -> Any:
        if not raw.strip():
            raw = "<html></html>"
        document: Any
        try:
            document = lxml.html.document_fromstring(raw)
        except ValueError:  # str with an XML encoding declaration
            document = lxml.html.document_fromstring(
                raw.encode("utf-8") if isinstance(raw, str) else raw
            )
        if base_url:
            document.make_links_absolute(base_url, resolve_base_href=True, handle_failures="ignore")
        return document

    def select(self, node: Any, query: str, language: str) -> list[Any]:
        if not isinstance(node, etree._Element):
            return []
        if language == "css":
            selector = self._css.get(query)
            if selector is None:
                selector = self._css[query] = CSSSelector(query, translator="html")
            matches: Any = selector(node)
            return list(matches)
        compiled = self._xpath.get(query)
        if compiled is None:
            compiled = self._xpath[query] = etree.XPath(query)
        result: Any = compiled(node)
        return xpath_results(result)

    def read(self, node: Any, attr: str) -> Value:
        if not isinstance(node, etree._Element):
            if attr in ("text", "raw_text"):
                text = str(node)
                return " ".join(text.split()) if attr == "text" else text
            return None
        if attr in ("text", "raw_text"):
            text = "".join(str(part) for part in node.itertext())
            return " ".join(text.split()) if attr == "text" else text
        if attr == "html":
            return etree.tostring(node, encoding="unicode", method="html", with_tail=False)
        if attr == "inner_html":
            inner = html_lib.escape(node.text or "", quote=False)
            return inner + "".join(
                etree.tostring(child, encoding="unicode", method="html", with_tail=True)
                for child in node
            )
        value = node.get(attr)
        return None if value is None else str(value)


__all__ = ["HtmlExtractor", "check_css", "check_xpath", "xpath_results"]

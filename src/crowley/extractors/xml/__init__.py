"""Built-in ``xml`` extractor (lxml): xpath and css queries (docs/SPEC.md §13.11).

Parsing never resolves entities or touches the network (no XXE).
"""

from typing import Any, ClassVar

from cssselect import GenericTranslator, SelectorError
from lxml import etree

from crowley.application.extractors import BaseExtractor
from crowley.domain.values import Value

_PARSER = etree.XMLParser(resolve_entities=False, no_network=True, load_dtd=False)


class XmlExtractor(BaseExtractor):
    name: ClassVar[str] = "xml"
    version: ClassVar[str] = "1.0.0"
    media_types: ClassVar[tuple[str, ...]] = ("application/xml", "text/xml", "*+xml")
    query_languages: ClassVar[tuple[str, ...]] = ("xpath", "css")
    default_language: ClassVar[str] = "xpath"
    attributes: ClassVar[tuple[str, ...]] = ("text", "raw_text", "xml", "*")
    default_attribute: ClassVar[str] = "text"
    builtin: ClassVar[bool] = True

    def __init__(self) -> None:
        self._css: dict[str, str] = {}

    def check_query(self, language: str, source: str) -> str | None:
        try:
            if language == "css":
                GenericTranslator().css_to_xpath(source)
            else:
                etree.XPath(source)
        except (SelectorError, etree.XPathSyntaxError) as exc:
            return f"invalid {language} query {source!r}: {exc}"
        return None

    def parse(self, raw: str | bytes, *, base_url: str | None, media_type: str | None) -> Any:
        data = raw.encode("utf-8") if isinstance(raw, str) else raw
        if isinstance(raw, str) and raw.lstrip().startswith("<?xml"):
            # the declaration may name another encoding; parse the text as text instead
            declaration_end = raw.find("?>")
            data = raw[declaration_end + 2 :].encode("utf-8")
        return etree.fromstring(data, _PARSER)

    def select(self, node: Any, query: str, language: str) -> list[Any]:
        if not isinstance(node, etree._Element):
            return []
        if language == "css":
            xpath = self._css.get(query)
            if xpath is None:
                xpath = self._css[query] = GenericTranslator().css_to_xpath(query)
            query = xpath
        namespaces = {k: v for k, v in node.nsmap.items() if k}
        result = node.xpath(query, namespaces=namespaces)
        if isinstance(result, list):
            return [str(item) if isinstance(item, str) else item for item in result]
        return [result]

    def read(self, node: Any, attr: str) -> Value:
        if not isinstance(node, etree._Element):
            if attr in ("text", "raw_text"):
                text = str(node)
                return " ".join(text.split()) if attr == "text" else text
            return None
        if attr == "text":
            return " ".join("".join(str(t) for t in node.itertext()).split())
        if attr == "raw_text":
            return "".join(str(t) for t in node.itertext())
        if attr == "xml":
            return etree.tostring(node, encoding="unicode", with_tail=False)
        value = node.get(attr)
        return None if value is None else str(value)


__all__ = ["XmlExtractor"]

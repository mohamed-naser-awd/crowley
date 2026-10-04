import pytest

from crowley.application.extractors import BaseExtractor
from crowley.extractors.html import HtmlExtractor
from crowley.extractors.json import JsonExtractor
from crowley.extractors.text import TextExtractor
from crowley.extractors.xml import XmlExtractor


@pytest.mark.parametrize(
    ("extractor", "language", "good", "bad"),
    [
        (HtmlExtractor(), "css", "div > a.title[href^='/']", "div >"),
        (HtmlExtractor(), "xpath", "//span[@itemprop='price']/@content", "//span["),
        (XmlExtractor(), "xpath", "/feed/entry/title", "/feed["),
        (XmlExtractor(), "css", "entry > title", "entry >"),
        (JsonExtractor(), "jsonpath", "$.data.items[*].id", "$.data["),
        (TextExtractor(), "regex", r"price: (?P<n>\d+)", "(unclosed"),
    ],
)
def test_check_query(extractor: BaseExtractor, language: str, good: str, bad: str) -> None:
    assert extractor.check_query(language, good) is None
    problem = extractor.check_query(language, bad)
    assert problem is not None
    assert bad in problem


@pytest.mark.parametrize(
    ("extractor", "attr", "supported"),
    [
        (HtmlExtractor(), "data-id", True),
        (JsonExtractor(), "value", True),
        (JsonExtractor(), "href", False),
        (TextExtractor(), "group:1", True),
        (TextExtractor(), "group:name", True),
        (TextExtractor(), "href", False),
    ],
)
def test_attributes(extractor: BaseExtractor, attr: str, supported: bool) -> None:
    assert extractor.spec.supports_attribute(attr) is supported


def test_specs_are_valid_and_builtin() -> None:
    for extractor in (HtmlExtractor(), XmlExtractor(), JsonExtractor(), TextExtractor()):
        spec = extractor.spec
        assert spec.builtin
        assert spec.default_language in spec.query_languages

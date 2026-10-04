import pytest

from crowley.extractors.html import HtmlExtractor
from crowley.extractors.json import JsonExtractor
from crowley.extractors.text import TextExtractor
from crowley.extractors.xml import XmlExtractor
from tests.extractors.contract import ContractCase, check_contract

CASES = [
    ContractCase(
        extractor=HtmlExtractor(),
        document="<ul><li class='a'> One </li><li class='a'>Two</li></ul>",
        language="css",
        query="li.a",
        attr="text",
        expected=["One", "Two"],
        bad_query="li[",
    ),
    ContractCase(
        extractor=HtmlExtractor(),
        document="<p><a href='/x'>x</a><a href='/y'>y</a></p>",
        language="xpath",
        query="//a",
        attr="href",
        expected=["/x", "/y"],
        bad_query="//a[",
    ),
    ContractCase(
        extractor=XmlExtractor(),
        document="<feed><entry id='1'>A</entry><entry id='2'>B</entry></feed>",
        language="xpath",
        query="/feed/entry",
        attr="id",
        expected=["1", "2"],
        bad_query="/feed[",
    ),
    ContractCase(
        extractor=XmlExtractor(),
        document=b"<feed><entry>A</entry><entry>B</entry></feed>",
        language="css",
        query="entry",
        attr="text",
        expected=["A", "B"],
        bad_query="entry >",
    ),
    ContractCase(
        extractor=JsonExtractor(),
        document='{"items": [{"id": 1}, {"id": 2}]}',
        language="jsonpath",
        query="$.items[*]",
        attr="keys",
        expected=[["id"], ["id"]],
        bad_query="$.items[",
    ),
    ContractCase(
        extractor=TextExtractor(),
        document="price: 10 EUR, price: 25 EUR",
        language="regex",
        query=r"price: (?P<amount>\d+)",
        attr="group:amount",
        expected=["10", "25"],
        bad_query="(unclosed",
    ),
]


@pytest.mark.parametrize("case", CASES, ids=[f"{c.extractor.name}-{c.language}" for c in CASES])
def test_contract(case: ContractCase) -> None:
    check_contract(case)

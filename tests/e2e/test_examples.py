"""Every shipped example template validates without errors or warnings."""

from pathlib import Path

import pytest

from crowley import Crowley

EXAMPLES = sorted((Path(__file__).parents[2] / "examples").glob("*/*.yml"))


@pytest.mark.parametrize("path", EXAMPLES, ids=[p.parent.name for p in EXAMPLES])
def test_example_validates(path: Path) -> None:
    report = Crowley().validate(str(path))
    assert report.diagnostics == (), [str(d) for d in report.diagnostics]


def test_examples_exist() -> None:
    assert {p.parent.name for p in EXAMPLES} >= {"books", "company-directory"}


BOOKS_PAGE = """<html><body><ol>
<li><article class="product_pod"><p class="star-rating Three"></p>
<h3><a href="a-light-in-the-attic_1000/index.html" title="A Light in the Attic">A Light</a></h3>
<div class="product_price"><p class="price_color">£51.77</p></div></article></li>
<li><article class="product_pod"><p class="star-rating One"></p>
<h3><a href="soumission_998/index.html" title="Soumission">Soumission</a></h3>
<div class="product_price"><p class="price_color">£50.10</p></div></article></li>
</ol></body></html>"""


def test_books_example_offline() -> None:
    import httpx

    from crowley.adapters.http import HttpAdapter
    from crowley.infrastructure.network import StaticHostResolver

    def site(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/catalogue/page-1.html"
        return httpx.Response(200, text=BOOKS_PAGE, headers={"content-type": "text/html"})

    cw = Crowley(
        adapters=[HttpAdapter(transport=httpx.MockTransport(site))],
        resolver=StaticHostResolver({"books.toscrape.com": ["93.184.216.34"]}),
    )
    examples = Path(__file__).parents[2] / "examples"
    result = cw.run_sync(
        str(examples / "books" / "books.yml"), "list_books", inputs={"max_pages": 1}
    )
    assert result.output == [
        {
            "title": "A Light in the Attic",
            "price": 51.77,
            "rating": "Three",
            "url": "https://books.toscrape.com/catalogue/a-light-in-the-attic_1000/index.html",
        },
        {
            "title": "Soumission",
            "price": 50.1,
            "rating": "One",
            "url": "https://books.toscrape.com/catalogue/soumission_998/index.html",
        },
    ]

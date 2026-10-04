"""Generated extractor functions, the field engine and ``extract.auto`` run from templates."""

from typing import Any

import pytest

from crowley import Crowley
from crowley.domain.errors import ExchangeError, ExecutionError, ValidationError
from crowley.extractors.html import HtmlExtractor

TEMPLATE = """\
crowley: 1
id: acme/extract
version: 1.0.0
name: Extraction
description: Extractor tests.
permissions:
  hosts: [api.example.com]
operations:
  html:
    description: html.extract with a literal field spec.
    inputs:
      page: {}
    steps:
      - use: html.extract
        with:
          source: ${{ inputs.page }}
          fields:
            title: { selector: [".missing", "h1"] }
            price: { xpath: "//span[@itemprop='price']/@content" }
            link: { selector: "a.more", attr: href }
            tags: { selector: ".tag", all: true }
            stock: { selector: ".stock", default: unknown }
            nothing: { selector: ".none" }
            many_nothing: { selector: ".none", all: true }
            author:
              selector: ".byline"
              fields:
                name: { selector: ".name" }
            variants:
              selector: ".variant"
              all: true
              fields:
                color: { attr: data-color }
                label: {}
            raw: { selector: "h1", attr: raw_text }
            outer: { selector: "h1", attr: html }
            inner: { selector: ".byline", attr: inner_html }
    output:
      schema: {}
  rows:
    description: html.extract with a root.
    inputs:
      page: {}
    steps:
      - use: html.extract
        with:
          source: ${{ inputs.page }}
          root: [".nope", "li"]
          fields:
            name: { selector: "b" }
            href: { selector: "a", attr: href }
    output:
      schema: {}
  dynamic:
    description: Field specs and functions given at runtime.
    inputs:
      fn: { type: string }
      args: { type: object }
    steps:
      - use: acme.call
        with: { fn: "${{ inputs.fn }}", args: "${{ inputs.args }}" }
    output:
      schema: {}
  piped:
    description: Sources taken from prev.
    inputs:
      page: {}
    steps:
      - use: html.parse
        with: { source: "${{ inputs.page }}" }
      - id: first
        use: html.select
        with: { selector: h1 }
      - use: html.select_all
        with: { source: "${{ inputs.page }}", selector: ".tag" }
    output:
      value: ${{ [steps.first, prev] }}
      schema: {}
  required:
    description: A required field that matches nothing.
    inputs:
      page: {}
    steps:
      - use: html.extract
        with:
          source: ${{ inputs.page }}
          fields:
            sku: { selector: ".sku", required: true }
    output:
      schema: {}
  auto:
    description: extract.auto routing.
    inputs:
      source: {}
      using: { type: [string, "null"], default: null }
      fields: { type: object }
      root: { default: null }
    steps:
      - use: extract.auto
        with:
          source: ${{ inputs.source }}
          using: ${{ inputs.using }}
          fields: ${{ inputs.fields }}
          root: ${{ inputs.root }}
    output:
      schema: {}
"""

PAGE = """
<html><head><title>t</title></head><body>
  <h1>  Big   Title </h1>
  <span itemprop="price" content="9.99">9.99 EUR</span>
  <a class="more" href="/details?id=1">more</a>
  <span class="tag">a</span><span class="tag">b</span>
  <p class="byline">by <span class="name">Ada</span></p>
  <div class="variant" data-color="red">Red</div>
  <div class="variant" data-color="blue">Blue</div>
  <ul><li><b>one</b><a href="/1">x</a></li><li><b>two</b><a href="https://other/2">y</a></li></ul>
</body></html>
"""


def run(operation: str, inputs: dict[str, Any], cw: Crowley | None = None) -> Any:
    from crowley import FunctionContext, function

    @function(
        name="acme.call",
        input={"type": "object", "required": ["fn", "args"], "properties": {"fn": {}, "args": {}}},
        output={},
    )
    async def call(ctx: FunctionContext, fn: str, args: dict[str, Any]) -> Any:
        name, method = fn.split(".")
        handle = ctx.extractor(name)
        if method == "extract":
            return await handle.extract(args["source"], args["fields"], root=args.get("root"))
        if method == "read":
            return handle.read(handle.parse(args["source"]), args.get("attr"))
        return getattr(handle, method)(args["source"], args["selector"], **args.get("kw", {}))

    cw = cw or Crowley(functions=[call])
    if "acme.call" not in cw.registry.handlers:
        cw.register(call)
    text = TEMPLATE.replace("operations:", 'requires: ["acme.*@^1"]\noperations:', 1)
    tpl = cw.load_text(text, name="extract.yml")
    return cw.run_sync(tpl, operation, inputs=inputs)


def test_html_extract_field_engine() -> None:
    response = {"body": PAGE, "url": "https://shop.example.com/item/7", "media_type": "text/html"}
    out = run("html", {"page": response}).output
    assert out == {
        "title": "Big Title",
        "price": "9.99",
        "link": "https://shop.example.com/details?id=1",  # resolved against the response URL
        "tags": ["a", "b"],
        "stock": "unknown",
        "nothing": None,
        "many_nothing": [],
        "author": {"name": "Ada"},
        "variants": [{"color": "red", "label": "Red"}, {"color": "blue", "label": "Blue"}],
        "raw": "  Big   Title ",
        "outer": "<h1>  Big   Title </h1>",
        "inner": 'by <span class="name">Ada</span>',
    }


def test_root_with_fallback() -> None:
    out = run("rows", {"page": PAGE}).output
    assert out == [{"name": "one", "href": "/1"}, {"name": "two", "href": "https://other/2"}]


def test_sources_from_prev_and_handles() -> None:
    out = run("piped", {"page": PAGE}).output
    assert out == ["Big Title", ["a", "b"]]


def test_required_field_is_e502() -> None:
    with pytest.raises(ExecutionError, match="required field 'sku'") as info:
        run("required", {"page": PAGE})
    assert info.value.code == "E502"


def test_selector_fallback_event() -> None:
    cw = Crowley()
    seen: list[tuple[str | None, str, int, str]] = []
    cw.add_notifier(
        "selector.fallback",
        lambda e: seen.append((e.extractor, e.field, e.index, e.query)),
        extractor="html",
    )
    run("html", {"page": PAGE}, cw=cw)
    assert seen == [("html", "title", 1, "h1")]


# ── other extractors ─────────────────────────────────────────────────────────

XML = """<?xml version="1.0" encoding="ISO-8859-1"?>
<feed xmlns:m="urn:media"><entry id="1"><title> A </title><m:thumb url="t1"/></entry>
<entry id="2"><title>B</title><m:thumb url="t2"/></entry></feed>"""


def test_xml_extract_with_namespaces_and_css() -> None:
    fields = {
        "id": {"attr": "id"},
        "title": {"selector": "title"},
        "thumb": {"selector": "m:thumb/@url"},
        "css_title": {"css": "title"},
    }
    args = {"source": XML, "fields": fields, "root": "/feed/entry"}
    out = run("dynamic", {"fn": "xml.extract", "args": args}).output
    assert out == [
        {"id": "1", "title": "A", "thumb": "t1", "css_title": "A"},
        {"id": "2", "title": "B", "thumb": "t2", "css_title": "B"},
    ]
    xml_attr = {"source": "<a><b>x</b></a>", "selector": "/a/b", "kw": {"attr": "xml"}}
    assert run("dynamic", {"fn": "xml.select", "args": xml_attr}).output == "<b>x</b>"


def test_xml_never_resolves_entities() -> None:
    evil = '<?xml version="1.0"?><!DOCTYPE d [<!ENTITY x SYSTEM "file:///etc/passwd">]><d>&x;</d>'
    out = run("dynamic", {"fn": "xml.select", "args": {"source": evil, "selector": "/d"}}).output
    assert "root:" not in (out or "")


def test_json_extract_on_parsed_and_raw_bodies() -> None:
    body = {"data": {"items": [{"id": 1, "tags": ["x", "y"]}, {"id": 2, "tags": []}]}}
    fields = {
        "id": "$.id",
        "count": {"selector": "$.tags", "attr": "length"},
        "first_tag": {"selector": "$.tags[0]", "default": None},
    }
    args = {"source": {"body": body}, "fields": fields, "root": "$.data.items[*]"}
    out = run("dynamic", {"fn": "json.extract", "args": args}).output
    assert out == [
        {"id": 1, "count": 2, "first_tag": "x"},
        {"id": 2, "count": 0, "first_tag": None},
    ]
    raw = {"source": '{"a": {"b": 1}}', "selector": "$.a", "kw": {"attr": "keys"}}
    assert run("dynamic", {"fn": "json.select", "args": raw}).output == ["b"]
    scalar = {"source": {"body": 5}, "selector": "$.x"}
    assert run("dynamic", {"fn": "json.select_all", "args": scalar}).output == []
    length = {"source": {"body": 5}, "attr": "length"}
    assert run("dynamic", {"fn": "json.read", "args": length}).output is None


def test_text_extract_with_groups() -> None:
    text = "order 17 shipped; order 23 pending"
    fields = {
        "number": {"attr": "group:1"},
        "state": {"attr": "group:state"},
        "missing": {"attr": "group:9"},
        "whole": {},
    }
    args = {"source": text, "fields": fields, "root": r"order (\d+) (?P<state>\w+)"}
    out = run("dynamic", {"fn": "text.extract", "args": args}).output
    assert out == [
        {"number": "17", "state": "shipped", "missing": None, "whole": "order 17 shipped"},
        {"number": "23", "state": "pending", "missing": None, "whole": "order 23 pending"},
    ]
    root_read = {"source": b"plain", "attr": "group:1"}
    assert run("dynamic", {"fn": "text.read", "args": root_read}).output is None


# ── extract.auto ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("source", "using", "fields", "expected"),
    [
        (
            {"body": "<h1>Hi</h1>", "media_type": "text/html"},
            None,
            {"t": "h1"},
            {"t": "Hi"},
        ),
        (
            {"body": {"a": 1}, "media_type": "application/vnd.api+json"},
            None,
            {"a": "$.a"},
            {"a": 1},
        ),
        (
            {"body": "<r><v>1</v></r>", "media_type": "application/atom+xml"},
            None,
            {"v": "/r/v"},
            {"v": "1"},
        ),
        ({"body": "id=42", "media_type": "text/csv"}, None, {"id": r"id=(\d+)"}, {"id": "id=42"}),
        ("<b>x</b>", "html", {"b": "b"}, {"b": "x"}),
    ],
)
def test_extract_auto_routes_by_media_type(
    source: Any, using: str | None, fields: dict[str, Any], expected: Any
) -> None:
    out = run("auto", {"source": source, "using": using, "fields": fields}).output
    assert out == expected


def test_extract_auto_without_media_type_is_e403() -> None:
    with pytest.raises(ValidationError, match="no extractor handles") as info:
        run("auto", {"source": "<b/>", "fields": {"b": "b"}})
    assert info.value.code == "E403"


# ── errors ───────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("fn", "args", "code", "message"),
    [
        ("html.extract", {"source": PAGE, "fields": {"x": 5}}, "E403", "invalid fields"),
        ("html.extract", {"source": 5, "fields": {"x": "h1"}}, "E403", "can't read a int"),
        ("html.extract", {"source": PAGE, "fields": {"x": {"jsonpath": "$"}}}, "E403", "jsonpath"),
        ("html.extract", {"source": PAGE, "fields": {"x": "h1"}, "root": 5}, "E403", "root"),
        ("xml.extract", {"source": "<unclosed>", "fields": {"x": "/a"}}, "E502", "xml.parse"),
        ("json.extract", {"source": "{bad", "fields": {"x": "$"}}, "E502", "json.parse"),
        ("nope.extract", {"source": "x", "fields": {"x": "y"}}, "E607", "nope"),
    ],
)
def test_extraction_errors(fn: str, args: dict[str, Any], code: str, message: str | None) -> None:
    with pytest.raises((ValidationError, ExecutionError, ExchangeError)) as info:
        run("dynamic", {"fn": fn, "args": args})
    assert info.value.code == code
    if message:
        assert message in info.value.message


def test_handles_of_another_extractor_are_e403() -> None:
    from crowley.application.extractors import ExtractionEngine
    from crowley.extractors.json import JsonExtractor

    handle = ExtractionEngine(HtmlExtractor()).handle("node")
    with pytest.raises(ValidationError, match=r"html.node"):
        ExtractionEngine(JsonExtractor()).root(handle)


class _CustomHtml(HtmlExtractor):
    def read(self, node: Any, attr: str) -> Any:
        value = super().read(node, attr)
        return value.upper() if isinstance(value, str) else value


def test_process_extractor_override() -> None:
    cw = Crowley()
    tpl_text = TEMPLATE.replace("operations:", 'requires: ["acme.*@^1"]\noperations:', 1)
    run("piped", {"page": PAGE}, cw=cw)  # registers acme.call
    tpl = cw.load_text(tpl_text, name="extract.yml")
    process = cw.init(tpl, "piped", inputs={"page": PAGE}, extractors={"html": _CustomHtml()})
    assert process.run_sync().output == ["BIG TITLE", ["A", "B"]]
    from crowley.domain.errors import ConfigurationError
    from crowley.extractors.json import JsonExtractor

    with pytest.raises(ConfigurationError, match="must support") as info:
        cw.init(tpl, "piped", inputs={"page": PAGE}, extractors={"html": _Narrow()})
    assert info.value.code == "E906"
    with pytest.raises(ConfigurationError, match="unknown extractor"):
        cw.init(tpl, "piped", inputs={"page": PAGE}, extractors={"pdf": JsonExtractor()})


class _Narrow(HtmlExtractor):
    query_languages = ("css",)

"""The built-in http adapter, against an in-memory httpx transport (SPEC §13.7)."""

import base64
import json
from typing import Any

import httpx
import pytest

from crowley import Crowley, FunctionContext, Limits, function
from crowley.adapters.http import DEFAULT_USER_AGENT, HttpAdapter
from crowley.adapters.http.wire import query_params, retry_after_seconds, status_matches
from crowley.domain.common import ByteSize
from crowley.domain.errors import ExchangeError, ValidationError
from crowley.infrastructure.network import StaticHostResolver

TEMPLATE = """\
crowley: 1
id: acme/http
version: 1.0.0
name: Http
description: http adapter tests.
permissions:
  hosts: [api.example.com, other.example.com]
requires: ["acme.*@^1"]
defaults:
  http:
    headers: { X-Template: tpl }
operations:
  call:
    description: Any request through ctx.exchange.
    inputs:
      request: { type: object }
    steps:
      - use: acme.http
        with: { request: "${{ inputs.request }}" }
    output:
      schema: {}
  functions:
    description: The http.* functions.
    steps:
      - id: got
        use: http.get
        with:
          url: https://api.example.com/echo?a=1
          query: { b: [x, y], c: null, d: true }
          headers: { X-Step: step }
      - id: posted
        use: http.post
        with:
          url: https://api.example.com/echo
          body: { json: { n: 1 } }
      - id: head
        use: http.head
        with: { url: "https://api.example.com/text" }
      - id: full
        use: http.request
        with: { method: PATCH, url: "https://api.example.com/echo" }
    output:
      value:
        got: ${{ steps.got.body }}
        posted: ${{ steps.posted.body }}
        head: ${{ steps.head.body }}
        full: ${{ steps.full.body.method }}
      schema: {}
  sessions:
    description: Cookies in the process session and an isolated one.
    steps:
      - use: http.get
        with: { url: "https://api.example.com/set-cookie" }
      - id: outer
        use: http.get
        with: { url: "https://api.example.com/echo" }
      - id: inner
        use: http.session
        with:
          headers: { X-Session: seeded }
          cookies: { seed: "1" }
        do:
          - use: http.get
            with: { url: "https://api.example.com/echo" }
          - return: ${{ prev.body.headers }}
    output:
      value:
        outer: ${{ steps.outer.body.headers.cookie }}
        inner: ${{ steps.inner }}
      schema: {}
"""

RESOLVER = StaticHostResolver(
    {"api.example.com": ["93.184.216.34"], "other.example.com": ["93.184.216.35"]}
)


@function(
    name="acme.http",
    input={"type": "object", "required": ["request"], "properties": {"request": {}}},
    output={},
)
async def call(ctx: FunctionContext, request: dict[str, Any], **kwargs: Any) -> Any:
    return await ctx.exchange("http", request)


class Server:
    """Routes requests by path; records what it received."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.flaky = 0

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path
        if path == "/json":
            return httpx.Response(200, json={"hello": "world"})
        if path == "/ld":
            return httpx.Response(
                200, content=b'{"a": 1}', headers={"content-type": "application/ld+json"}
            )
        if path == "/text":
            return httpx.Response(200, text="hi", headers={"content-type": "text/plain"})
        if path == "/latin":
            return httpx.Response(
                200,
                content="café".encode("latin-1"),
                headers={"content-type": "text/html; charset=latin-1"},
            )
        if path == "/bytes":
            return httpx.Response(200, content=b"\x89PNG", headers={"content-type": "image/png"})
        if path == "/untyped":
            return httpx.Response(200, content=b"plain")
        if path == "/badjson":
            return httpx.Response(
                200, content=b"{not json", headers={"content-type": "application/json"}
            )
        if path == "/empty":
            return httpx.Response(204, headers={"content-type": "application/json"})
        if path == "/set-cookie":
            return httpx.Response(200, headers={"set-cookie": "sid=abc; Path=/"}, json={})
        if path == "/redirect":
            return httpx.Response(302, headers={"location": "https://other.example.com/json"})
        if path == "/redirect-evil":
            return httpx.Response(302, headers={"location": "https://evil.org/x"})
        if path == "/loop":
            return httpx.Response(302, headers={"location": "/loop"})
        if path.startswith("/status/"):
            headers = {}
            if "retry_after" in request.url.params:
                headers["retry-after"] = request.url.params["retry_after"]
            return httpx.Response(int(path.rsplit("/", 1)[1]), headers=headers, json={})
        if path == "/flaky":
            self.flaky += 1
            return httpx.Response(503 if self.flaky == 1 else 200, json={"try": self.flaky})
        if path == "/big":
            return httpx.Response(200, content=b"x" * 2000)
        if path == "/timeout":
            raise httpx.ReadTimeout("slow", request=request)
        if path == "/broken":
            raise httpx.ConnectError("refused", request=request)
        body = request.content
        return httpx.Response(
            200,
            json={
                "method": request.method,
                "url": str(request.url),
                "headers": {k.lower(): v for k, v in request.headers.items()},
                "body": body.decode("utf-8", errors="replace"),
            },
        )


def make(**adapter_options: Any) -> tuple[Crowley, Any, Server]:
    server = Server()
    adapter = HttpAdapter(transport=httpx.MockTransport(server), **adapter_options)
    cw = Crowley(adapters=[adapter], functions=[call], resolver=RESOLVER)
    return cw, cw.load_text(TEMPLATE, name="http.yml"), server


async def request(req: dict[str, Any], **options: Any) -> Any:
    cw, tpl, _ = make(**options)
    return (await cw.run(tpl, "call", inputs={"request": req})).output


# ── functions, query, headers ────────────────────────────────────────────────


async def test_http_functions() -> None:
    cw, tpl, server = make()
    out = (await cw.run(tpl, "functions")).output
    got = out["got"]
    assert got["method"] == "GET"
    assert got["url"] == "https://api.example.com/echo?a=1&b=x&b=y&d=true"
    assert got["headers"]["user-agent"] == DEFAULT_USER_AGENT
    assert got["headers"]["x-template"] == "tpl"
    assert got["headers"]["x-step"] == "step"
    assert out["posted"]["method"] == "POST"
    assert json.loads(out["posted"]["body"]) == {"n": 1}
    assert out["posted"]["headers"]["content-type"] == "application/json"
    assert out["head"] == ""
    assert out["full"] == "PATCH"
    assert [r.method for r in server.requests] == ["GET", "POST", "HEAD", "PATCH"]


async def test_response_shape() -> None:
    out = await request({"url": "https://api.example.com/json"})
    assert out["status"] == 200
    assert out["ok"] is True
    assert out["url"] == "https://api.example.com/json"
    assert out["media_type"] == "application/json"
    assert out["body"] == {"hello": "world"}
    assert out["headers"]["content-type"] == "application/json"
    assert out["redirects"] == []
    assert out["request"]["method"] == "GET"
    assert out["elapsed_ms"] >= 0


async def test_header_overrides_and_removal() -> None:
    out = await request(
        {
            "url": "https://api.example.com/echo",
            "headers": {"User-Agent": "custom/1", "X-TEMPLATE": None},
        }
    )
    headers = out["body"]["headers"]
    assert headers["user-agent"] == "custom/1"
    assert "x-template" not in headers


async def test_configured_user_agent() -> None:
    out = await request({"url": "https://api.example.com/echo"}, user_agent="bot/2")
    assert out["body"]["headers"]["user-agent"] == "bot/2"


# ── bodies and auth ──────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("body", "content_type", "sent"),
    [
        (
            {"form": {"a": 1, "b": None, "c": True}},
            "application/x-www-form-urlencoded",
            "a=1&c=true",
        ),
        ({"raw": "hello"}, "text/plain; charset=utf-8", "hello"),
        ({"bytes": base64.b64encode(b"\x00\x01").decode()}, "application/octet-stream", "\x00\x01"),
    ],
)
async def test_bodies(body: dict[str, Any], content_type: str, sent: str) -> None:
    out = await request({"method": "POST", "url": "https://api.example.com/echo", "body": body})
    assert out["body"]["headers"]["content-type"] == content_type
    assert out["body"]["body"] == sent


async def test_multipart_and_explicit_content_type() -> None:
    parts = [
        {"name": "field", "value": "v"},
        {
            "name": "file",
            "filename": "a.txt",
            "content_type": "text/plain",
            "content_base64": base64.b64encode(b"data").decode(),
        },
    ]
    out = await request(
        {"method": "POST", "url": "https://api.example.com/echo", "body": {"multipart": parts}}
    )
    assert out["body"]["headers"]["content-type"].startswith("multipart/form-data")
    assert 'filename="a.txt"' in out["body"]["body"]
    explicit = await request(
        {
            "method": "POST",
            "url": "https://api.example.com/echo",
            "headers": {"content-type": "application/vnd.custom"},
            "body": {"raw": "x"},
        }
    )
    assert explicit["body"]["headers"]["content-type"] == "application/vnd.custom"


@pytest.mark.parametrize(
    ("auth", "header"),
    [
        ({"bearer": "tok"}, "Bearer tok"),
        ({"basic": {"username": "u", "password": "p"}}, "Basic dTpw"),
    ],
)
async def test_auth(auth: dict[str, Any], header: str) -> None:
    out = await request({"url": "https://api.example.com/echo", "auth": auth})
    assert out["body"]["headers"]["authorization"] == header


# ── response bodies ──────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("path", "response_type", "expected"),
    [
        ("/ld", "auto", {"a": 1}),
        ("/text", "auto", "hi"),
        ("/latin", "auto", "café"),
        ("/bytes", "auto", b"\x89PNG"),
        ("/untyped", "auto", "plain"),
        ("/badjson", "auto", "{not json"),
        ("/empty", "auto", None),
        ("/json", "text", '{"hello":"world"}'),
        ("/json", "bytes", b'{"hello":"world"}'),
        ("/text", "json", None),
    ],
)
async def test_response_types(path: str, response_type: str, expected: Any) -> None:
    req = {"url": f"https://api.example.com{path}", "response_type": response_type}
    if path == "/text" and response_type == "json":
        with pytest.raises(ExchangeError, match="not valid JSON") as info:
            await request(req)
        assert info.value.code == "E602"
        return
    if path == "/empty":
        req["expect_status"] = [204]
    if isinstance(expected, bytes):  # bytes are fine inside a run, but never as output (E405)
        cw, tpl, _ = make()
        bodies: list[Any] = []
        cw.add_notifier("exchange.after", lambda e: bodies.append(e.result.response["body"]))
        with pytest.raises(ValidationError, match="found bytes"):
            await cw.run(tpl, "call", inputs={"request": req})
        assert bodies == [expected]
        return
    assert (await request(req))["body"] == expected


async def test_encoding_override() -> None:
    out = await request({"url": "https://api.example.com/latin", "encoding": "utf-8"})
    assert out["body"] == "caf�"


# ── status, redirects ────────────────────────────────────────────────────────


async def test_unexpected_status_is_e601() -> None:
    with pytest.raises(ExchangeError, match="unexpected status 404") as info:
        await request({"url": "https://api.example.com/status/404"})
    assert info.value.code == "E601"
    assert info.value.value["status"] == 404
    out = await request({"url": "https://api.example.com/status/404", "expect_status": ["4xx"]})
    assert out["ok"] is False


async def test_redirects_are_followed_and_checked() -> None:
    cw, tpl, server = make()
    req = {"url": "https://api.example.com/redirect"}
    out = (await cw.run(tpl, "call", inputs={"request": req})).output
    assert out["url"] == "https://other.example.com/json"
    assert out["redirects"] == ["https://other.example.com/json"]
    assert out["body"] == {"hello": "world"}
    with pytest.raises(ExchangeError, match=r"evil.org") as info:
        await cw.run(
            tpl, "call", inputs={"request": {"url": "https://api.example.com/redirect-evil"}}
        )
    assert info.value.code == "E604"
    assert all(r.url.host != "evil.org" for r in server.requests)  # never contacted


async def test_redirect_options() -> None:
    no_follow = await request(
        {
            "url": "https://api.example.com/redirect",
            "follow_redirects": False,
            "expect_status": ["3xx"],
        }
    )
    assert no_follow["status"] == 302
    with pytest.raises(ExchangeError, match="more than 3 redirects") as info:
        await request({"url": "https://api.example.com/loop", "max_redirects": 3})
    assert info.value.code == "E602"


# ── cookies and sessions ─────────────────────────────────────────────────────


async def test_cookies_persist_per_process_and_sessions_isolate() -> None:
    cw, tpl, _ = make()
    out = (await cw.run(tpl, "sessions")).output
    assert out["outer"] == "sid=abc"
    assert out["inner"]["x-session"] == "seeded"
    assert out["inner"]["cookie"] == "seed=1"  # the process cookie does not leak in
    again = (await cw.run(tpl, "sessions")).output
    assert again["outer"] == "sid=abc"  # a new process starts with an empty jar


async def test_request_cookies() -> None:
    out = await request({"url": "https://api.example.com/echo", "cookies": {"a": "1"}})
    assert out["body"]["headers"]["cookie"] == "a=1"


# ── failures, retries, limits ────────────────────────────────────────────────


@pytest.mark.parametrize(("path", "code"), [("/timeout", "E603"), ("/broken", "E602")])
async def test_transport_failures(path: str, code: str) -> None:
    with pytest.raises(ExchangeError) as info:
        await request({"url": f"https://api.example.com{path}"})
    assert info.value.code == code


async def test_retry_on_status() -> None:
    cw, tpl, _ = make()
    delays: list[float] = []
    cw.add_notifier("exchange.retry", lambda e: delays.append(e.delay))
    req = {"url": "https://api.example.com/flaky", "retry": {"times": 2, "delay": 0}}
    out = (await cw.run(tpl, "call", inputs={"request": req})).output
    assert out["body"] == {"try": 2}
    assert delays == [0.0]


async def test_retry_after_and_on_status_filter() -> None:
    cw, tpl, server = make()
    delays: list[float] = []
    cw.add_notifier("exchange.retry", lambda e: setattr(e, "delay", 0) or delays.append(1))
    req = {
        "url": "https://api.example.com/status/429?retry_after=7",
        "retry": {"times": 1, "delay": "1s"},
    }
    with pytest.raises(ExchangeError):
        await cw.run(tpl, "call", inputs={"request": req})
    assert len(server.requests) == 2
    seen: list[float] = []
    cw.notifiers.clear()
    cw.add_notifier("exchange.retry", lambda e: seen.append(e.delay) or setattr(e, "delay", 0))
    with pytest.raises(ExchangeError):
        await cw.run(tpl, "call", inputs={"request": req})
    assert seen == [7.0]
    server.requests.clear()
    not_listed = {
        "url": "https://api.example.com/status/500",
        "retry": {"times": 3, "delay": 0},
    }
    with pytest.raises(ExchangeError):
        await cw.run(tpl, "call", inputs={"request": not_listed})
    assert len(server.requests) == 1


async def test_retry_on_network_errors() -> None:
    cw, tpl, server = make()
    req = {"url": "https://api.example.com/broken", "retry": {"times": 2, "delay": 0}}
    with pytest.raises(ExchangeError):
        await cw.run(tpl, "call", inputs={"request": req})
    assert len(server.requests) == 3
    server.requests.clear()
    off = {**req, "retry": {"times": 2, "delay": 0, "on_network_error": False}}
    with pytest.raises(ExchangeError):
        await cw.run(tpl, "call", inputs={"request": off})
    assert len(server.requests) == 1


async def test_response_size_cap_is_e606() -> None:
    server = Server()
    adapter = HttpAdapter(transport=httpx.MockTransport(server))
    cw = Crowley(
        adapters=[adapter],
        functions=[call],
        resolver=RESOLVER,
        limits=Limits(max_response_bytes=ByteSize(1000)),
    )
    tpl = cw.load_text(TEMPLATE, name="http.yml")
    with pytest.raises(ExchangeError) as info:
        await cw.run(tpl, "call", inputs={"request": {"url": "https://api.example.com/big"}})
    assert info.value.code == "E606"


# ── helpers ──────────────────────────────────────────────────────────────────


def test_wire_helpers() -> None:
    assert query_params({"a": [1, None, {"b": 2}], "c": False}) == [
        ("a", "1"),
        ("a", '{"b":2}'),
        ("c", "false"),
    ]
    assert status_matches(201, ["2xx"])
    assert status_matches(404, [404])
    assert not status_matches(500, ["2xx", 404, True])
    assert retry_after_seconds("12") == 12.0
    assert retry_after_seconds(None) is None
    assert retry_after_seconds("not a date") is None
    assert retry_after_seconds("Wed, 21 Oct 2015 07:28:00 GMT") == 0.0


def test_real_client_construction() -> None:
    adapter = HttpAdapter(proxy="http://proxy.local:8080", http2=False, timeout="5s")
    client = adapter.make_client(proxy=None, verify=False)
    assert adapter.timeout == 5.0
    assert client.follow_redirects is False
    assert adapter.request_defaults()["timeout"] == 5.0

"""``HttpAdapter``: the built-in http adapter on httpx (docs/SPEC.md §13.7)."""

import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from importlib import metadata
from typing import Any, ClassVar

import httpx

from crowley.adapters.http import schemas
from crowley.adapters.http.wire import (
    build_request,
    lower_headers,
    response_value,
    retry_after_seconds,
    status_matches,
)
from crowley.application.adapters import AdapterContext, BaseAdapter
from crowley.application.registry import RegisteredFunction
from crowley.application.runtime import FunctionContext
from crowley.domain.adapters import Exchange, ExchangeResult, RetryPolicy, TargetKind, deep_merge
from crowley.domain.common import Duration, SemVer
from crowley.domain.errors import ExchangeError
from crowley.domain.functions import FunctionKind, FunctionSpec


def _version() -> str:
    try:
        return metadata.version("crowley")
    except metadata.PackageNotFoundError:  # pragma: no cover - running from a source tree
        return "0"


DEFAULT_USER_AGENT = f"crowley/{_version()} (+https://github.com/mohamed-naser-awd/crowley)"
DEFAULT_TIMEOUT = 30.0
DEFAULT_RETRY_STATUSES = (429, 502, 503, 504)


@dataclass(slots=True)
class HttpSession:
    """One cookie jar and a client per (proxy, verify_tls) for a process or ``http.session``."""

    adapter: "HttpAdapter"
    cookies: httpx.Cookies = field(default_factory=httpx.Cookies)
    headers: dict[str, Any] = field(default_factory=dict)
    clients: dict[tuple[str | None, bool], httpx.AsyncClient] = field(default_factory=dict)

    def client(self, proxy: str | None, verify: bool) -> httpx.AsyncClient:
        key = (proxy, verify)
        client = self.clients.get(key)
        if client is None:
            client = self.adapter.make_client(proxy=proxy, verify=verify)
            self.clients[key] = client
        return client

    async def aclose(self) -> None:
        clients, self.clients = self.clients, {}
        for client in clients.values():
            await client.aclose()


class HttpAdapter(BaseAdapter):
    name: ClassVar[str] = "http"
    version: ClassVar[str] = "1.0.0"
    schemes: ClassVar[tuple[str, ...]] = ("http", "https")
    target_kind: ClassVar[TargetKind] = TargetKind.NETWORK
    builtin: ClassVar[bool] = True
    config_schema: ClassVar[dict[str, Any]] = schemas.CONFIG
    defaults_schema: ClassVar[dict[str, Any]] = schemas.DEFAULTS
    request_schema: ClassVar[dict[str, Any]] = schemas.REQUEST
    response_schema: ClassVar[dict[str, Any]] = schemas.RESPONSE
    sensitive_fields: ClassVar[tuple[str, ...]] = ("authorization", "cookie", "set-cookie")

    def __init__(
        self,
        *,
        user_agent: str = DEFAULT_USER_AGENT,
        proxy: str | None = None,
        timeout: float | str = DEFAULT_TIMEOUT,
        http2: bool = True,
        allow_private_networks: bool = False,
        max_connections: int = 100,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        """``transport`` replaces the network (e.g. ``httpx.MockTransport`` in tests)."""
        self.user_agent = user_agent
        self.proxy = proxy
        self.timeout = Duration.parse(timeout).seconds
        self.http2 = http2
        self.allow_private_networks = allow_private_networks
        self.max_connections = max_connections
        self.transport = transport

    # ── contract ───────────────────────────────────────────────────────────────

    def functions(self) -> Sequence[FunctionSpec | RegisteredFunction]:
        return _functions(SemVer.parse(self.version))

    def request_defaults(self) -> Mapping[str, Any]:
        return {"headers": {"user-agent": self.user_agent}, "timeout": self.timeout}

    def merge_request(self, *layers: Mapping[str, Any]) -> dict[str, Any]:
        """Deep merge, with header names compared case-insensitively."""
        return deep_merge(
            *(
                {**layer, "headers": lower_headers(layer["headers"])}
                if isinstance(layer.get("headers"), Mapping)
                else layer
                for layer in layers
            )
        )

    def make_client(self, *, proxy: str | None, verify: bool) -> httpx.AsyncClient:
        limits = httpx.Limits(max_connections=self.max_connections)
        if self.transport is not None:  # tests: the transport is the network
            return httpx.AsyncClient(transport=self.transport, follow_redirects=False)
        return httpx.AsyncClient(
            http2=self.http2,
            proxy=proxy or self.proxy,
            verify=verify,
            limits=limits,
            follow_redirects=False,
        )

    async def open(self, ctx: AdapterContext) -> HttpSession:
        session = HttpSession(adapter=self, headers=lower_headers(ctx.seed.get("headers")))
        for name, value in (ctx.seed.get("cookies") or {}).items():
            session.cookies.set(name, value)
        return session

    async def close(self, session: Any) -> None:
        await session.aclose()

    async def send(self, session: Any, exchange: Exchange) -> ExchangeResult:
        assert isinstance(session, HttpSession)
        request = dict(exchange.request)
        if session.headers:
            request["headers"] = {**session.headers, **lower_headers(request.get("headers"))}
        for name, value in (request.get("cookies") or {}).items():
            session.cookies.set(name, value)
        timeout = Duration.parse(request.get("timeout", self.timeout)).seconds
        client = session.client(request.get("proxy"), bool(request.get("verify_tls", True)))
        follow = bool(request.get("follow_redirects", True))
        max_redirects = int(request.get("max_redirects", 10))
        started = time.monotonic()
        outgoing = build_request(client, request, cookies=session.cookies, timeout=timeout)
        redirects: list[str] = []
        total = 0
        while True:
            response, content = await self._send_one(client, outgoing, exchange)
            total += len(content)
            session.cookies.extract_cookies(response)
            client.cookies.clear()
            next_request = response.next_request
            if not (follow and response.is_redirect and next_request is not None):
                break
            if len(redirects) >= max_redirects:
                raise ExchangeError(
                    "E602",
                    f"more than {max_redirects} redirects from {exchange.target}",
                    hint="raise max_redirects or set follow_redirects: false",
                )
            hop = str(next_request.url)
            if exchange.check_hop is not None:
                await exchange.check_hop(hop)
            redirects.append(hop)
            next_request.headers.pop("cookie", None)
            session.cookies.set_cookie_header(next_request)
            outgoing = next_request

        value = response_value(
            response,
            content,
            request=outgoing,
            redirects=redirects,
            response_type=str(request.get("response_type", "auto")),
            encoding=request.get("encoding"),
            elapsed_ms=(time.monotonic() - started) * 1000,
        )
        expected = request.get("expect_status", ["2xx"])
        if not status_matches(response.status_code, expected):
            raise ExchangeError(
                "E601",
                f"unexpected status {response.status_code} for {outgoing.method} {response.url}",
                value=value,
                hint=f"expect_status is {expected}",
            )
        return ExchangeResult(
            response=value,
            meta={"bytes_in": total, "hops": redirects, "elapsed_ms": value["elapsed_ms"]},
        )

    async def _send_one(
        self, client: httpx.AsyncClient, request: httpx.Request, exchange: Exchange
    ) -> tuple[httpx.Response, bytes]:
        try:
            response = await client.send(request, stream=True)
            try:
                chunks: list[bytes] = []
                size = 0
                async for chunk in response.aiter_bytes():
                    size += len(chunk)
                    if exchange.max_bytes is not None and size > exchange.max_bytes:
                        raise ExchangeError(
                            "E606",
                            f"response from {request.url} exceeds {exchange.max_bytes} bytes",
                        )
                    chunks.append(chunk)
            finally:
                await response.aclose()
        except httpx.TimeoutException as exc:
            raise ExchangeError(
                "E603", f"{request.method} {request.url} timed out: {exc}", cause=exc
            ) from exc
        except httpx.HTTPError as exc:
            raise ExchangeError(
                "E602",
                f"{request.method} {request.url} failed: {type(exc).__name__}: {exc}",
                cause=exc,
            ) from exc
        return response, b"".join(chunks)

    def retry_delay(
        self,
        exchange: Exchange,
        result: ExchangeResult | None,
        error: Exception | None,
        attempt: int,
    ) -> float | None:
        config = exchange.request.get("retry")
        if not isinstance(config, Mapping) or attempt > int(config.get("times", 0)):
            return None
        policy = RetryPolicy(
            times=int(config["times"]),
            backoff=config.get("backoff", "exponential"),
            delay=Duration.parse(config.get("delay", "1s")),
            max_delay=Duration.parse(config.get("max_delay", "30s")),
        )
        if not isinstance(error, ExchangeError):
            return None
        if error.code == "E601" and isinstance(error.value, Mapping):
            status = error.value.get("status")
            if status not in config.get("on_status", DEFAULT_RETRY_STATUSES):
                return None
            if config.get("respect_retry_after", True):
                headers = error.value.get("headers") or {}
                after = retry_after_seconds(headers.get("retry-after"))
                if after is not None:
                    return min(after, policy.max_delay.seconds)
            return policy.delay_for(attempt)
        if error.code in ("E602", "E603") and config.get("on_network_error", True):
            return policy.delay_for(attempt)
        return None


# ── functions ──────────────────────────────────────────────────────────────────


def _request_handler(method: str | None) -> Any:
    async def handler(ctx: FunctionContext, **kwargs: Any) -> Any:
        request = {k: v for k, v in kwargs.items() if k != "prev"}
        if method is not None:
            request["method"] = method
        return await ctx.exchange("http", request)

    handler.__name__ = f"http_{(method or 'request').lower()}"
    return handler


async def _session(ctx: FunctionContext, **kwargs: Any) -> Any:
    assert ctx.body is not None
    seed = {k: kwargs[k] for k in ("headers", "cookies") if k in kwargs}
    async with ctx.adapter("http").isolated(**seed):
        outcome = await ctx.body.run()
    return outcome.value if outcome.has_value else None


def _functions(version: SemVer) -> list[FunctionSpec | RegisteredFunction]:
    common: dict[str, Any] = {
        "version": version,
        "adapter": "http",
        "builtin": True,
        "output": schemas.RESPONSE,
    }
    found: list[FunctionSpec | RegisteredFunction] = [
        RegisteredFunction.create(
            FunctionSpec(
                name="http.request",
                description="Send an HTTP request (SPEC §13.7).",
                input=schemas.REQUEST,
                **common,
            ),
            _request_handler(None),
        )
    ]
    without_method = {k: v for k, v in schemas.REQUEST_PROPERTIES.items() if k != "method"}
    for method in ("get", "post", "put", "patch", "delete", "head"):
        spec = FunctionSpec(
            name=f"http.{method}",
            description=f"http.request with method {method.upper()}.",
            input={
                "type": "object",
                "required": ["url"],
                "properties": without_method,
                "additionalProperties": False,
            },
            **common,
        )
        found.append(RegisteredFunction.create(spec, _request_handler(method.upper())))
    session_spec = FunctionSpec(
        name="http.session",
        description="Run the body with an isolated cookie jar and connection pool.",
        kind=FunctionKind.BLOCK,
        input={
            "type": "object",
            "properties": {
                "headers": schemas.REQUEST_PROPERTIES["headers"],
                "cookies": schemas.REQUEST_PROPERTIES["cookies"],
            },
            "additionalProperties": False,
        },
        version=version,
        adapter="http",
        builtin=True,
    )
    found.append(RegisteredFunction.create(session_spec, _session))
    return found

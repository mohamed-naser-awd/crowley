"""A fake network adapter for exercising the exchange pipeline."""

from typing import Any, ClassVar

from crowley import FunctionContext, RegisteredFunction, function
from crowley.application.adapters import AdapterContext, BaseAdapter
from crowley.domain.adapters import Exchange, ExchangeResult, TargetKind
from crowley.domain.errors import ExchangeError
from crowley.domain.functions import TARGET

REQUEST: dict[str, Any] = {
    "type": "object",
    "required": ["url"],
    "properties": {
        "url": {"type": "string", TARGET: True},
        "payload": {},
        "fail": {"type": "string"},
        "size": {"type": "integer"},
        "hops": {"type": "array", "items": {"type": "string"}},
        "headers": {"type": "object"},
        "bad_response": {"type": "boolean"},
    },
    "additionalProperties": False,
}
RESPONSE: dict[str, Any] = {
    "type": "object",
    "required": ["url"],
    "properties": {"url": {"type": "string"}},
}


@function(name="echo.call", input=REQUEST, output=RESPONSE)
async def call(ctx: FunctionContext, **kwargs: Any) -> Any:
    request = {k: v for k, v in kwargs.items() if k != "prev"}
    return await ctx.exchange("echo", request)


@function(
    name="echo.send",
    input={
        "type": "object",
        "required": ["request"],
        "properties": {"request": {"type": "object"}},
    },
    output={},
)
async def send(ctx: FunctionContext, request: dict[str, Any], **kwargs: Any) -> Any:
    return await ctx.exchange("echo", request)


@function(
    name="echo.isolated",
    kind="block",
    input={"type": "object", "properties": {"seed": {"type": "string"}}},
    output={},
)
async def isolated(ctx: FunctionContext, seed: str = "", **kwargs: Any) -> Any:
    assert ctx.body is not None
    handle = ctx.adapter("echo")
    async with handle.isolated(seed=seed):
        outcome = await ctx.body.run()
    return outcome.value


@function(name="echo.raw", input={"type": "object"}, output={})
async def raw(ctx: FunctionContext, **kwargs: Any) -> Any:
    """Calls an adapter by name from a non-adapter perspective (used for E607)."""
    handle = ctx.adapter(str(kwargs.get("adapter", "echo")))
    session = await handle.session()
    return {"name": handle.name, "version": str(handle.spec.version), "session": session}


class EchoAdapter(BaseAdapter):
    name: ClassVar[str] = "echo"
    version: ClassVar[str] = "1.0.0"
    schemes: ClassVar[tuple[str, ...]] = ("echo",)
    target_kind: ClassVar[TargetKind] = TargetKind.NETWORK
    request_schema: ClassVar[dict[str, Any]] = REQUEST
    response_schema: ClassVar[dict[str, Any]] = RESPONSE
    defaults_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {"headers": {"type": "object"}, "payload": {}},
        "additionalProperties": False,
    }

    def __init__(self, *, allow_private_networks: bool = False, retries: int = 0) -> None:
        self.allow_private_networks = allow_private_networks
        self.retries = retries
        self.opened: list[dict[str, Any]] = []
        self.closed: list[int] = []
        self.sent: list[Exchange] = []

    def functions(self) -> list[RegisteredFunction]:
        return [call, send, isolated, raw]

    def request_defaults(self) -> dict[str, Any]:
        return {"headers": {"x-config": "config", "x-layer": "config"}}

    async def open(self, ctx: AdapterContext) -> Any:
        session = {"id": len(self.opened), "seed": dict(ctx.seed)}
        self.opened.append(session)
        return session

    async def close(self, session: Any) -> None:
        self.closed.append(session["id"])

    async def send(self, session: Any, exchange: Exchange) -> ExchangeResult:
        self.sent.append(exchange)
        request = exchange.request
        for hop in request.get("hops", []):
            assert exchange.check_hop is not None
            await exchange.check_hop(hop)
        fail = request.get("fail")
        if fail == "transport":
            raise ConnectionError("connection reset")
        if fail:
            raise ExchangeError(fail, f"echo failed with {fail}")
        if request.get("bad_response"):
            return ExchangeResult(response={"no_url": True})
        return ExchangeResult(
            response={
                "url": exchange.target,
                "payload": request.get("payload"),
                "headers": request.get("headers"),
                "session": session["id"],
                "seed": session["seed"],
            },
            meta={"bytes_in": request.get("size", 10)},
        )

    def retry_delay(
        self,
        exchange: Exchange,
        result: ExchangeResult | None,
        error: Exception | None,
        attempt: int,
    ) -> float | None:
        if isinstance(error, ExchangeError) and error.code == "E601" and attempt <= self.retries:
            return 0.0
        return None


class OtherEcho(EchoAdapter):
    """A compatible replacement."""

    version: ClassVar[str] = "1.5.0"


class IncompatibleEcho(EchoAdapter):
    version: ClassVar[str] = "2.0.0"

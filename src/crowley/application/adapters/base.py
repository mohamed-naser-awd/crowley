"""BaseAdapter: the public base class for I/O plugins (docs/SPEC.md §13.1).

An adapter only moves data: ``open`` a per-process session, ``send`` one exchange, ``close``.
Notifiers, permissions, limits, rate limiting, retries, size caps and redaction all live in the
shared exchange pipeline (``AdapterService``), so every adapter gets them for free.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, ClassVar

from crowley.domain.adapters import AdapterSpec, Exchange, ExchangeResult, TargetKind, deep_merge
from crowley.domain.common import SemVer
from crowley.domain.errors import ExchangeError
from crowley.domain.events import RunInfo
from crowley.domain.functions import TARGET, FunctionSpec

if TYPE_CHECKING:
    from crowley.application.registry.functions import RegisteredFunction


@dataclass(frozen=True, slots=True, kw_only=True)
class AdapterContext:
    """Passed to ``open``: the run it belongs to and optional seed values (e.g. cookies)."""

    run: RunInfo | None = None
    seed: Mapping[str, Any] = field(default_factory=dict)


class BaseAdapter:
    """Subclass to add a protocol: set the class attributes, implement ``send``."""

    name: ClassVar[str]
    version: ClassVar[str]
    schemes: ClassVar[tuple[str, ...]] = ()
    target_kind: ClassVar[TargetKind] = TargetKind.NETWORK
    config_schema: ClassVar[Mapping[str, Any]] = {"type": "object"}
    defaults_schema: ClassVar[Mapping[str, Any]] = {"type": "object"}
    request_schema: ClassVar[Mapping[str, Any]] = {"type": "object"}
    response_schema: ClassVar[Mapping[str, Any]] = {"type": "object"}
    sensitive_fields: ClassVar[tuple[str, ...]] = ()
    """Request/response fields scrubbed when exchanges are serialized (e.g. ``Authorization``)."""
    builtin: ClassVar[bool] = False

    allow_private_networks: bool = False
    """Network adapters only: permit loopback, private, link-local and multicast addresses."""

    @property
    def spec(self) -> AdapterSpec:
        return AdapterSpec(
            name=self.name,
            version=SemVer.parse(self.version),
            schemes=self.schemes,
            target_kind=self.target_kind,
            config_schema=self.config_schema,
            defaults_schema=self.defaults_schema,
            request_schema=self.request_schema,
            response_schema=self.response_schema,
            builtin=self.builtin,
        )

    def functions(self) -> "Sequence[FunctionSpec | RegisteredFunction]":
        """Functions this adapter contributes, all in its own namespace (e.g. ``http.get``)."""
        return []

    # ── runtime ────────────────────────────────────────────────────────────────

    async def open(self, ctx: AdapterContext) -> Any:
        """Create a per-process session (connection pool, cookie jar, …)."""
        return None

    async def send(self, session: Any, exchange: Exchange) -> ExchangeResult:
        """Perform one exchange. Raise ``ExchangeError`` (E601/E602/E603) on failure."""
        raise ExchangeError("E607", f"adapter {self.name!r} does not implement send()")

    async def close(self, session: Any) -> None:
        """Release a session. Called once per opened session, even after failures."""

    # ── hooks with sensible defaults ───────────────────────────────────────────

    def request_defaults(self) -> Mapping[str, Any]:
        """Request values from the integrator's adapter config (lowest merge priority)."""
        return {}

    def merge_request(self, *layers: Mapping[str, Any]) -> dict[str, Any]:
        """Merge request layers, lowest priority first (SPEC §13.4). Default: deep merge."""
        return deep_merge(*layers)

    def target_field(self) -> str | None:
        """The request property holding the target URI (marked ``x-crowley-target``)."""
        properties = self.request_schema.get("properties", {})
        if isinstance(properties, Mapping):
            for name, schema in properties.items():
                if isinstance(schema, Mapping) and schema.get(TARGET) is True:
                    return str(name)
        return None

    def target_of(self, request: Mapping[str, Any]) -> str:
        key = self.target_field()
        value = request.get(key) if key else None
        return value if isinstance(value, str) else ""

    def with_target(self, request: Mapping[str, Any], target: str) -> dict[str, Any]:
        key = self.target_field()
        return {**request, key: target} if key else dict(request)

    def retry_delay(
        self,
        exchange: Exchange,
        result: ExchangeResult | None,
        error: Exception | None,
        attempt: int,
    ) -> float | None:
        """Seconds to wait before re-sending, or ``None`` not to retry. ``attempt`` is 1-based."""
        return None

    def serialize(self, exchange: Exchange, result: ExchangeResult) -> dict[str, Any]:
        """A JSON-friendly record of one exchange (cassettes)."""
        return {
            "adapter": exchange.adapter,
            "target": exchange.target,
            "request": exchange.request,
            "response": result.response,
            "meta": result.meta,
        }

    def deserialize(self, data: Mapping[str, Any]) -> tuple[Exchange, ExchangeResult]:
        return (
            Exchange(
                adapter=str(data["adapter"]),
                target=str(data["target"]),
                request=dict(data["request"]),
            ),
            ExchangeResult(response=data["response"], meta=dict(data.get("meta", {}))),
        )


__all__ = ["AdapterContext", "BaseAdapter"]

"""The exchange pipeline every adapter goes through (ARCHITECTURE §4.5, SPEC §13.3).

merge (config < defaults < args) → validate request (E403) → ``exchange.before`` →
permission guard (E604) → limits (E701) and rate limit → ``adapter.send`` (E602 for unexpected
failures; hops re-checked) → size cap (E606) → validate response (E407) → ``exchange.after`` →
adapter retry policy (``exchange.retry``) → response.
Errors fire ``exchange.error`` (retry, replace, abort).
"""

import contextlib
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from contextvars import ContextVar
from typing import Any

from crowley.application.adapters.base import AdapterContext, BaseAdapter
from crowley.application.adapters.guards import PermissionGuard, RateLimiter
from crowley.application.ports import CompiledSchema, HostResolver, SchemaValidator
from crowley.application.registry import Registry
from crowley.application.runtime.frame import Frame
from crowley.application.runtime.state import RunState
from crowley.domain.adapters import Exchange, ExchangeResult, Target, TargetKind
from crowley.domain.errors import (
    ControlError,
    CrowleyError,
    ExchangeError,
    LimitError,
    ValidationError,
)
from crowley.domain.events import (
    ExchangeAfter,
    ExchangeBefore,
    ExchangeErrorEvent,
    ExchangeRetry,
    Replace,
    Retry,
    Skip,
    StepInfo,
)
from crowley.domain.template import Permissions
from crowley.domain.values import Value

_NOT_RECOVERABLE = frozenset({"E604"})
"""Errors notifiers can't retry or replace: a permission denial must stay a denial."""

_ISOLATED: ContextVar[Mapping[tuple[int, str], Any] | None] = ContextVar(
    "crowley_isolated_sessions", default=None
)


class AdapterService:
    """One per process: resolves adapters, owns their sessions and runs every exchange."""

    def __init__(
        self,
        *,
        state: RunState,
        registry: Registry,
        schemas: SchemaValidator,
        permissions: Permissions | None = None,
        resolver: HostResolver | None = None,
        overrides: Mapping[str, BaseAdapter] | None = None,
    ) -> None:
        self._state = state
        self._registry = registry
        self._schemas = schemas
        self._overrides = dict(overrides or {})
        self._sessions: dict[str, Any] = {}
        self._opened: list[tuple[BaseAdapter, Any]] = []
        self._compiled: dict[tuple[str, str], CompiledSchema] = {}
        self.guard = PermissionGuard(permissions or Permissions(), resolver)
        self.limiter = RateLimiter(state.limits.rate_per_host, sleep=state.sleep)

    # ── adapters and sessions ──────────────────────────────────────────────────

    def adapter(self, name: str) -> BaseAdapter:
        adapter = self._overrides.get(name) or self._registry.adapter(name)
        if adapter is None:
            raise ExchangeError("E607", f"adapter {name!r} is not available")
        return adapter

    async def session(self, name: str) -> Any:
        """The session exchanges with ``name`` use: an isolated one if active, else the
        process session (opened on first use)."""
        isolated = _ISOLATED.get() or {}
        key = (id(self), name)
        if key in isolated:
            return isolated[key]
        if name not in self._sessions:
            adapter = self.adapter(name)
            session = await adapter.open(AdapterContext(run=self._state.run))
            self._sessions[name] = session
            self._opened.append((adapter, session))
        return self._sessions[name]

    @asynccontextmanager
    async def isolated(self, name: str, **seed: Any) -> AsyncIterator[Any]:
        """Run a block with a fresh session for ``name`` (e.g. ``http.session``)."""
        adapter = self.adapter(name)
        session = await adapter.open(AdapterContext(run=self._state.run, seed=seed))
        token = _ISOLATED.set({**(_ISOLATED.get() or {}), (id(self), name): session})
        try:
            yield session
        finally:
            _ISOLATED.reset(token)
            await adapter.close(session)

    async def close(self) -> None:
        """Close every session this process opened, newest first. Errors are swallowed."""
        opened, self._opened = self._opened, []
        self._sessions.clear()
        for adapter, session in reversed(opened):
            with contextlib.suppress(Exception):  # closing must not mask the run's outcome
                await adapter.close(session)

    # ── the pipeline ───────────────────────────────────────────────────────────

    async def exchange(
        self,
        name: str,
        request: Mapping[str, Any],
        *,
        options: Mapping[str, Any] | None = None,
        defaults: Mapping[str, Any] | None = None,
        step: StepInfo | None = None,
        frame: Frame | None = None,
        prev: Value = None,
    ) -> Any:
        """Run one exchange through the pipeline and return the adapter's response."""
        state = self._state
        adapter = self.adapter(name)
        path = step.path if step is not None else None
        merged = adapter.merge_request(adapter.request_defaults(), defaults or {}, request)
        self._check(adapter, "request", merged, path)
        max_bytes = state.limits.max_response_bytes
        exchange = Exchange(
            adapter=name,
            target=adapter.target_of(merged),
            request=merged,
            options=dict(options or {}),
            max_bytes=max_bytes.bytes if max_bytes is not None else None,
        )

        async def check_hop(uri: str) -> None:
            await self.guard.check(adapter, uri, path)

        exchange.check_hop = check_hop
        common: dict[str, Any] = {"step": step, "frame": frame, "scope_prev": prev, "adapter": name}
        retries = 0
        while True:
            attempt = retries + 1
            target = exchange.target
            before, outcome = await state.publish(ExchangeBefore, exchange=exchange, **common)
            exchange = before.exchange
            if exchange.target != target:
                exchange.request = adapter.with_target(exchange.request, exchange.target)
            exchange.target = adapter.target_of(exchange.request) or exchange.target
            if outcome.changed:
                self._check(adapter, "request", exchange.request, path)
            action = outcome.action
            try:
                if isinstance(action, Skip | Replace):
                    response = action.result if isinstance(action, Skip) else action.value
                    result = ExchangeResult(response=response, meta={"synthetic": True})
                else:
                    result = await self._send(adapter, exchange, path)
                self._check(adapter, "response", result.response, path)
            except CrowleyError as exc:
                exc.with_context(path=path)
                _, outcome = await state.publish(
                    ExchangeErrorEvent, exchange=exchange, error=exc, attempt=attempt, **common
                )
                if isinstance(exc, ControlError | LimitError) or exc.code in _NOT_RECOVERABLE:
                    raise
                action = outcome.action
                if isinstance(action, Retry) and retries < state.guard.max_retries:
                    retries += 1
                    state.stats.retries += 1
                    if action.after:
                        await state.sleep(action.after)
                    continue
                if isinstance(action, Replace):
                    self._check(adapter, "response", action.value, path)
                    return action.value
                delay = adapter.retry_delay(exchange, None, exc, attempt)
                if delay is not None and retries < state.guard.max_retries:
                    retries += 1
                    await self._wait(exchange, delay, attempt, common)
                    continue
                raise

            after, outcome = await state.publish(
                ExchangeAfter, exchange=exchange, result=result, **common
            )
            action = outcome.action
            if isinstance(action, Retry) and retries < state.guard.max_retries:
                retries += 1
                state.stats.retries += 1
                if action.after:
                    await state.sleep(action.after)
                continue
            if isinstance(action, Replace):
                self._check(adapter, "response", action.value, path)
                return action.value
            result = after.result
            if outcome.changed:
                self._check(adapter, "response", result.response, path)
            delay = adapter.retry_delay(exchange, result, None, attempt)
            if delay is not None and retries < state.guard.max_retries:
                retries += 1
                await self._wait(exchange, delay, attempt, common)
                continue
            return result.response

    async def _send(
        self, adapter: BaseAdapter, exchange: Exchange, path: str | None
    ) -> ExchangeResult:
        state = self._state
        session = await self.session(exchange.adapter)
        await self.guard.check(adapter, exchange.target, path)
        limit = state.limits.max_requests
        if limit is not None and state.stats.requests + 1 > limit:
            raise LimitError("E701", f"limits.max_requests of {limit} exceeded", path=path)
        state.stats.requests += 1
        key = exchange.adapter
        if adapter.target_kind is TargetKind.NETWORK:
            key = Target.parse(exchange.target).host
        await self.limiter.acquire(key)
        try:
            result = await adapter.send(session, exchange)
        except CrowleyError:
            raise
        except Exception as exc:  # unexpected adapter failures are transport errors
            raise ExchangeError(
                "E602",
                f"{exchange.adapter} exchange with {exchange.target} failed: "
                f"{type(exc).__name__}: {exc}",
                path=path,
                cause=exc,
            ) from exc
        for hop in result.hops:  # adapters should check hops before following them
            await self.guard.check(adapter, hop, path)
        state.stats.bytes_in += result.bytes_in
        if exchange.max_bytes is not None and result.bytes_in > exchange.max_bytes:
            raise ExchangeError(
                "E606",
                f"response of {result.bytes_in} bytes exceeds limits.max_response_bytes "
                f"({exchange.max_bytes})",
                path=path,
            )
        return result

    async def _wait(
        self, exchange: Exchange, delay: float, attempt: int, common: dict[str, Any]
    ) -> None:
        state = self._state
        state.stats.retries += 1
        event, _ = await state.publish(
            ExchangeRetry, exchange=exchange, delay=delay, attempt=attempt, **common
        )
        if event.delay > 0:
            await state.sleep(event.delay)

    def _check(self, adapter: BaseAdapter, kind: str, value: Any, path: str | None) -> None:
        key = (adapter.name, kind)
        compiled = self._compiled.get(key)
        if compiled is None:
            schema = adapter.request_schema if kind == "request" else adapter.response_schema
            compiled = self._schemas.compile(schema)
            self._compiled[key] = compiled
        for violation in compiled.validate(value):
            where = str(violation.path)
            suffix = "" if where == "<root>" else f" at {where}"
            raise ValidationError(
                "E403" if kind == "request" else "E407",
                f"invalid {adapter.name} {kind}{suffix}: {violation.message}",
                path=path,
            )


__all__ = ["AdapterService"]

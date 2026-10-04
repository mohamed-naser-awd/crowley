"""Per-run state shared by the executor, the step pipeline and function invocation."""

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from types import MappingProxyType
from typing import Any, TypeVar

from crowley.application.events import EventBus, Interception
from crowley.application.runtime.frame import Frame
from crowley.application.runtime.limits import LimitsGuard
from crowley.application.runtime.output import OutputCollector
from crowley.domain.events import Event, RunInfo, StepInfo
from crowley.domain.expressions import EvalEnv
from crowley.domain.services import Clock
from crowley.domain.template import Limits
from crowley.domain.values import Value

E = TypeVar("E", bound=Event)

ItemValidator = Callable[[Value, str], None]
"""Validates one emitted item; raises E404. Arguments: item, step path."""

_EMPTY: Mapping[str, Value] = MappingProxyType({})
NOTHING = Interception()


@dataclass(slots=True, kw_only=True)
class RunStats:
    """Counters reported in ``RunResult.stats`` (SPEC §20)."""

    requests: int = 0
    bytes_in: int = 0
    items: int = 0
    duration_ms: int = 0
    retries: int = 0
    errors_caught: int = 0

    def as_dict(self) -> dict[str, int]:
        return {
            "requests": self.requests,
            "bytes_in": self.bytes_in,
            "items": self.items,
            "duration_ms": self.duration_ms,
            "retries": self.retries,
            "errors_caught": self.errors_caught,
        }


class RunState:
    """Everything one process run owns: bus, limits, counters, output and services."""

    def __init__(
        self,
        *,
        run: RunInfo,
        bus: EventBus,
        limits: Limits | None = None,
        env: EvalEnv | None = None,
        clock: Clock | None = None,
        validate_item: ItemValidator | None = None,
        streaming: bool = False,
        sleep: Callable[[float], Awaitable[Any]] = asyncio.sleep,
    ) -> None:
        self.run = run
        self.bus = bus
        self.guard = LimitsGuard(limits or Limits())
        self.env = env or EvalEnv()
        self.clock = clock
        self.validate_item = validate_item
        self.output = OutputCollector(streaming=streaming)
        self.stats = RunStats()
        self.sleep = sleep

    @property
    def limits(self) -> Limits:
        return self.guard.limits

    def now(self) -> datetime:
        return self.clock.now() if self.clock is not None else datetime.now(UTC)

    async def publish(
        self,
        cls: type[E],
        *,
        step: StepInfo | None = None,
        frame: Frame | None = None,
        scope_prev: Value = None,
        **fields: Any,
    ) -> tuple[E, Interception]:
        """Build an event with the common payload (SPEC §17.2) and dispatch it.

        The event object is always returned so callers can read back mutable fields. The scope
        view, the costly part, is only built when some notifier listens to this event.
        """
        listened = self.bus.has_listeners(cls.NAME)
        event = cls(
            timestamp=self.now(),
            run=self.run,
            step=step,
            depth=frame.depth if frame is not None else 0,
            iteration=frame.iteration if frame is not None else None,
            scope=MappingProxyType(frame.scope(scope_prev)) if listened and frame else _EMPTY,
            **fields,
        )
        if not listened:
            return event, NOTHING
        return event, await self.bus.publish(event)

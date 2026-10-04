"""Processes: one run of one operation, with its own notifiers, state and cancellation (§17.3)."""

import asyncio
import uuid
from collections.abc import AsyncIterator, Coroutine, Iterable
from enum import Enum
from typing import Any, TypeVar

from crowley.application.events import (
    EventBus,
    Notifier,
    NotifierRegistry,
    NotifierScope,
    NotifierSet,
)
from crowley.application.runtime import OutputCollector, RunState
from crowley.application.use_cases.run import PreparedRun, RunOperation, RunResult
from crowley.domain.errors import ConfigurationError, ControlError
from crowley.domain.template import Operation, OutputMode, Template
from crowley.domain.values import Value

T = TypeVar("T")

ProcessNotifiers = NotifierRegistry | Iterable[Notifier | NotifierRegistry]


class ProcessStatus(Enum):
    CREATED = "created"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class Process(NotifierScope):
    """Created by ``Crowley.init``; inputs and secrets are already validated. Runs once."""

    def __init__(
        self,
        *,
        runner: RunOperation,
        prepared: PreparedRun,
        global_notifiers: NotifierSet,
        strict_observers: bool = False,
        notifiers: ProcessNotifiers | None = None,
    ) -> None:
        self.id = uuid.uuid4().hex
        self._runner = runner
        self._prepared = prepared
        self._notifier_set = NotifierSet("process")
        self._bus = EventBus(
            global_notifiers, self._notifier_set, strict_observers=strict_observers
        )
        self._status = ProcessStatus.CREATED
        self._task: asyncio.Task[RunResult] | None = None
        self._state: RunState | None = None
        self._cancel_requested = False
        if notifiers is not None:
            attach(self._notifier_set, notifiers)

    # ── info ───────────────────────────────────────────────────────────────────

    @property
    def template(self) -> Template:
        return self._prepared.template

    @property
    def operation(self) -> Operation:
        return self._prepared.operation

    @property
    def inputs(self) -> dict[str, Value]:
        return dict(self._prepared.inputs)

    @property
    def status(self) -> str:
        return self._status.value

    def __repr__(self) -> str:
        return f"<Process {self.id[:8]} {self.template.id}#{self.operation.name} {self.status}>"

    # ── running ────────────────────────────────────────────────────────────────

    async def run(self) -> RunResult:
        """Run to completion and return the result."""
        self.start()
        return await self.result()

    def start(self) -> None:
        """Start running in the background (inside a running event loop)."""
        self._start(OutputCollector())

    async def result(self) -> RunResult:
        """Wait for a started process and return its result."""
        if self._task is None:
            raise ConfigurationError("E902", "the process has not been started")
        return await self._task

    def run_sync(self) -> RunResult:
        """Synchronous ``run()``; not usable inside a running event loop (E902)."""
        return run_sync(self.run())

    async def stream(self) -> AsyncIterator[Value]:
        """Yield emitted items as they are produced. For value and pipe operations, the output."""
        collector = OutputCollector(streaming=True)
        task = self._start(collector)
        emit_mode = self._prepared.operation.output.mode is OutputMode.EMIT
        try:
            while True:
                more, item = await collector.next()
                if not more:
                    break
                yield item
            result = await task
            if not emit_mode:
                yield result.output
        finally:
            if not task.done():
                self.cancel()
                await asyncio.gather(task, return_exceptions=True)

    def cancel(self) -> None:
        """Cancel the run. The run fails with E803 and adapter sessions are still closed."""
        self._cancel_requested = True
        if self._state is not None:
            self._state.guard.cancel_requested = True
        if self._task is not None and not self._task.done():
            self._task.cancel()

    def _start(self, collector: OutputCollector) -> "asyncio.Task[RunResult]":
        if self._status is not ProcessStatus.CREATED:
            raise ConfigurationError(
                "E905",
                f"process {self.id[:8]} was already started; a process runs once",
                hint="create a new process with crowley.init(...)",
            )
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            raise ConfigurationError(
                "E902", "process.start() needs a running event loop; use run_sync() instead"
            ) from None
        self._status = ProcessStatus.RUNNING
        self._task = loop.create_task(self._execute(collector))
        return self._task

    async def _execute(self, collector: OutputCollector) -> RunResult:
        def keep(state: RunState) -> None:
            self._state = state
            state.guard.cancel_requested = self._cancel_requested

        try:
            result = await self._runner.execute(
                self._prepared, run_id=self.id, bus=self._bus, output=collector, on_state=keep
            )
        except asyncio.CancelledError:
            self._status = ProcessStatus.CANCELLED
            if self._cancel_requested:
                raise ControlError("E803", "the run was cancelled by the caller") from None
            raise
        except ControlError as exc:
            cancelled = exc.code == "E803"
            self._status = ProcessStatus.CANCELLED if cancelled else ProcessStatus.FAILED
            raise
        except BaseException:
            self._status = ProcessStatus.FAILED
            raise
        self._status = ProcessStatus.SUCCEEDED
        return result


def attach(target: NotifierSet, notifiers: ProcessNotifiers) -> None:
    """Attach ``notifiers=`` given to ``init``/``run``/``stream``."""
    items = [notifiers] if isinstance(notifiers, NotifierRegistry) else list(notifiers)
    for item in items:
        if isinstance(item, NotifierRegistry):
            target.add_notifier_registry(item)
        elif isinstance(item, Notifier):
            target.add(item)
        else:
            raise TypeError(f"expected a Notifier or NotifierRegistry, got {item!r}")


def run_sync(coroutine: Coroutine[Any, Any, T]) -> T:
    """Run ``coroutine`` with ``asyncio.run``; E902 when an event loop is already running."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coroutine)
    coroutine.close()
    raise ConfigurationError(
        "E902",
        "the synchronous API cannot be used inside a running event loop",
        hint="use the async variant (await ...) instead",
    )

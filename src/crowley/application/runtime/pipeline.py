"""The step pipeline: the only code path that runs a step (ARCHITECTURE §4.4, SPEC §17).

Every step of every kind goes through :meth:`StepPipeline.run`, which fires ``step.before`` and
then ``step.after``, ``step.error`` or ``step.skipped``, applies notifier actions and the step's
``on_error`` policy, enforces ``timeout``, and decides the outgoing ``prev`` (SPEC §8.4).
"""

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

from crowley.application.runtime.evaluation import evaluate_expr, evaluate_node
from crowley.application.runtime.frame import Frame
from crowley.application.runtime.signals import HandlerResult, Return, StepOutcome
from crowley.application.runtime.state import RunState
from crowley.domain.errors import (
    ConfigurationError,
    ControlError,
    CrowleyError,
    ExecutionError,
    LimitError,
)
from crowley.domain.events import (
    ConditionEvaluated,
    Replace,
    Retry,
    Skip,
    StepAfter,
    StepBefore,
    StepErrorEvent,
    StepInfo,
    StepSkipped,
)
from crowley.domain.steps import ErrorPolicy, Expr, RetrySpec, Step, UseDefault, UseStep
from crowley.domain.values import Value, type_of

StepHandler = Callable[[Any, Frame, Value, dict[str, Value] | None], Awaitable[HandlerResult]]
"""``handler(step, frame, prev, kwargs) -> HandlerResult``."""

Prepare = Callable[[Any, Frame, Value], Awaitable[dict[str, Value] | None]]
"""Computes a step's ``kwargs`` (evaluated ``with:`` args) before ``step.before``."""

Revalidate = Callable[[Any, Value], None]
"""Validates a changed step result (function calls: against the output schema, E407)."""

RESULT_KINDS = frozenset({"use", "for_each", "while"})
"""Step kinds whose result is stored as ``steps.<id>`` (SPEC §8.2)."""


def step_info(step: Step) -> StepInfo:
    return StepInfo(
        id=step.id,
        kind=step.kind,
        path=str(step.path),
        function=step.uses if isinstance(step, UseStep) else None,
    )


def retry_delay(spec: RetrySpec, attempt: int) -> float:
    """Delay before retry number ``attempt`` (1-based)."""
    if spec.backoff == "fixed":
        return spec.delay.seconds
    return min(spec.delay.seconds * 2.0 ** (attempt - 1), spec.max_delay.seconds)


def _handles(policy: ErrorPolicy, error: CrowleyError) -> bool:
    return error.catchable and (not policy.catch or error.code in policy.catch)


class StepPipeline:
    def __init__(self, state: RunState) -> None:
        self._state = state
        self._handlers: dict[str, StepHandler] = {}
        self._prepare: dict[str, Prepare] = {}
        self._revalidate: dict[str, Revalidate] = {}

    def register(
        self,
        kind: str,
        handler: StepHandler,
        *,
        prepare: Prepare | None = None,
        revalidate: Revalidate | None = None,
    ) -> None:
        """Install the handler of one step kind. This is the only way to run step code."""
        self._handlers[kind] = handler
        if prepare is not None:
            self._prepare[kind] = prepare
        if revalidate is not None:
            self._revalidate[kind] = revalidate

    # ── conditions ─────────────────────────────────────────────────────────────

    async def condition(self, expr: Expr, info: StepInfo, frame: Frame, prev: Value) -> bool:
        """Evaluate a condition, fire ``condition.evaluated``; the value must stay a bool."""
        state = self._state
        value = evaluate_expr(expr, frame.scope(prev), state.env)
        if not isinstance(value, bool):
            raise ExecutionError(
                "E501",
                f"condition must be a bool, got {type_of(value)}",
                path=str(expr.path),
                location=expr.location,
                value=value,
            )
        event, _ = await state.publish(
            ConditionEvaluated, step=info, frame=frame, scope_prev=prev, value=value
        )
        if not isinstance(event.value, bool):
            raise ExecutionError(
                "E501",
                f"a notifier changed the condition to {type_of(event.value)}; it must stay a bool",
                path=str(expr.path),
                value=event.value,
            )
        return event.value

    # ── the pipeline ───────────────────────────────────────────────────────────

    async def run(self, step: Step, frame: Frame, prev: Value) -> StepOutcome:
        state = self._state
        info = step_info(step)
        state.guard.check_step(frame.depth, info.path)
        if step.when is not None and not await self.condition(step.when, info, frame, prev):
            await state.publish(StepBefore, step=info, frame=frame, scope_prev=prev, prev=prev)
            await state.publish(StepSkipped, step=info, frame=frame, scope_prev=prev)
            return self._finish(step, frame, None, prev)

        retries = 0  # every retry source counts toward max_retries_per_step
        policy_retries = 0
        while True:
            try:
                return await self._attempt(step, info, frame, prev)
            except CrowleyError as exc:
                exc.with_context(path=info.path, location=step.location)
                _, outcome = await state.publish(
                    StepErrorEvent,
                    step=info,
                    frame=frame,
                    scope_prev=prev,
                    error=exc,
                    attempt=retries + 1,
                )
                action = outcome.action
                if isinstance(exc, ControlError | LimitError):
                    raise  # aborts, cancellation and limits can't be retried or replaced
                if isinstance(action, Retry) and retries < state.guard.max_retries:
                    retries += 1
                    state.stats.retries += 1
                    if action.after:
                        await state.sleep(action.after)
                    continue
                if isinstance(action, Replace):
                    return self._finish(step, frame, action.value, action.value)
                policy = step.on_error
                if policy is None or not _handles(policy, exc):
                    raise
                if (
                    policy.retry is not None
                    and policy_retries < policy.retry.times
                    and retries < state.guard.max_retries
                ):
                    policy_retries += 1
                    retries += 1
                    state.stats.retries += 1
                    await state.sleep(retry_delay(policy.retry, policy_retries))
                    continue
                if policy.outcome == "fail":
                    raise
                state.stats.errors_caught += 1
                return self._recover(step, frame, prev, policy, exc)

    async def _attempt(self, step: Step, info: StepInfo, frame: Frame, prev: Value) -> StepOutcome:
        state = self._state
        prepare = self._prepare.get(step.kind)
        kwargs = await prepare(step, frame, prev) if prepare is not None else None
        before, outcome = await state.publish(
            StepBefore, step=info, frame=frame, scope_prev=prev, prev=prev, kwargs=kwargs
        )
        prev_in = before.prev
        action = outcome.action
        if isinstance(action, Skip):
            await state.publish(StepSkipped, step=info, frame=frame, scope_prev=prev_in)
            return self._finish(
                step, frame, action.result, prev_in if action.result is None else action.result
            )
        if isinstance(action, Replace):
            result = HandlerResult(value=action.value)
        else:
            result = await self._call(step, frame, prev_in, before.kwargs)

        value = prev_in if result.passthrough else result.value
        after, outcome = await state.publish(
            StepAfter, step=info, frame=frame, scope_prev=prev_in, result=value
        )
        value = outcome.action.value if isinstance(outcome.action, Replace) else after.result
        revalidate = self._revalidate.get(step.kind)
        if outcome.changed and revalidate is not None and result.signal is None:
            revalidate(step, value)
        if isinstance(result.signal, Return):
            return StepOutcome(prev_in, Return(value))
        if result.signal is not None:
            return StepOutcome(prev_in, result.signal)
        return self._finish(step, frame, value, value)

    async def _call(
        self, step: Step, frame: Frame, prev: Value, kwargs: dict[str, Value] | None
    ) -> HandlerResult:
        handler = self._handlers.get(step.kind)
        if handler is None:
            raise ConfigurationError("E902", f"no handler is registered for {step.kind!r} steps")
        if step.timeout is None:
            return await handler(step, frame, prev, kwargs)
        timeout = asyncio.timeout(step.timeout.seconds)
        try:
            async with timeout:
                return await handler(step, frame, prev, kwargs)
        except TimeoutError:
            if not timeout.expired():
                raise
            raise ExecutionError(
                "E504",
                f"step {step.label!r} timed out after {step.timeout}",
                path=str(step.path),
                location=step.location,
            ) from None

    def _recover(
        self, step: Step, frame: Frame, prev: Value, policy: ErrorPolicy, error: CrowleyError
    ) -> StepOutcome:
        """Apply the ``skip`` or ``default`` outcome of an ``on_error`` policy."""
        if not isinstance(policy.outcome, UseDefault):
            return self._finish(step, frame, None, None)
        scope = frame.scope(prev, {"error": error.as_value()})
        value = evaluate_node(policy.outcome.value, scope, self._state.env)
        revalidate = self._revalidate.get(step.kind)
        if revalidate is not None:
            revalidate(step, value)
        return self._finish(step, frame, value, value)

    @staticmethod
    def _finish(step: Step, frame: Frame, result: Value, prev_out: Value) -> StepOutcome:
        if step.id is not None and step.kind in RESULT_KINDS:
            frame.record(step.id, result)
        return StepOutcome(prev_out)

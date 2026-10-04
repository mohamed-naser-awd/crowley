"""``control.*``: retry, sleep and parallel (SPEC §12.3)."""

import asyncio
from collections.abc import Callable
from typing import Any

from crowley.application.runtime import FunctionContext
from crowley.domain.adapters import RetryPolicy
from crowley.domain.common import Duration
from crowley.domain.errors import CrowleyError, ExecutionError
from crowley.domain.functions import Outcome
from crowley.domain.values import Value, type_of


async def retry(
    ctx: FunctionContext,
    times: int,
    backoff: str = "exponential",
    delay: Any = "1s",
    max_delay: Any = "30s",
    until: Any = None,
    **kwargs: Any,
) -> Value:
    """Run the body; re-run it on catchable errors, or while ``until`` is false."""
    assert ctx.body is not None
    policy = RetryPolicy(
        times=times,
        backoff="fixed" if backoff == "fixed" else "exponential",
        delay=Duration.parse(delay),
        max_delay=Duration.parse(max_delay),
    )
    for attempt in range(times + 1):
        try:
            outcome = await ctx.body.run()
        except CrowleyError as exc:
            if not exc.catchable or attempt == times:
                raise
            await ctx.sleep(policy.delay_for(attempt + 1))
            continue
        value = outcome.value if outcome.has_value else None
        if outcome.is_break or until is None:
            return value
        done = ctx.evaluate(until, {"result": value})
        if not isinstance(done, bool):
            raise ExecutionError(
                "E501", f"until must be a bool, got {type_of(done)}", path=ctx.step.path
            )
        if done:
            return value
        if attempt < times:
            await ctx.sleep(policy.delay_for(attempt + 1))
    raise ExecutionError(
        "E502",
        f"control.retry: until was still false after {times + 1} attempts",
        path=ctx.step.path,
    )


async def sleep(ctx: FunctionContext, duration: Any, prev: Value = None, **kwargs: Any) -> Value:
    """Wait, then pass ``prev`` through unchanged."""
    await ctx.sleep(Duration.parse(duration).seconds)
    return prev


async def parallel(ctx: FunctionContext, branches: int, **kwargs: Any) -> list[Value]:
    """Run the body ``branches`` times concurrently, bounded by ``limits.max_concurrency``."""
    body = ctx.body
    assert body is not None
    gate = asyncio.Semaphore(ctx.limits.max_concurrency or branches)

    async def branch(index: int) -> Outcome:
        async with gate:
            return await body.run(bindings={"branch": {"index": index}})

    try:
        async with asyncio.TaskGroup() as group:
            tasks = [group.create_task(branch(i)) for i in range(branches)]
    except ExceptionGroup as failed:
        errors = [e for e in failed.exceptions if isinstance(e, CrowleyError)]
        if not errors:
            raise
        first = errors[0]
        first.related = (*first.related, *errors[1:])
        raise first from None
    return [t.result().value for t in tasks if t.result().has_value]


HANDLERS: dict[str, Callable[..., Any]] = {
    "control.retry": retry,
    "control.sleep": sleep,
    "control.parallel": parallel,
}

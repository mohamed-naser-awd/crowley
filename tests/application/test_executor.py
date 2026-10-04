"""Executor and step pipeline: prev piping, scopes, control flow, events, on_error, limits."""

import asyncio
from typing import Any

import pytest

from crowley.application.events import NotifierSet
from crowley.application.runtime import retry_delay
from crowley.domain.common import Duration, SemVer
from crowley.domain.errors import (
    ConfigurationError,
    ControlError,
    CrowleyError,
    ExecutionError,
    LimitError,
    ValidationError,
)
from crowley.domain.functions import FunctionSpec
from crowley.domain.steps import RetrySpec
from crowley.domain.template import Limits
from tests.application.runtime_support import execute, operation

ARRAY = "schema: { type: array }"


def recorder(notifiers: NotifierSet, pattern: str = "*") -> list[tuple[str, str | None]]:
    seen: list[tuple[str, str | None]] = []
    notifiers.add_notifier(
        pattern, lambda e: seen.append((e.name, e.step.id if e.step else None)), observe=True
    )
    return seen


# ── prev piping (SPEC §8.4) ──────────────────────────────────────────────────


async def test_initial_prev_is_inputs_and_passthrough_steps_keep_it() -> None:
    tpl = operation(
        """
        - set: { a: 1 }
        - log: hello
        - assert: ${{ true }}
        """
    )
    run = await execute(tpl, inputs={"company": "acme"})
    assert run.prev == {"company": "acme"}
    assert run.frame.vars == {"a": 1}


async def test_for_each_result_and_item_is_first_prev() -> None:
    tpl = operation(
        """
        - id: doubled
          for_each: ${{ [1, 2, 3] }}
          do:
            - set: { seen: true }
            - return: ${{ prev * 2 }}
        - id: echoed
          for_each: ${{ ['a', 'b'] }}
          do:
            - log: ${{ item }}
        """
    )
    run = await execute(tpl)
    assert run.frame.steps == {"doubled": [2, 4, 6], "echoed": ["a", "b"]}
    assert run.prev == ["a", "b"]


async def test_loop_bindings() -> None:
    tpl = operation(
        """
        - for_each: ${{ ['x', 'y'] }}
          as: letter
          index_as: i
          do:
            - return: ${{ [letter, i, loop.index, loop.first, loop.last, loop.length] }}
        """
    )
    run = await execute(tpl)
    assert run.prev == [["x", 0, 0, True, False, 2], ["y", 1, 1, False, True, 2]]


async def test_break_and_continue() -> None:
    tpl = operation(
        """
        - for_each: ${{ [1, 2, 3, 4, 5] }}
          do:
            - if: ${{ item == 2 }}
              then:
                - continue: true
            - if: ${{ item == 4 }}
              then:
                - break: true
            - return: ${{ item }}
        """
    )
    assert (await execute(tpl)).prev == [1, 3]


async def test_if_passes_branch_output_or_incoming_prev() -> None:
    tpl = operation(
        """
        - id: ran
          for_each: ${{ [1] }}
          do:
            - if: ${{ true }}
              then:
                - for_each: ${{ [7] }}
                  do:
                    - return: ${{ item }}
              else:
                - log: never
        - id: kept
          for_each: ${{ ['in'] }}
          do:
            - if: ${{ false }}
              then:
                - log: never
        """
    )
    run = await execute(tpl)
    assert run.frame.steps == {"ran": [[7]], "kept": ["in"]}


async def test_when_false_skips_and_passes_prev_through() -> None:
    notifiers = NotifierSet("global")
    seen = recorder(notifiers, "step.*")
    tpl = operation(
        """
        - id: skipped
          when: ${{ false }}
          for_each: ${{ [1] }}
          do:
            - log: never
        """
    )
    run = await execute(tpl, inputs={"x": 1}, notifiers=notifiers)
    assert run.prev == {"x": 1}
    assert run.frame.steps == {"skipped": None}
    assert seen == [("step.before", "skipped"), ("step.skipped", "skipped")]


async def test_while_pipes_previous_iteration_value() -> None:
    tpl = operation(
        """
        - for_each: ${{ [0] }}
          do:
            - return: ${{ 0 }}
        - id: grown
          while: ${{ len(prev) < 4 }}
          max_iterations: 10
          do:
            - return: ${{ prev + [len(prev)] }}
        """
    )
    run = await execute(tpl)
    assert run.frame.steps["grown"] == [[0, 1], [0, 1, 2], [0, 1, 2, 3]]


async def test_while_with_outer_variable() -> None:
    tpl = operation(
        """
        - set: { n: 0 }
        - id: counted
          while: ${{ vars.n < 3 }}
          max_iterations: 5
          do:
            - set: { n: "${{ vars.n + 1 }}" }
            - return: ${{ vars.n * 10 }}
        """
    )
    run = await execute(tpl)
    assert run.frame.steps["counted"] == [10, 20, 30]
    assert run.frame.vars == {"n": 3}


async def test_while_max_iterations_is_e702() -> None:
    tpl = operation(
        """
        - while: ${{ true }}
          max_iterations: 2
          do:
            - log: spin
        """
    )
    with pytest.raises(LimitError) as info:
        await execute(tpl)
    assert info.value.code == "E702"


async def test_set_scoping() -> None:
    tpl = operation(
        """
        - set: { a: 1 }
        - if: ${{ true }}
          then:
            - set: { a: 2, b: "${{ vars.a + 1 }}" }
        """
    )
    run = await execute(tpl)
    assert run.frame.vars == {"a": 2}


async def test_spec_only_functions_cannot_run() -> None:
    declared = FunctionSpec(name="acme.declared", version=SemVer.parse("1.0.0"))
    tpl = operation("- use: acme.declared", extra='requires: ["acme.*@^1"]', functions=[declared])
    with pytest.raises(ConfigurationError) as info:
        await execute(tpl, functions=[declared])
    assert info.value.code == "E903"


# ── emit ─────────────────────────────────────────────────────────────────────


async def test_emit_collects_and_validates_items() -> None:
    tpl = operation(
        """
        - for_each: ${{ [1, 2, 3] }}
          do:
            - emit: ${{ item }}
        """,
        output=ARRAY,
    )
    checked: list[Any] = []
    run = await execute(tpl, validate_item=lambda item, path: checked.append((item, path)))
    assert run.items == [1, 2, 3]
    assert run.state.stats.items == 3
    assert checked[0] == (1, "operations.op.steps[0].do[0]")
    assert run.prev == [1, 2, 3]  # emit passes the item's prev (the item) through


async def test_max_items_is_e701() -> None:
    tpl = operation(
        """
        - for_each: ${{ [1, 2, 3] }}
          do:
            - emit: ${{ item }}
        """,
        output=ARRAY,
    )
    with pytest.raises(LimitError) as info:
        await execute(tpl, limits=Limits(max_items=2))
    assert info.value.code == "E701"


# ── fail, assert, log ────────────────────────────────────────────────────────


async def test_fail_is_e503_with_user_code() -> None:
    tpl = operation(
        """
        - fail: "${{ 'login rejected: ' + 'nope' }}"
          code: login_rejected
        """
    )
    with pytest.raises(ExecutionError) as info:
        await execute(tpl)
    assert info.value.code == "E503"
    assert info.value.message == "login rejected: nope"
    assert info.value.user_code == "login_rejected"
    assert info.value.path == "operations.op.steps[0]"


async def test_assert() -> None:
    passing = operation("- assert: ${{ 1 < 2 }}")
    assert (await execute(passing, inputs={"k": 1})).prev == {"k": 1}
    failing = operation(
        """
        - assert: ${{ 1 > 2 }}
        - assert: ${{ false }}
          message: custom
        """
    )
    with pytest.raises(ValidationError) as info:
        await execute(failing)
    assert info.value.code == "E406"
    assert info.value.message == "assertion failed: 1 > 2"
    custom = operation(
        """
        - assert: ${{ false }}
          message: ${{ 'custom ' + 'message' }}
        """
    )
    with pytest.raises(ValidationError, match="custom message"):
        await execute(custom)
    typed = operation("- assert: ${{ 'yes' }}")
    with pytest.raises(ExecutionError, match="must be a bool"):
        await execute(typed)


async def test_log_fires_log_event() -> None:
    notifiers = NotifierSet("global")
    logs: list[tuple[str, str]] = []
    notifiers.add_notifier("log", lambda e: logs.append((e.level, e.message)))
    tpl = operation(
        """
        - log: ${{ 'n=' + str(2) }}
          level: warning
        """
    )
    await execute(tpl, notifiers=notifiers)
    assert logs == [("warning", "n=2")]


async def test_condition_must_be_bool() -> None:
    tpl = operation(
        """
        - if: ${{ 1 }}
          then:
            - log: x
        """
    )
    with pytest.raises(ExecutionError) as info:
        await execute(tpl)
    assert info.value.code == "E501"
    assert info.value.path == "operations.op.steps[0].if"


async def test_for_each_needs_a_list_and_positive_concurrency() -> None:
    tpl = operation(
        """
        - for_each: ${{ 'abc' }}
          do:
            - log: x
        """
    )
    with pytest.raises(ExecutionError, match="must be a list"):
        await execute(tpl)
    tpl = operation(
        """
        - for_each: ${{ [1] }}
          concurrency: ${{ 0 }}
          do:
            - log: x
        """
    )
    with pytest.raises(ExecutionError, match="concurrency"):
        await execute(tpl)
    tpl = operation(
        """
        - for_each: ${{ [1, 2] }}
          concurrency: 2
          do:
            - return: ${{ item }}
        """
    )
    assert (await execute(tpl)).prev == [1, 2]


# ── notifier actions in the pipeline ─────────────────────────────────────────


async def test_step_events_fire_before_and_after_at_every_depth() -> None:
    notifiers = NotifierSet("global")
    seen = recorder(notifiers, "step.*")
    tpl = operation(
        """
        - id: outer
          for_each: ${{ [1] }}
          do:
            - if: ${{ true }}
              id: branch
              then:
                - log: inner
                  id: deep
        """
    )
    await execute(tpl, notifiers=notifiers)
    assert seen == [
        ("step.before", "outer"),
        ("step.before", "branch"),
        ("step.before", "deep"),
        ("step.after", "deep"),
        ("step.after", "branch"),
        ("step.after", "outer"),
    ]


async def test_step_before_changes_prev_and_after_replaces_result() -> None:
    notifiers = NotifierSet("global")
    notifiers.add_notifier("step.before", lambda e: setattr(e, "prev", 10), step="double")
    notifiers.add_notifier("step.after", lambda e: e.replace("patched"), step="tail")
    tpl = operation(
        """
        - id: double
          for_each: ${{ [prev] }}
          do:
            - return: ${{ item * 2 }}
        - id: tail
          for_each: ${{ prev }}
          do:
            - return: ${{ item }}
        """
    )
    run = await execute(tpl, notifiers=notifiers)
    assert run.frame.steps == {"double": [20], "tail": "patched"}
    assert run.prev == "patched"


async def test_step_before_skip_and_replace() -> None:
    notifiers = NotifierSet("global")
    notifiers.add_notifier("step.before", lambda e: e.skip(), step="a")
    notifiers.add_notifier("step.before", lambda e: e.skip(["given"]), step="b")
    notifiers.add_notifier("step.before", lambda e: e.replace(["swapped"]), step="c")
    seen = recorder(notifiers, "step.*")
    tpl = operation(
        """
        - id: a
          for_each: ${{ [1] }}
          do:
            - fail: should not run
        - id: b
          for_each: ${{ [1] }}
          do:
            - fail: should not run
        - id: c
          for_each: ${{ [1] }}
          do:
            - fail: should not run
        """
    )
    run = await execute(tpl, inputs={"in": True}, notifiers=notifiers)
    assert run.frame.steps == {"a": None, "b": ["given"], "c": ["swapped"]}
    assert run.prev == ["swapped"]
    assert ("step.skipped", "b") in seen
    assert ("step.after", "c") in seen


async def test_return_value_can_be_replaced() -> None:
    notifiers = NotifierSet("global")
    notifiers.add_notifier("step.after", lambda e: e.replace("new"), step="ret")
    tpl = operation(
        """
        - for_each: ${{ [1] }}
          do:
            - id: ret
              return: old
        """
    )
    assert (await execute(tpl, notifiers=notifiers)).prev == ["new"]


async def test_condition_can_be_flipped_but_must_stay_bool() -> None:
    notifiers = NotifierSet("global")
    notifiers.add_notifier("condition.evaluated", lambda e: setattr(e, "value", True))
    tpl = operation(
        """
        - id: picked
          for_each: ${{ [1] }}
          do:
            - if: ${{ false }}
              then:
                - return: then
        """
    )
    assert (await execute(tpl, notifiers=notifiers)).frame.steps["picked"] == ["then"]
    notifiers.clear()
    notifiers.add_notifier("condition.evaluated", lambda e: setattr(e, "value", "yes"))
    with pytest.raises(ExecutionError, match="must stay a bool"):
        await execute(tpl, notifiers=notifiers)


async def test_loop_events() -> None:
    notifiers = NotifierSet("global")
    notifiers.add_notifier("loop.start", lambda e: e.items.append(4))
    notifiers.add_notifier("loop.iteration.before", lambda e: e.skip() if e.binding == 2 else None)
    notifiers.add_notifier("loop.iteration.before", lambda e: setattr(e, "binding", e.binding * 10))
    notifiers.add_notifier("loop.iteration.after", lambda e: setattr(e, "stop", e.value == 30))
    notifiers.add_notifier("loop.end", lambda e: e.result.append("end"))
    tpl = operation(
        """
        - for_each: ${{ [1, 2, 3] }}
          do:
            - return: ${{ item }}
        """
    )
    assert (await execute(tpl, notifiers=notifiers)).prev == [10, 30, "end"]


async def test_while_iteration_skip_and_stop() -> None:
    notifiers = NotifierSet("global")
    notifiers.add_notifier(
        "loop.iteration.before", lambda e: e.skip() if e.iteration == 0 else None
    )
    notifiers.add_notifier("loop.iteration.after", lambda e: setattr(e, "stop", True))
    tpl = operation(
        """
        - set: { n: 0 }
        - while: ${{ vars.n < 5 }}
          max_iterations: 10
          do:
            - set: { n: "${{ vars.n + 1 }}" }
            - return: ${{ vars.n }}
        """
    )
    assert (await execute(tpl, notifiers=notifiers)).prev == [1]


async def test_item_emit_and_variable_set_notifiers() -> None:
    notifiers = NotifierSet("global")
    notifiers.add_notifier("item.emit", lambda e: e.skip() if e.item == 2 else None)
    notifiers.add_notifier("variable.set", lambda e: setattr(e, "value", e.value * 100))
    tpl = operation(
        """
        - set: { x: 1 }
        - for_each: ${{ [1, 2, 3] }}
          do:
            - emit: ${{ item }}
        """,
        output=ARRAY,
    )
    run = await execute(tpl, notifiers=notifiers)
    assert run.items == [1, 3]
    assert run.frame.vars == {"x": 100}


async def test_abort_is_e801_and_not_recoverable() -> None:
    notifiers = NotifierSet("global")
    notifiers.add_notifier("step.before", lambda e: e.abort("stop"), step="target")
    notifiers.add_notifier("step.error", lambda e: e.replace("ignored"))
    tpl = operation(
        """
        - id: target
          log: x
          on_error: skip
        """
    )
    with pytest.raises(ControlError) as info:
        await execute(tpl, notifiers=notifiers)
    assert info.value.code == "E801"


# ── errors: step.error actions and on_error ──────────────────────────────────


def count_attempts(notifiers: NotifierSet, step: str) -> list[int]:
    attempts: list[int] = []
    notifiers.add_notifier("step.before", lambda e: attempts.append(1), step=step)
    return attempts


async def test_step_error_retry_is_capped_and_replace_recovers() -> None:
    notifiers = NotifierSet("global")
    attempts = count_attempts(notifiers, "boom")
    notifiers.add_notifier("step.error", lambda e: e.retry())
    tpl = operation("- id: boom\n  fail: always")
    with pytest.raises(ExecutionError):
        await execute(tpl, notifiers=notifiers, limits=Limits(max_retries_per_step=2))
    assert len(attempts) == 3
    notifiers.clear()
    notifiers.add_notifier("step.error", lambda e: e.replace("recovered"), step="boom")
    run = await execute(tpl, notifiers=notifiers)
    assert run.prev == "recovered"


async def test_step_error_retry_waits() -> None:
    notifiers = NotifierSet("global")
    waits: list[float] = []

    async def sleep(seconds: float) -> None:
        waits.append(seconds)

    notifiers.add_notifier("step.error", lambda e: e.retry(0.5) if e.attempt == 1 else None)
    tpl = operation("- fail: always")
    with pytest.raises(ExecutionError):
        await execute(tpl, notifiers=notifiers, sleep=sleep)
    assert waits == [0.5]


async def test_on_error_skip_and_default() -> None:
    tpl = operation(
        """
        - id: skipped
          for_each: ${{ [1] }}
          on_error: skip
          do:
            - fail: nope
        - id: defaulted
          for_each: ${{ [1] }}
          on_error:
            default: "${{ {'failed': true, 'reason': error.code, 'user': error.user_code} }}"
          do:
            - fail: nope
              code: custom
        """
    )
    run = await execute(tpl)
    assert run.frame.steps == {
        "skipped": None,
        "defaulted": {"failed": True, "reason": "E503", "user": "custom"},
    }
    assert run.state.stats.errors_caught == 2


async def test_on_error_catch_filters_codes() -> None:
    tpl = operation(
        """
        - for_each: ${{ [1] }}
          on_error:
            catch: [E601]
            then: skip
          do:
            - fail: nope
        """
    )
    with pytest.raises(ExecutionError):
        await execute(tpl)


async def test_on_error_never_catches_validation_errors() -> None:
    tpl = operation(
        """
        - for_each: ${{ [1] }}
          on_error: skip
          do:
            - assert: ${{ false }}
        """
    )
    with pytest.raises(ValidationError):
        await execute(tpl)


async def test_on_error_retry_then_outcome() -> None:
    notifiers = NotifierSet("global")
    attempts = count_attempts(notifiers, "flaky")
    waits: list[float] = []

    async def sleep(seconds: float) -> None:
        waits.append(seconds)

    tpl = operation(
        """
        - id: flaky
          for_each: ${{ [1] }}
          on_error:
            retry: { times: 3, backoff: exponential, delay: 1s, max_delay: 3s }
            then: { default: [] }
          do:
            - fail: nope
        """
    )
    run = await execute(tpl, notifiers=notifiers, sleep=sleep)
    assert len(attempts) == 4
    assert waits == [1.0, 2.0, 3.0]
    assert run.frame.steps["flaky"] == []
    assert run.state.stats.retries == 3
    fails = operation(
        """
        - for_each: ${{ [1] }}
          on_error:
            retry: { times: 1, delay: 0s }
          do:
            - fail: nope
        """
    )
    with pytest.raises(ExecutionError):
        await execute(fails, sleep=sleep)


def test_retry_delay() -> None:
    fixed = RetrySpec(times=3, backoff="fixed", delay=Duration(2.0))
    assert [retry_delay(fixed, n) for n in (1, 2, 3)] == [2.0, 2.0, 2.0]
    exp = RetrySpec(times=5, delay=Duration(1.0), max_delay=Duration(5.0))
    assert [retry_delay(exp, n) for n in (1, 2, 3, 4)] == [1.0, 2.0, 4.0, 5.0]


async def test_timeout_is_e504() -> None:
    notifiers = NotifierSet("global")

    async def slow(event: Any) -> None:
        await asyncio.sleep(1)

    notifiers.add_notifier("step.before", slow, step="inner")
    tpl = operation(
        """
        - for_each: ${{ [1] }}
          timeout: 20ms
          do:
            - id: inner
              log: x
        """
    )
    with pytest.raises(ExecutionError) as info:
        await execute(tpl, notifiers=notifiers)
    assert info.value.code == "E504"


async def test_timeout_not_hit() -> None:
    tpl = operation(
        """
        - for_each: ${{ [1] }}
          timeout: 5s
          do:
            - return: ok
        """
    )
    assert (await execute(tpl)).prev == ["ok"]


# ── limits and cancellation ──────────────────────────────────────────────────


async def test_max_loop_iterations_is_e701() -> None:
    tpl = operation(
        """
        - for_each: ${{ [1, 2, 3] }}
          do:
            - log: x
        """
    )
    with pytest.raises(LimitError, match="max_loop_iterations"):
        await execute(tpl, limits=Limits(max_loop_iterations=2))
    spin = operation(
        """
        - while: ${{ true }}
          max_iterations: 100
          do:
            - log: x
        """
    )
    with pytest.raises(LimitError, match="max_loop_iterations"):
        await execute(spin, limits=Limits(max_loop_iterations=3))


async def test_max_depth_is_e701() -> None:
    tpl = operation(
        """
        - if: ${{ true }}
          then:
            - if: ${{ true }}
              then:
                - log: deep
        """
    )
    with pytest.raises(LimitError, match="max_depth"):
        await execute(tpl, limits=Limits(max_depth=1))


async def test_max_duration_is_e701() -> None:
    notifiers = NotifierSet("global")

    async def slow(event: Any) -> None:
        await asyncio.sleep(0.02)

    notifiers.add_notifier("step.before", slow, step="first")
    tpl = operation(
        """
        - id: first
          log: a
        - log: b
        """
    )
    with pytest.raises(LimitError, match="max_duration"):
        await execute(tpl, notifiers=notifiers, limits=Limits(max_duration=Duration(0.01)))


async def test_cancellation_is_checked_at_step_boundaries() -> None:
    notifiers = NotifierSet("global")
    state_holder: list[Any] = []

    def cancel(event: Any) -> None:
        state_holder[0].cancel_requested = True

    notifiers.add_notifier("step.after", cancel, step="first")
    tpl = operation(
        """
        - id: first
          log: a
        - log: b
        """
    )
    from crowley.application.runtime import LimitsGuard

    original = LimitsGuard.__init__

    def capture(self: LimitsGuard, limits: Limits) -> None:
        original(self, limits)
        state_holder.append(self)

    LimitsGuard.__init__ = capture  # type: ignore[method-assign]
    try:
        with pytest.raises(ControlError) as info:
            await execute(tpl, notifiers=notifiers)
    finally:
        LimitsGuard.__init__ = original  # type: ignore[method-assign]
    assert info.value.code == "E803"


async def test_errors_carry_step_path() -> None:
    tpl = operation(
        """
        - for_each: ${{ [1] }}
          do:
            - log: ${{ str([1][5]) }}
        """
    )
    with pytest.raises(CrowleyError) as info:
        await execute(tpl)
    assert info.value.code == "E501"
    assert info.value.path == "operations.op.steps[0].do[0].log"


async def test_output_collector_streams_until_closed() -> None:
    from crowley.application.runtime import OutputCollector

    collector = OutputCollector(streaming=True)
    collector.add(1)
    collector.add({"a": 2})
    collector.close()
    assert [await collector.next() for _ in range(3)] == [
        (True, 1),
        (True, {"a": 2}),
        (False, None),
    ]
    assert len(collector) == 2
    with pytest.raises(RuntimeError, match="does not stream"):
        await OutputCollector().next()

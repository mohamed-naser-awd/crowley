"""Notifier conformance (M2 exit criterion, SPEC §17).

Every step of every kind, at every nesting depth (main block, then/else, for_each, while,
block-function bodies, template functions, template functions called from loops) fires
``step.before`` and then exactly one of ``step.after``, ``step.error`` or ``step.skipped``.
Notifiers can change a step's incoming ``prev``, its kwargs and its result, and the change
flows on to the next step, ``steps.<id>`` and the final output.
"""

from collections import defaultdict
from typing import Any

import pytest

from crowley import Crowley, FunctionContext, function
from crowley.domain.steps import STEP_KINDS, iter_steps

TEMPLATE = """\
crowley: 1
id: acme/conformance
version: 1.0.0
name: Conformance
description: Every step kind at every depth.
permissions:
  hosts: [api.example.com]
requires: ["acme.*@^1"]
functions:
  helper:
    description: Template function body with several step kinds.
    output: {}
    steps:
      - id: h_set
        set: { seen: true }
      - id: h_log
        log: in helper
      - id: h_if
        if: ${{ args.prev == 1 }}
        then:
          - id: h_then
            return: one
        else:
          - id: h_else
            log: other
      - id: h_assert
        assert: ${{ vars.seen }}
      - id: h_return
        return: ${{ args.prev }}
operations:
  op:
    description: Exercises the pipeline.
    steps:
      - id: m_set
        set: { counter: 0 }
      - id: m_log
        log: start
      - id: m_assert
        assert: ${{ true }}
      - id: m_skip
        when: ${{ false }}
        log: never
      - id: m_fail
        fail: deliberate
        on_error: skip
      - id: m_use
        use: acme.double
        with: { value: 2 }
      - id: m_local
        use: local.helper
      - id: m_loop
        for_each: ${{ [1, 2, 3, 4] }}
        do:
          - id: l_if
            if: ${{ item == 2 }}
            then:
              - id: l_continue
                continue: true
            else:
              - id: l_log
                log: ${{ str(item) }}
          - id: l_break_if
            if: ${{ item == 4 }}
            then:
              - id: l_break
                break: true
          - id: l_local
            use: local.helper
          - id: l_emit
            emit:
              n: ${{ item }}
          - id: l_return
            return: ${{ item }}
      - id: m_while
        while: ${{ vars.counter < 2 }}
        max_iterations: 5
        do:
          - id: w_set
            set: { counter: "${{ vars.counter + 1 }}" }
          - id: w_use
            use: acme.double
            with: { value: "${{ vars.counter }}" }
      - id: m_block
        use: acme.each
        with: { items: [1, 2] }
        do:
          - id: b_use
            use: acme.double
            with: { value: "${{ entry }}" }
          - id: b_if
            if: ${{ entry == 2 }}
            then:
              - id: b_break
                break: true
          - id: b_emit
            emit:
              n: ${{ prev }}
      - id: m_if
        if: ${{ true }}
        then:
          - id: t_emit
            emit:
              n: 0
    output:
      schema:
        type: array
        items:
          type: object
          required: [n]
"""


@function(
    name="acme.double",
    input={"type": "object", "required": ["value"], "properties": {"value": {"type": "integer"}}},
    output={"type": "integer"},
)
async def double(ctx: FunctionContext, value: int) -> int:
    return value * 2


@function(
    name="acme.each",
    kind="block",
    input={"type": "object", "required": ["items"], "properties": {"items": {"type": "array"}}},
    output={"type": "array"},
    body_bindings=("entry",),
)
async def each(ctx: FunctionContext, items: list[Any], **kwargs: Any) -> list[Any]:
    assert ctx.body is not None
    results = []
    for entry in items:
        outcome = await ctx.body.run(input=entry, bindings={"entry": entry})
        if outcome.is_break:
            break
        if outcome.has_value:
            results.append(outcome.value)
    return results


TERMINAL = {"step.after", "step.error", "step.skipped"}


def _check_sequences(events: dict[str, list[str]]) -> None:
    """Per step path: (before, terminal)+ with terminal in after/error/skipped."""
    for path, names in events.items():
        assert len(names) % 2 == 0, (path, names)
        for before, terminal in zip(names[::2], names[1::2], strict=True):
            assert before == "step.before", (path, names)
            assert terminal in TERMINAL, (path, names)


async def test_every_step_fires_before_and_after() -> None:
    cw = Crowley(functions=[double, each])
    tpl = cw.load_text(TEMPLATE, name="conformance.yml")
    events: dict[str, list[str]] = defaultdict(list)
    kinds: dict[str, str] = {}
    function_events: dict[str, list[str]] = defaultdict(list)

    def record(event: Any) -> None:
        events[event.step.path].append(event.name)
        kinds[event.step.path] = event.step.kind

    cw.add_notifier("step.*", record, observe=True)
    cw.add_notifier(
        "function.*", lambda e: function_events[e.step.path].append(e.name), observe=True
    )
    result = await cw.run(tpl, "op")

    _check_sequences(events)
    # every step of the operation and of the template function ran at least once
    all_steps = [*iter_steps(tpl.operation("op").steps), *iter_steps(tpl.functions["helper"].steps)]
    assert {str(s.path) for s in all_steps} == set(events)
    assert set(kinds.values()) == set(STEP_KINDS)
    # the specific terminals we expect
    assert events["operations.op.steps[3]"] == ["step.before", "step.skipped"]
    assert events["operations.op.steps[4]"] == ["step.before", "step.error"]
    # every function call fired function.before then function.after
    for path, names in function_events.items():
        assert names[::2] == ["function.before"] * (len(names) // 2), path
        assert names[1::2] == ["function.after"] * (len(names) // 2), path
    assert result.output == [{"n": 1}, {"n": 3}, {"n": 2}, {"n": 0}]


async def test_notifiers_change_prev_kwargs_and_results_downstream() -> None:
    cw = Crowley(functions=[double, each])
    tpl = cw.load_text(TEMPLATE, name="conformance.yml")
    results: dict[str, Any] = {}
    incoming: dict[str, Any] = {}
    process = cw.init(tpl, "op")
    # before: change kwargs of a function call and the incoming prev of a template function
    process.add_notifier("step.before", lambda e: e.kwargs.update(value=10), step="m_use")
    process.add_notifier("step.before", lambda e: setattr(e, "prev", 7), step="m_local")
    # after: replace results deep inside loop and block bodies
    process.add_notifier("step.after", lambda e: e.replace(e.result * 100), step="l_return")
    process.add_notifier("step.after", lambda e: e.replace(e.result + 1), step="b_use")
    # observe what flowed downstream
    process.add_notifier(
        "step.after", lambda e: results.update({e.step.id: e.result}), step="m_*", observe=True
    )
    process.add_notifier(
        "step.before",
        lambda e: incoming.update({e.step.id: (e.prev, dict(e.scope["steps"]))}),
        step="m_*",
        observe=True,
    )
    result = await process.run()

    assert results["m_use"] == 20  # kwargs changed in step.before
    assert results["m_local"] == 7  # prev changed in step.before, returned as args.prev
    assert incoming["m_loop"][0] == 7  # the next step received the changed result as prev
    assert incoming["m_loop"][1]["m_use"] == 20  # and steps.<id> holds the changed result
    assert results["m_loop"] == [100, 300]  # results replaced inside the loop body
    assert results["m_block"] == [3]  # replaced inside the block-function body
    assert result.output == [{"n": 1}, {"n": 3}, {"n": 3}, {"n": 0}]


@pytest.mark.parametrize("pattern", ["step.before", "step.*", "*"])
async def test_global_notifiers_reach_nested_template_functions(pattern: str) -> None:
    cw = Crowley(functions=[double, each])
    tpl = cw.load_text(TEMPLATE, name="conformance.yml")
    seen: set[str] = set()
    cw.add_notifier(pattern, lambda e: seen.add(e.step.id) if e.step else None, observe=True)
    await cw.run(tpl, "op")
    assert {"h_then", "h_else", "b_break", "w_use", "l_continue", "l_break"} <= seen

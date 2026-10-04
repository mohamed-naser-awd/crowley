"""paginate.*, transform.* and control.* run from templates (SPEC §12)."""

import asyncio
from typing import Any

import pytest

from crowley import FunctionContext, function
from crowley.application.events import NotifierSet
from crowley.domain.errors import ExecutionError, LimitError, ValidationError
from crowley.domain.template import Limits
from tests.application.runtime_support import execute, operation

PAGES = {1: ["a", "b"], 2: ["c"], 3: []}
CURSORS = {None: (["a"], "c1"), "c1": (["b"], "c2"), "c2": (["c"], None)}
REQUIRES = 'requires: ["acme.*@^1"]'
calls: list[Any] = []


@function(
    name="acme.page",
    input={"type": "object", "properties": {"n": {}, "cursor": {}, "url": {}, "offset": {}}},
    output={},
)
async def page_fn(ctx: FunctionContext, **kwargs: Any) -> Any:
    calls.append({k: v for k, v in kwargs.items() if k != "prev"})
    if "n" in kwargs:
        return PAGES.get(kwargs["n"], [])
    if "offset" in kwargs:
        data = list(range(7))
        return data[kwargs["offset"] : kwargs["offset"] + 3]
    if "url" in kwargs:
        url = kwargs["url"]
        links = {
            "https://api.example.com/p1": "/p2",
            "https://api.example.com/p2": "p3",
            "https://api.example.com/p3": "/p1",
        }
        return {"items": [url[-2:]], "next": links.get(url)}
    items, cursor = CURSORS[kwargs.get("cursor")]
    return {"items": items, "next": cursor}


@function(name="acme.flaky", output={})
async def flaky(ctx: FunctionContext, **kwargs: Any) -> Any:
    calls.append("flaky")
    if len(calls) < 3:
        raise ValueError("not yet")
    return len(calls)


@function(name="acme.slow", input={"type": "object", "properties": {"i": {}}}, output={})
async def slow(ctx: FunctionContext, i: int = 0, **kwargs: Any) -> Any:
    await asyncio.sleep(0.01 * (3 - i))
    return i


FUNCTIONS = [page_fn, flaky, slow]


def op(steps: str, **kwargs: Any) -> Any:
    kwargs.setdefault("extra", REQUIRES)
    return operation(steps, functions=FUNCTIONS, **kwargs)


async def run(tpl: Any, **kwargs: Any) -> Any:
    calls.clear()
    return await execute(tpl, functions=FUNCTIONS, **kwargs)


# ── paginate ─────────────────────────────────────────────────────────────────


async def test_by_page_stops_on_empty_and_flattens() -> None:
    tpl = op(
        """
        - id: nested
          use: paginate.by_page
          do:
            - use: acme.page
              with: { n: "${{ page.number }}" }
        - id: flat
          use: paginate.by_page
          with: { flatten: true }
          do:
            - use: acme.page
              with: { n: "${{ page.number }}" }
        """
    )
    result = await run(tpl)
    assert result.frame.steps["nested"] == [["a", "b"], ["c"]]
    assert result.frame.steps["flat"] == ["a", "b", "c"]


async def test_by_page_stop_when_step_and_page_events() -> None:
    notifiers = NotifierSet("global")
    seen: list[Any] = []
    notifiers.add_notifier("page.before", lambda e: seen.append(dict(e.page)), observe=True)
    notifiers.add_notifier("page.after", lambda e: setattr(e, "value", [*e.value, "!"]))
    tpl = op(
        """
        - use: paginate.by_page
          with:
            start: 1
            step: 2
            stop_on_empty: false
            stop_when: ${{ page.number >= 3 }}
          do:
            - use: acme.page
              with: { n: "${{ page.number }}" }
        """
    )
    result = await run(tpl, notifiers=notifiers)
    assert result.prev == [["a", "b", "!"], ["!"]]
    assert seen == [{"number": 1, "index": 0}, {"number": 3, "index": 1}]


async def test_page_skip_and_stop_and_break() -> None:
    notifiers = NotifierSet("global")
    notifiers.add_notifier("page.before", lambda e: e.skip() if e.page["number"] == 1 else None)
    notifiers.add_notifier("page.after", lambda e: setattr(e, "stop", True))
    tpl = op(
        """
        - use: paginate.by_page
          with: { stop_on_empty: false }
          do:
            - use: acme.page
              with: { n: "${{ page.number }}" }
        """
    )
    assert (await run(tpl, notifiers=notifiers)).prev == [["c"]]
    breaking = op(
        """
        - use: paginate.by_page
          do:
            - if: ${{ page.number == 2 }}
              then:
                - break: true
            - use: acme.page
              with: { n: "${{ page.number }}" }
        """
    )
    assert (await run(breaking)).prev == [["a", "b"]]


async def test_max_pages_and_loop_limit() -> None:
    tpl = op(
        """
        - use: paginate.by_page
          with: { max_pages: 2, stop_on_empty: false }
          do:
            - return: ${{ page.number }}
        """
    )
    assert (await run(tpl)).prev == [1, 2]
    capped = op(
        """
        - use: paginate.by_page
          with: { stop_on_empty: false }
          do:
            - return: ${{ page.index }}
        """
    )
    assert (await run(capped, limits=Limits(max_loop_iterations=3))).prev == [0, 1, 2]


async def test_stop_when_must_be_bool() -> None:
    tpl = op(
        """
        - use: paginate.by_page
          with: { stop_when: "${{ 1 }}" }
          do:
            - return: [1]
        """
    )
    with pytest.raises(ExecutionError, match="stop_when must be a bool"):
        await run(tpl)


async def test_by_offset() -> None:
    tpl = op(
        """
        - id: all
          use: paginate.by_offset
          with: { limit: 3, flatten: true }
          do:
            - use: acme.page
              with: { offset: "${{ page.offset }}" }
        - id: capped
          use: paginate.by_offset
          with: { limit: 3, flatten: true, max_items: 4 }
          do:
            - use: acme.page
              with: { offset: "${{ page.offset }}" }
        - id: stopped
          use: paginate.by_offset
          with: { limit: 3, stop_when: "${{ page.offset >= 3 }}" }
          do:
            - use: acme.page
              with: { offset: "${{ page.offset }}" }
        - id: not_lists
          use: paginate.by_offset
          with: { limit: 3, stop_on_short_page: false, max_pages: 2 }
          do:
            - return: ${{ page.limit }}
        """
    )
    steps = (await run(tpl)).frame.steps
    assert steps["all"] == [0, 1, 2, 3, 4, 5, 6]
    assert steps["capped"] == [0, 1, 2, 3]
    assert steps["stopped"] == [[0, 1, 2], [3, 4, 5]]
    assert steps["not_lists"] == [3, 3]


async def test_by_cursor_and_loop_detection() -> None:
    tpl = op(
        """
        - use: paginate.by_cursor
          with:
            next_cursor: ${{ result.next }}
          do:
            - use: acme.page
              with: { cursor: "${{ page.cursor }}" }
        """
    )
    assert [page["items"] for page in (await run(tpl)).prev] == [["a"], ["b"], ["c"]]
    loop = op(
        """
        - use: paginate.by_cursor
          with:
            initial: x
            next_cursor: ${{ 'x' }}
          do:
            - return: 1
        """
    )
    with pytest.raises(LimitError) as info:
        await run(loop)
    assert info.value.code == "E705"


async def test_by_next_link_resolves_relative_links_and_stops_on_revisit() -> None:
    tpl = op(
        """
        - use: paginate.by_next_link
          with:
            start_url: https://api.example.com/p1
            next: ${{ result.next }}
          do:
            - use: acme.page
              with: { url: "${{ page.url }}" }
        """
    )
    result = await run(tpl)
    assert [r["items"] for r in result.prev] == [["p1"], ["p2"], ["p3"]]
    bad = op(
        """
        - use: paginate.by_next_link
          with: { start_url: "https://api.example.com/p1", next: "${{ 5 }}" }
          do:
            - return: 1
        """
    )
    with pytest.raises(ExecutionError, match="URL string"):
        await run(bad)


# ── transform ────────────────────────────────────────────────────────────────


async def test_transforms() -> None:
    tpl = op(
        """
        - for_each: "${{ [{'n': 'b'}, {'n': 'a'}, {'n': 'c'}, {'n': 'd'}] }}"
          do:
            - return: ${{ item }}
        - id: mapped
          use: transform.map
          with:
            fields: { name: "${{ item.n }}", i: "${{ index }}" }
        - id: filtered
          use: transform.filter
          with: { items: "${{ steps.mapped }}", where: "${{ item.i > 0 }}" }
        - id: deduped
          use: transform.dedupe
          with: { items: "${{ [1, 2, 1, 3, 2] }}", by: "${{ item }}" }
        - id: sorted
          use: transform.sort
          with: { items: "${{ [{'k': 2}, {'k': null}, {'k': 1}] }}", by: "${{ item.k }}" }
        - id: desc
          use: transform.sort
          with: { items: "${{ [3, 1, 2] }}", by: "${{ item }}", order: desc }
        - id: grouped
          use: transform.group_by
          with:
            items: "${{ [{'t': 'x', 'v': 1}, {'t': 'y', 'v': 2}, {'t': 'x', 'v': 3}] }}"
            by: ${{ item.t }}
        - id: flat
          use: transform.flatten
          with: { items: "${{ [[1, [2]], [3]] }}" }
        - id: deep
          use: transform.flatten
          with: { items: "${{ [[1, [2, [3]]]] }}", depth: 5 }
        """
    )
    steps = (await run(tpl)).frame.steps
    assert steps["mapped"] == [
        {"name": "b", "i": 0},
        {"name": "a", "i": 1},
        {"name": "c", "i": 2},
        {"name": "d", "i": 3},
    ]
    assert [m["name"] for m in steps["filtered"]] == ["a", "c", "d"]
    assert steps["deduped"] == [1, 2, 3]
    assert steps["sorted"] == [{"k": 1}, {"k": 2}, {"k": None}]
    assert steps["desc"] == [3, 2, 1]
    assert steps["grouped"] == {
        "x": [{"t": "x", "v": 1}, {"t": "x", "v": 3}],
        "y": [{"t": "y", "v": 2}],
    }
    assert steps["flat"] == [1, [2], 3]
    assert steps["deep"] == [1, 2, 3]


@pytest.mark.parametrize(
    ("fn", "args", "message"),
    [
        ("transform.filter", '{ items: [1], where: "${{ 1 }}" }', "where must be a bool"),
        ("transform.group_by", '{ items: [1], by: "${{ [item] }}" }', "group key"),
        ("transform.sort", '{ items: [1, a], by: "${{ item }}" }', "cannot order"),
        ("transform.flatten", '{ items: "${{ 5 }}" }', "not of type 'array'"),
    ],
)
async def test_transform_errors(fn: str, args: str, message: str) -> None:
    tpl = op(f"- use: {fn}\n  with: {args}")
    with pytest.raises((ExecutionError, ValidationError), match=message):
        await run(tpl)


# ── control ──────────────────────────────────────────────────────────────────


async def test_control_retry_on_errors_and_until() -> None:
    tpl = op(
        """
        - id: retried
          use: control.retry
          with: { times: 3, delay: 0 }
          do:
            - use: acme.flaky
        """
    )
    result = await run(tpl)
    assert result.frame.steps["retried"] == 3
    until = op(
        """
        - set: { n: 0 }
        - id: polled
          use: control.retry
          with: { times: 5, delay: 0, backoff: fixed, until: "${{ result >= 2 }}" }
          do:
            - set: { n: "${{ vars.n + 1 }}" }
            - return: ${{ vars.n }}
        """
    )
    assert (await run(until)).frame.steps["polled"] == 2


async def test_control_retry_gives_up() -> None:
    never = op(
        """
        - use: control.retry
          with: { times: 1, delay: 0, until: "${{ false }}" }
          do:
            - return: 1
        """
    )
    with pytest.raises(ExecutionError, match="still false"):
        await run(never)
    failing = op(
        """
        - use: control.retry
          with: { times: 1, delay: 0 }
          do:
            - fail: always
        """
    )
    with pytest.raises(ExecutionError, match="always"):
        await run(failing)
    typed = op(
        """
        - use: control.retry
          with: { times: 1, until: "${{ 1 }}" }
          do:
            - return: 1
        """
    )
    with pytest.raises(ExecutionError, match="until must be a bool"):
        await run(typed)
    breaking = op(
        """
        - use: control.retry
          with: { times: 2 }
          do:
            - break: true
        """
    )
    assert (await run(breaking)).prev is None


async def test_control_sleep_passes_prev_through() -> None:
    waits: list[float] = []

    async def sleep(seconds: float) -> None:
        waits.append(seconds)

    tpl = op(
        """
        - for_each: ${{ [1] }}
          do:
            - return: ${{ item }}
        - use: control.sleep
          with: { duration: 250ms }
        """
    )
    assert (await run(tpl, sleep=sleep)).prev == [1]
    assert waits == [0.25]


async def test_control_parallel() -> None:
    tpl = op(
        """
        - use: control.parallel
          with: { branches: 3 }
          do:
            - use: acme.slow
              with: { i: "${{ branch.index }}" }
        """
    )
    assert (await run(tpl)).prev == [0, 1, 2]
    failing = op(
        """
        - use: control.parallel
          with: { branches: 2 }
          do:
            - fail: ${{ 'branch ' + str(branch.index) }}
        """
    )
    with pytest.raises(ExecutionError, match="branch") as info:
        await run(failing)
    assert len(info.value.related) == 1

"""``paginate.*``: block functions that run their body once per page (SPEC §12.1).

All four bind ``page`` in the body, fire ``page.before``/``page.after``, collect the body's
per-page values (``flatten: true`` concatenates lists), stop on ``break``, and are capped by
``max_pages`` and ``limits.max_loop_iterations``.
"""

from collections.abc import Awaitable, Callable
from typing import Any
from urllib.parse import urljoin

from crowley.application.runtime import FunctionContext
from crowley.domain.errors import ExecutionError, LimitError
from crowley.domain.events import PageAfter, PageBefore, Skip
from crowley.domain.values import Value, to_json_text, type_of

NextPage = Callable[[dict[str, Value], Value, int], Awaitable[dict[str, Value] | None]]
"""(current page, its value, number of pages done) → next page, or None to stop."""


def _truthy(value: Value, what: str, ctx: FunctionContext) -> bool:
    if not isinstance(value, bool):
        raise ExecutionError(
            "E501", f"{what} must be a bool, got {type_of(value)}", path=ctx.step.path
        )
    return value


async def _pages(
    ctx: FunctionContext,
    first: dict[str, Value],
    next_page: NextPage,
    *,
    max_pages: int,
    flatten: bool,
    keep: Callable[[Value], bool] = lambda value: True,
) -> list[Value]:
    assert ctx.body is not None
    limit = min(max_pages, ctx.limits.max_loop_iterations or max_pages)
    results: list[Value] = []
    page: dict[str, Value] | None = first
    done = 0
    while page is not None and done < limit:
        before, outcome = await ctx.publish(PageBefore, page=page)
        page = before.page if isinstance(before.page, dict) else page
        value: Value = None
        if not isinstance(outcome.action, Skip):
            result = await ctx.body.run(bindings={"page": page})
            if result.is_break:
                break
            value = result.value if result.has_value else None
            after, _ = await ctx.publish(PageAfter, page=page, value=value)
            value = after.value
            if result.has_value and keep(value):
                if flatten and isinstance(value, list):
                    results.extend(value)
                else:
                    results.append(value)
            if after.stop:
                break
        done += 1
        page = await next_page(page, value, done)
    return results


def _empty(value: Value) -> bool:
    return value is None or value == []


async def by_page(
    ctx: FunctionContext,
    start: int = 1,
    step: int = 1,
    max_pages: int = 100,
    stop_when: Any = None,
    stop_on_empty: bool = True,
    flatten: bool = False,
    **kwargs: Any,
) -> list[Value]:
    async def next_page(page: dict[str, Value], value: Value, done: int) -> dict[str, Value] | None:
        if stop_on_empty and _empty(value):
            return None
        if stop_when is not None and _truthy(
            ctx.evaluate(stop_when, {"result": value, "page": page}), "stop_when", ctx
        ):
            return None
        return {"number": start + done * step, "index": done}

    return await _pages(
        ctx,
        {"number": start, "index": 0},
        next_page,
        max_pages=max_pages,
        flatten=flatten,
        keep=lambda value: not (stop_on_empty and _empty(value)),
    )


async def by_offset(
    ctx: FunctionContext,
    limit: int,
    start: int = 0,
    max_pages: int = 100,
    max_items: int | None = None,
    stop_when: Any = None,
    stop_on_short_page: bool = True,
    flatten: bool = False,
    **kwargs: Any,
) -> list[Value]:
    collected = 0

    async def next_page(page: dict[str, Value], value: Value, done: int) -> dict[str, Value] | None:
        nonlocal collected
        size = len(value) if isinstance(value, list) else 0
        collected += size
        if stop_on_short_page and (not isinstance(value, list) or size < limit):
            return None
        if max_items is not None and collected >= max_items:
            return None
        if stop_when is not None and _truthy(
            ctx.evaluate(stop_when, {"result": value, "page": page}), "stop_when", ctx
        ):
            return None
        return {"offset": start + done * limit, "limit": limit, "index": done}

    results = await _pages(
        ctx,
        {"offset": start, "limit": limit, "index": 0},
        next_page,
        max_pages=max_pages,
        flatten=flatten,
    )
    if max_items is not None and flatten:
        return results[:max_items]
    return results


async def by_cursor(
    ctx: FunctionContext,
    next_cursor: Any,
    initial: Value = None,
    max_pages: int = 100,
    flatten: bool = False,
    **kwargs: Any,
) -> list[Value]:
    seen: set[str] = {to_json_text(initial)}

    async def next_page(page: dict[str, Value], value: Value, done: int) -> dict[str, Value] | None:
        cursor = ctx.evaluate(next_cursor, {"result": value, "page": page})
        if cursor is None or cursor == "":
            return None
        key = to_json_text(cursor)
        if key in seen:
            raise LimitError(
                "E705",
                f"pagination loop: cursor {key} was already used",
                path=ctx.step.path,
                hint="check that next_cursor reads the cursor of the current response",
            )
        seen.add(key)
        return {"cursor": cursor, "index": done}

    return await _pages(
        ctx, {"cursor": initial, "index": 0}, next_page, max_pages=max_pages, flatten=flatten
    )


async def by_next_link(
    ctx: FunctionContext,
    start_url: str,
    next: Any,
    max_pages: int = 100,
    flatten: bool = False,
    **kwargs: Any,
) -> list[Value]:
    visited: set[str] = {start_url}

    async def next_page(page: dict[str, Value], value: Value, done: int) -> dict[str, Value] | None:
        link = ctx.evaluate(next, {"result": value, "page": page})
        if link is None or link == "":
            return None
        if not isinstance(link, str):
            raise ExecutionError(
                "E501", f"next must be a URL string, got {type_of(link)}", path=ctx.step.path
            )
        url = urljoin(str(page["url"]), link)
        if url in visited:
            return None
        visited.add(url)
        return {"url": url, "index": done}

    return await _pages(
        ctx, {"url": start_url, "index": 0}, next_page, max_pages=max_pages, flatten=flatten
    )


HANDLERS: dict[str, Callable[..., Any]] = {
    "paginate.by_page": by_page,
    "paginate.by_offset": by_offset,
    "paginate.by_cursor": by_cursor,
    "paginate.by_next_link": by_next_link,
}

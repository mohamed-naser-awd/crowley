"""``transform.*``: named list transformations (SPEC §12.2). ``items`` defaults to ``prev``."""

import functools
from collections.abc import Callable
from typing import Any

from crowley.application.runtime import FunctionContext
from crowley.domain.errors import ExecutionError
from crowley.domain.values import Value, compare, to_json_text, type_of


def _items(ctx: FunctionContext, items: Value) -> list[Value]:
    if not isinstance(items, list):
        raise ExecutionError(
            "E501", f"items must be a list, got {type_of(items)}", path=ctx.step.path
        )
    return items


async def map_(ctx: FunctionContext, items: Value, fields: Any, **kwargs: Any) -> list[Value]:
    return [
        ctx.evaluate(fields, {"item": item, "index": index})
        for index, item in enumerate(_items(ctx, items))
    ]


async def filter_(ctx: FunctionContext, items: Value, where: Any, **kwargs: Any) -> list[Value]:
    kept: list[Value] = []
    for index, item in enumerate(_items(ctx, items)):
        keep = ctx.evaluate(where, {"item": item, "index": index})
        if not isinstance(keep, bool):
            raise ExecutionError(
                "E501", f"where must be a bool, got {type_of(keep)}", path=ctx.step.path
            )
        if keep:
            kept.append(item)
    return kept


async def dedupe(ctx: FunctionContext, items: Value, by: Any, **kwargs: Any) -> list[Value]:
    seen: set[str] = set()
    kept: list[Value] = []
    for item in _items(ctx, items):
        key = to_json_text(ctx.evaluate(by, {"item": item}))
        if key not in seen:
            seen.add(key)
            kept.append(item)
    return kept


async def sort(
    ctx: FunctionContext, items: Value, by: Any, order: str = "asc", **kwargs: Any
) -> list[Value]:
    keyed = [(ctx.evaluate(by, {"item": item}), item) for item in _items(ctx, items)]

    def cmp(a: tuple[Value, Value], b: tuple[Value, Value]) -> int:
        x, y = a[0], b[0]
        if x is None or y is None:  # nulls always last
            return (x is None) - (y is None)
        result = compare(x, y)
        return -result if order == "desc" else result

    try:
        keyed.sort(key=functools.cmp_to_key(cmp))
    except ExecutionError as exc:
        raise exc.with_context(path=ctx.step.path) from None
    return [item for _, item in keyed]


async def group_by(
    ctx: FunctionContext, items: Value, by: Any, **kwargs: Any
) -> dict[str, list[Value]]:
    groups: dict[str, list[Value]] = {}
    for item in _items(ctx, items):
        key = ctx.evaluate(by, {"item": item})
        if isinstance(key, bool) or not isinstance(key, str | int | float):
            raise ExecutionError(
                "E501",
                f"group key must be a string or number, got {type_of(key)}",
                path=ctx.step.path,
            )
        groups.setdefault(str(key), []).append(item)
    return groups


async def flatten(ctx: FunctionContext, items: Value, depth: int = 1, **kwargs: Any) -> list[Value]:
    def flat(values: list[Value], level: int) -> list[Value]:
        out: list[Value] = []
        for value in values:
            if isinstance(value, list) and level > 0:
                out.extend(flat(value, level - 1))
            else:
                out.append(value)
        return out

    return flat(_items(ctx, items), depth)


HANDLERS: dict[str, Callable[..., Any]] = {
    "transform.map": map_,
    "transform.filter": filter_,
    "transform.dedupe": dedupe,
    "transform.sort": sort,
    "transform.group_by": group_by,
    "transform.flatten": flatten,
}

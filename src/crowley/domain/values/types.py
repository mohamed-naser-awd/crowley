"""Type names, equality and ordering with Crowley's strict rules (docs/SPEC.md §9)."""

from typing import Literal, cast

from crowley.domain.errors import ExecutionError
from crowley.domain.values.model import Handle

TypeName = Literal["null", "bool", "int", "float", "string", "list", "object", "bytes", "handle"]


def type_of(value: object) -> TypeName:
    """Crowley type name of ``value``. ``bool`` is never reported as ``int``."""
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, int):
        return "int"
    if isinstance(value, float):
        return "float"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "list"
    if isinstance(value, dict):
        return "object"
    if isinstance(value, bytes):
        return "bytes"
    if isinstance(value, Handle):
        return "handle"
    raise TypeError(f"not a Crowley value: {type(value).__name__}")


def is_number(value: object) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool)


def values_equal(a: object, b: object) -> bool:
    """Deep equality: ``int`` and ``float`` compare numerically; other type mismatches are unequal.

    Unlike Python, ``True != 1`` and ``False != 0``.
    """
    if is_number(a) and is_number(b):
        return a == b
    ta, tb = type_of(a), type_of(b)
    if ta != tb:
        return False
    if ta == "list":
        assert isinstance(a, list)
        assert isinstance(b, list)
        return len(a) == len(b) and all(values_equal(x, y) for x, y in zip(a, b, strict=True))
    if ta == "object":
        assert isinstance(a, dict)
        assert isinstance(b, dict)
        return a.keys() == b.keys() and all(values_equal(a[k], b[k]) for k in a)
    if ta == "handle":
        return a is b
    return a == b


def compare(a: object, b: object) -> int:
    """Order two values: numbers with numbers, strings with strings, lists element-wise.

    Returns -1, 0 or 1. Anything else raises ``E501``.
    """
    if is_number(a) and is_number(b):
        x, y = cast(float, a), cast(float, b)  # int | float; no float() so big ints stay exact
        return (x > y) - (x < y)
    if isinstance(a, str) and isinstance(b, str):
        return (a > b) - (a < b)
    if isinstance(a, list) and isinstance(b, list):
        for x, y in zip(a, b, strict=False):
            result = compare(x, y)
            if result:
                return result
        return (len(a) > len(b)) - (len(a) < len(b))
    raise ExecutionError(
        "E501",
        f"cannot order {type_of(a)} and {type_of(b)}",
        hint="ordering works on numbers, strings and lists of those",
    )

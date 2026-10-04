"""Value nodes: template values (``with:``, ``emit:``, ``set:`` …) as written, before evaluation."""

from dataclasses import dataclass
from typing import TypeAlias, Union

from crowley.domain.common import TemplatePath
from crowley.domain.errors import SourceLocation
from crowley.domain.expressions import Compiled, Expression, Interpolation
from crowley.domain.values import Value


@dataclass(frozen=True, slots=True, kw_only=True)
class Lit:
    """A literal: number, bool, null or plain string (``$${{`` escapes already resolved)."""

    value: Value
    path: TemplatePath
    location: SourceLocation | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class Expr:
    """A string containing ``${{ }}``: one typed expression or an interpolation."""

    scalar: Compiled | Interpolation
    path: TemplatePath
    location: SourceLocation | None = None

    @property
    def expressions(self) -> tuple[Expression, ...]:
        if isinstance(self.scalar, Compiled):
            return (self.scalar.expression,)
        return self.scalar.expressions

    @property
    def is_typed(self) -> bool:
        """True for a single ``${{ expr }}`` (keeps the expression's type)."""
        return isinstance(self.scalar, Compiled)


@dataclass(frozen=True, slots=True, kw_only=True)
class MapNode:
    entries: tuple[tuple[str, "ValueNode"], ...]
    path: TemplatePath
    location: SourceLocation | None = None

    def get(self, key: str) -> "ValueNode | None":
        return next((node for k, node in self.entries if k == key), None)

    def keys(self) -> tuple[str, ...]:
        return tuple(k for k, _ in self.entries)


@dataclass(frozen=True, slots=True, kw_only=True)
class ListNode:
    items: tuple["ValueNode", ...]
    path: TemplatePath
    location: SourceLocation | None = None


ValueNode: TypeAlias = Union[Lit, Expr, MapNode, ListNode]  # noqa: UP007 - forward references


class _NotLiteralError(Exception):
    pass


def is_literal(node: ValueNode) -> bool:
    """True if the node contains no expressions anywhere."""
    try:
        _literal(node)
    except _NotLiteralError:
        return False
    return True


def literal_value(node: ValueNode) -> Value:
    """The plain value of a fully literal node; raises ``ValueError`` if it contains expressions."""
    try:
        return _literal(node)
    except _NotLiteralError:
        raise ValueError(f"value at {node.path} contains expressions") from None


def _literal(node: ValueNode) -> Value:
    match node:
        case Lit(value=value):
            return value
        case MapNode(entries=entries):
            return {k: _literal(v) for k, v in entries}
        case ListNode(items=items):
            return [_literal(v) for v in items]
        case _:
            raise _NotLiteralError


def partial_literal(node: ValueNode) -> Value:
    """Literal view where expression-valued parts are replaced by ``None``.

    Used for static checks of ``with:`` arguments (expression values are only checked for presence).
    """
    match node:
        case Lit(value=value):
            return value
        case MapNode(entries=entries):
            return {k: partial_literal(v) for k, v in entries}
        case ListNode(items=items):
            return [partial_literal(v) for v in items]
        case _:
            return None


def walk_expressions(node: ValueNode) -> list[tuple[Expression, Expr]]:
    """Every expression inside ``node``, with the ``Expr`` node that holds it."""
    found: list[tuple[Expression, Expr]] = []

    def visit(n: ValueNode) -> None:
        match n:
            case Expr():
                found.extend((e, n) for e in n.expressions)
            case MapNode(entries=entries):
                for _, child in entries:
                    visit(child)
            case ListNode(items=items):
                for child in items:
                    visit(child)
            case _:
                pass

    visit(node)
    return found

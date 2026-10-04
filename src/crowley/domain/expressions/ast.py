"""Expression syntax tree (docs/SPEC.md §10.2). Nodes are immutable; spans are source offsets."""

from dataclasses import dataclass
from typing import TypeAlias, Union

from crowley.domain.values import Value


@dataclass(frozen=True, slots=True, kw_only=True)
class Span:
    start: int
    end: int


@dataclass(frozen=True, slots=True, kw_only=True)
class Literal:
    value: Value
    span: Span


@dataclass(frozen=True, slots=True, kw_only=True)
class Name:
    """A root name (``prev``, ``inputs``, a loop variable) or a lambda parameter."""

    name: str
    span: Span


@dataclass(frozen=True, slots=True, kw_only=True)
class Current:
    """The element being processed inside a ``[*]`` projection body."""

    span: Span


@dataclass(frozen=True, slots=True, kw_only=True)
class Member:
    target: "Node"
    name: str
    optional: bool
    span: Span


@dataclass(frozen=True, slots=True, kw_only=True)
class Index:
    target: "Node"
    index: "Node"
    span: Span


@dataclass(frozen=True, slots=True, kw_only=True)
class Slice:
    target: "Node"
    start: "Node | None"
    stop: "Node | None"
    step: "Node | None"
    span: Span


@dataclass(frozen=True, slots=True, kw_only=True)
class Projection:
    """``source[*]<body>``: evaluates ``body`` once per element (``Current``) of ``source``."""

    source: "Node"
    body: "Node"
    span: Span


@dataclass(frozen=True, slots=True, kw_only=True)
class Unary:
    op: str  # "-" or "not"
    operand: "Node"
    span: Span


@dataclass(frozen=True, slots=True, kw_only=True)
class Binary:
    op: str  # + - * / // %
    left: "Node"
    right: "Node"
    span: Span


@dataclass(frozen=True, slots=True, kw_only=True)
class BoolOp:
    op: str  # "and" | "or"
    left: "Node"
    right: "Node"
    span: Span


@dataclass(frozen=True, slots=True, kw_only=True)
class Compare:
    """Python-style chained comparison: ``a < b <= c`` means ``a < b and b <= c``."""

    first: "Node"
    rest: tuple[tuple[str, "Node"], ...]
    span: Span


@dataclass(frozen=True, slots=True, kw_only=True)
class Conditional:
    test: "Node"
    then: "Node"
    otherwise: "Node"
    span: Span


@dataclass(frozen=True, slots=True, kw_only=True)
class Call:
    name: str
    args: tuple["Node", ...]
    span: Span


@dataclass(frozen=True, slots=True, kw_only=True)
class Lambda:
    params: tuple[str, ...]
    body: "Node"
    span: Span


@dataclass(frozen=True, slots=True, kw_only=True)
class ListLiteral:
    items: tuple["Node", ...]
    span: Span


@dataclass(frozen=True, slots=True, kw_only=True)
class ObjectLiteral:
    entries: tuple[tuple[str, "Node"], ...]
    span: Span


Node: TypeAlias = Union[  # noqa: UP007 - forward references
    Literal,
    Name,
    Current,
    Member,
    Index,
    Slice,
    Projection,
    Unary,
    Binary,
    BoolOp,
    Compare,
    Conditional,
    Call,
    Lambda,
    ListLiteral,
    ObjectLiteral,
]


@dataclass(frozen=True, slots=True, kw_only=True)
class Expression:
    """A compiled expression: its source text plus the parsed tree."""

    source: str
    node: Node

    def __str__(self) -> str:
        return self.source

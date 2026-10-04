"""Static facts about an expression, used by the template validator (E305, E313-E316, E328)."""

from collections.abc import Iterator
from dataclasses import dataclass

from crowley.domain.expressions import ast


@dataclass(frozen=True, slots=True, kw_only=True)
class Reference:
    """A use of a root name, plus the constant member/index chain that follows it.

    ``steps.rows[0].title`` → ``root="steps", path=("rows", 0, "title")``.
    """

    root: str
    path: tuple[str | int, ...]
    span: ast.Span


@dataclass(frozen=True, slots=True, kw_only=True)
class HelperCall:
    name: str
    arg_count: int
    lambda_positions: tuple[int, ...]
    span: ast.Span


@dataclass(frozen=True, slots=True, kw_only=True)
class Analysis:
    references: tuple[Reference, ...]
    calls: tuple[HelperCall, ...]

    @property
    def roots(self) -> frozenset[str]:
        return frozenset(r.root for r in self.references)


def analyze(expression: ast.Expression) -> Analysis:
    references: list[Reference] = []
    calls: list[HelperCall] = []
    _walk(expression.node, frozenset(), references, calls)
    return Analysis(references=tuple(references), calls=tuple(calls))


def _chain(node: ast.Node) -> tuple[ast.Name | None, tuple[str | int, ...]]:
    """Unwind ``a.b[0].c`` into (Name a, ("b", 0, "c")) when every step is constant."""
    path: list[str | int] = []
    while True:
        match node:
            case ast.Member(target=target, name=name):
                path.append(name)
                node = target
            case ast.Index(target=target, index=ast.Literal(value=value)) if isinstance(
                value, str | int
            ) and not isinstance(value, bool):
                path.append(value)
                node = target
            case ast.Name():
                return node, tuple(reversed(path))
            case _:
                return None, ()


def _children(node: ast.Node) -> Iterator[ast.Node]:
    match node:
        case ast.Member(target=t):
            yield t
        case ast.Index(target=t, index=i):
            yield t
            yield i
        case ast.Slice(target=t, start=a, stop=b, step=c):
            yield t
            yield from (x for x in (a, b, c) if x is not None)
        case ast.Projection(source=s, body=b):
            yield s
            yield b
        case ast.Unary(operand=o):
            yield o
        case ast.Binary(left=a, right=b) | ast.BoolOp(left=a, right=b):
            yield a
            yield b
        case ast.Compare(first=f, rest=rest):
            yield f
            yield from (n for _, n in rest)
        case ast.Conditional(test=t, then=a, otherwise=b):
            yield from (t, a, b)
        case ast.Call(args=args) | ast.ListLiteral(items=args):
            yield from args
        case ast.ObjectLiteral(entries=entries):
            yield from (v for _, v in entries)
        case _:
            return


def _walk(
    node: ast.Node, bound: frozenset[str], refs: list[Reference], calls: list[HelperCall]
) -> None:
    if isinstance(node, ast.Member | ast.Index):
        name, path = _chain(node)
        if name is not None:
            if name.name not in bound:
                refs.append(Reference(root=name.name, path=path, span=node.span))
            # Still visit non-constant parts (none for a pure chain).
            return
    if isinstance(node, ast.Name):
        if node.name not in bound:
            refs.append(Reference(root=node.name, path=(), span=node.span))
        return
    if isinstance(node, ast.Lambda):
        _walk(node.body, bound | set(node.params), refs, calls)
        return
    if isinstance(node, ast.Call):
        calls.append(
            HelperCall(
                name=node.name,
                arg_count=len(node.args),
                lambda_positions=tuple(
                    i for i, a in enumerate(node.args) if isinstance(a, ast.Lambda)
                ),
                span=node.span,
            )
        )
    for child in _children(node):
        _walk(child, bound, refs, calls)

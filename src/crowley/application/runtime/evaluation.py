"""Evaluating template value nodes (``with:``, ``emit:``, ``set:`` …) against a scope."""

from collections.abc import Mapping

from crowley.domain.errors import CrowleyError
from crowley.domain.expressions import EvalEnv, evaluate_scalar
from crowley.domain.steps import Expr, ListNode, Lit, MapNode, ValueNode
from crowley.domain.values import Value


def evaluate_node(node: ValueNode, scope: Mapping[str, Value], env: EvalEnv) -> Value:
    """Evaluate every expression inside ``node``; literals are returned as fresh copies."""
    match node:
        case Lit(value=value):
            return value
        case MapNode(entries=entries):
            return {key: evaluate_node(child, scope, env) for key, child in entries}
        case ListNode(items=items):
            return [evaluate_node(child, scope, env) for child in items]
        case Expr():
            return evaluate_expr(node, scope, env)
    raise TypeError(f"not a value node: {node!r}")  # pragma: no cover


def evaluate_expr(node: Expr, scope: Mapping[str, Value], env: EvalEnv) -> Value:
    """Evaluate one ``${{ }}`` scalar; errors carry the node's path and location."""
    try:
        return evaluate_scalar(node.scalar, scope, env)
    except CrowleyError as exc:
        raise exc.with_context(path=str(node.path), location=node.location) from exc.__cause__

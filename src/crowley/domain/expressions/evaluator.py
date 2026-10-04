"""Strict, sandboxed evaluation of compiled expressions (docs/SPEC.md §10.3)."""

import math
from collections.abc import Mapping
from dataclasses import dataclass, field

from crowley.domain.errors import CrowleyError, ExecutionError, LimitError, TemplateSemanticError
from crowley.domain.expressions import ast
from crowley.domain.expressions.embedding import Compiled, Scalar, Text
from crowley.domain.expressions.helpers import HelperTable, fail
from crowley.domain.services import Clock, RandomSource, RegexEngine
from crowley.domain.values import Value, compare, interpolate_text, is_number, type_of, values_equal

MAX_STEPS = 100_000


@dataclass(slots=True, kw_only=True)
class EvalEnv:
    """Everything an evaluation may use besides the scope."""

    helpers: HelperTable = field(default_factory=HelperTable.builtin)
    clock: Clock | None = None
    random: RandomSource | None = None
    regex: RegexEngine | None = None
    max_steps: int = MAX_STEPS


class _Context:
    """Per-evaluation state handed to helpers (``HelperContext``)."""

    __slots__ = ("clock", "max_steps", "random", "regex", "steps")

    def __init__(self, env: EvalEnv) -> None:
        self.clock = env.clock
        self.random = env.random
        self.regex = env.regex
        self.max_steps = env.max_steps
        self.steps = 0

    def tick(self, steps: int = 1) -> None:
        self.steps += steps
        if self.steps > self.max_steps:
            raise LimitError(
                "E703",
                f"expression exceeded the evaluation budget of {self.max_steps} steps",
                hint="split the work into steps or use transform.* functions",
            )


class _Function:
    """A lambda closed over the scope it was created in."""

    __slots__ = ("_evaluator", "_locals", "body", "params")

    def __init__(
        self, node: ast.Lambda, evaluator: "_Evaluator", locals_: Mapping[str, Value]
    ) -> None:
        self.params = node.params
        self.body = node.body
        self._evaluator = evaluator
        self._locals = locals_

    def __call__(self, *args: Value) -> Value:
        if len(args) < len(self.params):
            raise fail(f"lambda expects {len(self.params)} arguments, got {len(args)}")
        bound = dict(self._locals)
        bound.update(zip(self.params, args, strict=False))
        return self._evaluator.eval(self.body, bound, None)


def evaluate(
    expression: ast.Expression, scope: Mapping[str, Value], env: EvalEnv | None = None
) -> Value:
    """Evaluate ``expression`` against ``scope`` (root name → value).

    Raises ``E501`` for evaluation errors, ``E703`` when the step budget is exceeded,
    ``E313``/``E314`` for unknown helpers or wrong arity.
    """
    evaluator = _Evaluator(scope, env or EvalEnv())
    try:
        return evaluator.eval(expression.node, {}, None)
    except ExecutionError as exc:
        raise ExecutionError(
            exc.code,
            f"{exc.message} (in ${{{{ {expression.source} }}}})",
            value=exc.value,
            hint=exc.hint,
            cause=exc.cause,
        ) from None


def evaluate_scalar(
    scalar: Scalar, scope: Mapping[str, Value], env: EvalEnv | None = None
) -> Value:
    """Evaluate a parsed YAML scalar: plain text, one typed expression, or an interpolation."""
    if isinstance(scalar, Text):
        return scalar.value
    if isinstance(scalar, Compiled):
        return evaluate(scalar.expression, scope, env)
    parts: list[str] = []
    for part in scalar.parts:
        if isinstance(part, str):
            parts.append(part)
        else:
            value = evaluate(part, scope, env)
            try:
                parts.append(interpolate_text(value))
            except ExecutionError as exc:
                raise ExecutionError(
                    exc.code,
                    f"{exc.message} (in ${{{{ {part.source} }}}})",
                    value=value,
                    hint=exc.hint,
                ) from None
    return "".join(parts)


def _require_bool(value: Value, what: str) -> bool:
    if not isinstance(value, bool):
        raise fail(
            f"{what} must be true or false, got {type_of(value)}",
            value,
            hint="compare explicitly, e.g. 'x != null' or 'len(xs) > 0'",
        )
    return value


class _Evaluator:
    __slots__ = ("ctx", "env", "scope")

    def __init__(self, scope: Mapping[str, Value], env: EvalEnv) -> None:
        self.scope = scope
        self.env = env
        self.ctx = _Context(env)

    def eval(self, node: ast.Node, locals_: Mapping[str, Value], current: Value) -> Value:
        self.ctx.tick()
        match node:
            case ast.Literal(value=value):
                return value
            case ast.Name(name=name):
                if name in locals_:
                    return locals_[name]
                if name in self.scope:
                    return self.scope[name]
                raise fail(f"unknown name {name!r}")
            case ast.Current():
                return current
            case ast.Member(target=target, name=name, optional=optional):
                return self._member(self.eval(target, locals_, current), name, optional)
            case ast.Index(target=target, index=index):
                return self._index(
                    self.eval(target, locals_, current), self.eval(index, locals_, current)
                )
            case ast.Slice():
                return self._slice(node, locals_, current)
            case ast.Projection(source=source, body=body):
                items = self.eval(source, locals_, current)
                if not isinstance(items, list):
                    raise fail(f"[*] needs a list, got {type_of(items)}", items)
                return [self.eval(body, locals_, item) for item in items]
            case ast.Unary(op="not", operand=operand):
                return not _require_bool(self.eval(operand, locals_, current), "operand of 'not'")
            case ast.Unary(operand=operand):
                value = self.eval(operand, locals_, current)
                if not is_number(value):
                    raise fail(f"cannot negate {type_of(value)}", value)
                return -value  # type: ignore[operator]
            case ast.Binary(op=op, left=left, right=right):
                return _arithmetic(
                    op, self.eval(left, locals_, current), self.eval(right, locals_, current)
                )
            case ast.BoolOp(op=op, left=left, right=right):
                lhs = _require_bool(self.eval(left, locals_, current), f"left operand of '{op}'")
                if (op == "and" and not lhs) or (op == "or" and lhs):
                    return lhs
                return _require_bool(self.eval(right, locals_, current), f"right operand of '{op}'")
            case ast.Compare(first=first, rest=rest):
                left_value = self.eval(first, locals_, current)
                for op, right_node in rest:
                    right_value = self.eval(right_node, locals_, current)
                    if not _compare(op, left_value, right_value):
                        return False
                    left_value = right_value
                return True
            case ast.Conditional(test=test, then=then, otherwise=otherwise):
                chosen = (
                    then
                    if _require_bool(self.eval(test, locals_, current), "condition")
                    else otherwise
                )
                return self.eval(chosen, locals_, current)
            case ast.Call():
                return self._call(node, locals_, current)
            case ast.Lambda():
                raise fail("a lambda can only be passed to a helper")
            case ast.ListLiteral(items=items):
                return [self.eval(item, locals_, current) for item in items]
            case ast.ObjectLiteral(entries=entries):
                return {key: self.eval(value, locals_, current) for key, value in entries}
        raise AssertionError(f"unhandled node {node!r}")  # pragma: no cover

    @staticmethod
    def _member(target: Value, name: str, optional: bool) -> Value:
        if isinstance(target, dict):
            if name in target:
                return target[name]
            if optional:
                return None
            raise fail(
                f"object has no key {name!r}",
                sorted(target)[:20],
                hint=f"use '?.{name}' or default() if the key is optional",
            )
        if target is None and optional:
            return None
        raise fail(f"cannot read {name!r} from {type_of(target)}", target)

    @staticmethod
    def _index(target: Value, index: Value) -> Value:
        if isinstance(target, list | str):
            if isinstance(index, bool) or not isinstance(index, int):
                raise fail(
                    f"{type_of(target)} index must be an integer, got {type_of(index)}", index
                )
            if not -len(target) <= index < len(target):
                raise fail(
                    f"index {index} out of range for {type_of(target)} of length {len(target)}",
                    index,
                )
            return target[index]
        if isinstance(target, dict):
            if not isinstance(index, str):
                raise fail(f"object key must be a string, got {type_of(index)}", index)
            if index not in target:
                raise fail(f"object has no key {index!r}", index)
            return target[index]
        raise fail(f"cannot index {type_of(target)}", target)

    def _slice(self, node: ast.Slice, locals_: Mapping[str, Value], current: Value) -> Value:
        target = self.eval(node.target, locals_, current)
        if not isinstance(target, list | str):
            raise fail(f"cannot slice {type_of(target)}", target)
        bounds: list[int | None] = []
        for part in (node.start, node.stop, node.step):
            value = None if part is None else self.eval(part, locals_, current)
            if value is not None and (isinstance(value, bool) or not isinstance(value, int)):
                raise fail(f"slice bounds must be integers, got {type_of(value)}", value)
            bounds.append(value)
        if bounds[2] == 0:
            raise fail("slice step must not be zero")
        return target[bounds[0] : bounds[1] : bounds[2]]

    def _call(self, node: ast.Call, locals_: Mapping[str, Value], current: Value) -> Value:
        spec = self.env.helpers.get(node.name)
        if spec is None:
            raise TemplateSemanticError("E313", f"unknown helper {node.name!r}")
        if not spec.accepts(len(node.args)):
            raise TemplateSemanticError(
                "E314", f"{node.name}() takes {spec.arity_text}, got {len(node.args)}"
            )
        args: list[object] = []
        for position, arg in enumerate(node.args):
            if position in spec.lambda_args:
                if not isinstance(arg, ast.Lambda):
                    raise fail(
                        f"argument {position + 1} of {node.name}() must be a lambda "
                        "like 'x => x.id'"
                    )
                args.append(_Function(arg, self, locals_))
            elif isinstance(arg, ast.Lambda):
                raise fail(f"argument {position + 1} of {node.name}() must not be a lambda")
            else:
                args.append(self.eval(arg, locals_, current))
        try:
            return spec.fn(self.ctx, *args)
        except CrowleyError:
            raise
        except RecursionError:  # pragma: no cover - deeply nested data
            raise fail(f"{node.name}(): value nested too deeply") from None


def _arithmetic(op: str, a: Value, b: Value) -> Value:
    if op == "+":
        if is_number(a) and is_number(b):
            return a + b  # type: ignore[operator]
        if isinstance(a, str) and isinstance(b, str):
            return a + b
        if isinstance(a, list) and isinstance(b, list):
            return a + b
        raise fail(f"cannot add {type_of(a)} and {type_of(b)}", [a, b])
    if not (is_number(a) and is_number(b)):
        raise fail(f"'{op}' needs numbers, got {type_of(a)} and {type_of(b)}", [a, b])
    x: float = a  # type: ignore[assignment]
    y: float = b  # type: ignore[assignment]
    if op == "-":
        return x - y
    if op == "*":
        return x * y
    if y == 0:
        raise fail(f"division by zero in '{op}'")
    if op == "/":
        return x / y
    if op == "//":
        return x // y if isinstance(x, int) and isinstance(y, int) else math.floor(x / y)
    return x % y


def _compare(op: str, a: Value, b: Value) -> bool:
    match op:
        case "==":
            return values_equal(a, b)
        case "!=":
            return not values_equal(a, b)
        case "<":
            return compare(a, b) < 0
        case "<=":
            return compare(a, b) <= 0
        case ">":
            return compare(a, b) > 0
        case ">=":
            return compare(a, b) >= 0
        case "in":
            return _contains(b, a)
        case _:  # "not in"
            return not _contains(b, a)


def _contains(container: Value, item: Value) -> bool:
    if isinstance(container, str):
        if not isinstance(item, str):
            raise fail(f"'in' on a string needs a string, got {type_of(item)}", item)
        return item in container
    if isinstance(container, list):
        return any(values_equal(item, element) for element in container)
    if isinstance(container, dict):
        if not isinstance(item, str):
            raise fail(f"'in' on an object needs a string key, got {type_of(item)}", item)
        return item in container
    raise fail(f"'in' needs a string, list or object, got {type_of(container)}", container)

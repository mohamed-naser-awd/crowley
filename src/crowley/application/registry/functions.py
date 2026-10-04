"""Python function handlers: ``handler(ctx, **kwargs)`` binding and the ``@function`` decorator.

The handler signature is inspected once, at registration (docs/SPEC.md §11.1). At call time
Crowley passes every validated ``with:`` argument plus ``prev`` as keyword arguments, and the
binding keeps only what the handler accepts.
"""

import inspect
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from crowley.domain.common import SemVer
from crowley.domain.errors import ConfigurationError
from crowley.domain.functions import FunctionKind, FunctionSpec
from crowley.domain.values import Value

Handler = Callable[..., Any]


@dataclass(frozen=True, slots=True)
class HandlerBinding:
    """How a handler's signature receives ``kwargs``: all of them, or only the names it lists."""

    params: frozenset[str]
    var_kw: bool
    is_async: bool

    @classmethod
    def from_handler(cls, handler: Handler, spec: FunctionSpec) -> "HandlerBinding":
        """Inspect ``handler`` against ``spec``; raises E903 for incompatible signatures."""
        name = spec.name
        try:
            signature = inspect.signature(handler)
        except (TypeError, ValueError) as exc:
            raise ConfigurationError("E903", f"{name}: handler is not inspectable: {exc}") from exc
        parameters = list(signature.parameters.values())
        positional = (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD)
        if not parameters or parameters[0].kind not in positional:
            raise ConfigurationError(
                "E903",
                f"{name}: the handler must take the function context as its first parameter",
                hint="def handler(ctx, **kwargs): ...",
            )
        is_async = inspect.iscoroutinefunction(handler)
        if spec.kind is FunctionKind.BLOCK and not is_async:
            raise ConfigurationError(
                "E903", f"{name}: block functions must be async (they await ctx.body.run)"
            )
        params: set[str] = set()
        var_kw = False
        for param in parameters[1:]:
            if param.kind is inspect.Parameter.VAR_KEYWORD:
                var_kw = True
                continue
            if param.kind is inspect.Parameter.VAR_POSITIONAL:
                continue
            if param.kind is inspect.Parameter.POSITIONAL_ONLY:
                raise ConfigurationError(
                    "E903",
                    f"{name}: parameter {param.name!r} is positional-only; arguments are "
                    "passed by keyword",
                )
            params.add(param.name)
            required = param.default is inspect.Parameter.empty
            if required and param.name != "prev" and param.name not in spec.required_args:
                raise ConfigurationError(
                    "E903",
                    f"{name}: parameter {param.name!r} has no default but is not a required "
                    "argument of the input schema",
                    hint=f"give it a default or add {param.name!r} to the schema's 'required'",
                )
        return cls(params=frozenset(params), var_kw=var_kw, is_async=is_async)

    def bind(self, kwargs: Mapping[str, Value]) -> dict[str, Value]:
        """The keyword arguments this handler receives."""
        if self.var_kw:
            return dict(kwargs)
        return {key: value for key, value in kwargs.items() if key in self.params}


@dataclass(frozen=True, slots=True)
class RegisteredFunction:
    """A function spec with its Python handler."""

    spec: FunctionSpec
    handler: Handler
    binding: HandlerBinding

    @classmethod
    def create(cls, spec: FunctionSpec, handler: Handler) -> "RegisteredFunction":
        if not callable(handler):
            raise ConfigurationError("E903", f"{spec.name}: handler is not callable")
        return cls(spec, handler, HandlerBinding.from_handler(handler, spec))

    @property
    def name(self) -> str:
        return self.spec.name


def function(
    *,
    name: str,
    version: str = "1.0.0",
    input: Mapping[str, Any] | None = None,
    output: Mapping[str, Any] | None = None,
    kind: str | FunctionKind = FunctionKind.PLAIN,
    prev: Mapping[str, Any] | None = None,
    description: str = "",
    events: tuple[str, ...] = (),
    pure: bool = False,
    body_bindings: tuple[str, ...] = (),
) -> Callable[[Handler], RegisteredFunction]:
    """Declare a Crowley function from a Python handler (docs/SPEC.md §11.1).

    ``@function(name="acme.slugify", input={...}, output={...})`` turns the decorated
    ``async def slugify(ctx, **kwargs)`` into a :class:`RegisteredFunction` ready for
    ``Crowley(functions=[...])`` or ``crowley.register(...)``.
    """

    def decorate(handler: Handler) -> RegisteredFunction:
        spec = FunctionSpec(
            name=name,
            version=SemVer.parse(version),
            kind=FunctionKind(kind),
            input=input if input is not None else {"type": "object"},
            output=output if output is not None else {},
            prev=prev,
            description=description or inspect.getdoc(handler) or "",
            events=tuple(events),
            pure=pure,
            body_bindings=tuple(body_bindings),
        )
        return RegisteredFunction.create(spec, handler)

    return decorate

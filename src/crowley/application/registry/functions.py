"""Python functions for templates: the ``@function`` decorator and handler binding.

The simplest form needs nothing but type hints::

    @function
    def slugify(text: str, sep: str = "-") -> str:
        return sep.join(text.lower().split())

That registers ``app.slugify`` with an input schema built from the parameters (``text`` required,
``sep`` defaulting to ``"-"``) and an output schema from the return annotation. A handler gets the
function context only if its first parameter asks for it (``ctx``/``context`` or annotated
``FunctionContext``), and only the keyword arguments it names unless it takes ``**kwargs``.
The signature is inspected once, at registration (docs/SPEC.md §11.1).
"""

import inspect
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass
from typing import Any, overload

from crowley.application.registry.inference import infer_schemas, wants_context
from crowley.domain.common import SemVer, is_function_name
from crowley.domain.errors import ConfigurationError
from crowley.domain.functions import FunctionKind, FunctionSpec
from crowley.domain.values import Value

Handler = Callable[..., Any]

DEFAULT_NAMESPACE = "app"
"""Namespace of functions registered without an explicit name: ``use: app.<function name>``."""


@dataclass(frozen=True, slots=True)
class HandlerBinding:
    """How a handler receives its arguments: with or without ``ctx``; all kwargs or named ones."""

    params: frozenset[str]
    var_kw: bool
    is_async: bool
    wants_ctx: bool = True

    @classmethod
    def from_handler(cls, handler: Handler, spec: FunctionSpec) -> "HandlerBinding":
        """Inspect ``handler`` against ``spec``; raises E903 for incompatible signatures."""
        name = spec.name
        try:
            signature = inspect.signature(handler)
        except (TypeError, ValueError) as exc:
            raise ConfigurationError("E903", f"{name}: handler is not inspectable: {exc}") from exc
        parameters = list(signature.parameters.values())
        wants_ctx = wants_context(handler)
        is_async = inspect.iscoroutinefunction(handler)
        if spec.kind is FunctionKind.BLOCK:
            if not is_async:
                raise ConfigurationError(
                    "E903", f"{name}: block functions must be async (they await ctx.body.run)"
                )
            if not wants_ctx:
                raise ConfigurationError(
                    "E903",
                    f"{name}: block functions must take the function context first",
                    hint="async def handler(ctx, **kwargs): ...",
                )
        params: set[str] = set()
        var_kw = False
        for param in parameters[1:] if wants_ctx else parameters:
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
        return cls(params=frozenset(params), var_kw=var_kw, is_async=is_async, wants_ctx=wants_ctx)

    def bind(self, kwargs: Mapping[str, Value]) -> dict[str, Value]:
        """The keyword arguments this handler receives."""
        if self.var_kw:
            return dict(kwargs)
        return {key: value for key, value in kwargs.items() if key in self.params}


@dataclass(frozen=True, slots=True)
class RegisteredFunction:
    """A function spec with its Python handler. Calling it calls the handler directly."""

    spec: FunctionSpec
    handler: Handler
    binding: HandlerBinding

    @classmethod
    def create(cls, spec: FunctionSpec, handler: Handler) -> "RegisteredFunction":
        if not callable(handler):
            raise ConfigurationError("E903", f"{spec.name}: handler is not callable")
        return cls(spec, handler, HandlerBinding.from_handler(handler, spec))

    @classmethod
    def from_callable(cls, handler: Handler, **options: Any) -> "RegisteredFunction":
        """Wrap a plain Python function; schemas come from its type hints."""
        return function(**options)(handler)

    @property
    def name(self) -> str:
        return self.spec.name

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        """The decorated function stays callable (and testable) as plain Python."""
        return self.handler(*args, **kwargs)


class FunctionRegistry:
    """A reusable, named bundle of functions, usually defined in its own module.

    ::

        # scraping/functions.py
        functions = FunctionRegistry("acme")

        @functions.function
        def clean_price(text: str) -> float:     # registered as acme.clean_price
            ...

        # main.py
        cw = Crowley(functions=functions)        # or cw.register(functions), plugins=[functions]

    Functions named without a namespace go into the registry's ``namespace``.
    """

    def __init__(self, namespace: str = DEFAULT_NAMESPACE) -> None:
        if not is_function_name(f"{namespace}.x"):
            raise ConfigurationError("E903", f"invalid function namespace {namespace!r}")
        self.namespace = namespace
        self._functions: dict[str, RegisteredFunction | FunctionSpec] = {}

    def function(self, handler_or_name: Handler | str | None = None, /, **options: Any) -> Any:
        """Decorator: ``@functions.function``, ``@functions.function("x")`` or with options."""
        if callable(handler_or_name):
            return self.add(handler_or_name, **options)

        def decorate(handler: Handler) -> Any:
            if isinstance(handler_or_name, str):
                options.setdefault("name", handler_or_name)
            return self.add(handler, **options)

        return decorate

    def add(self, fn: Handler | RegisteredFunction | FunctionSpec, **options: Any) -> Any:
        """Add a function (plain, decorated or a spec). Returns what was stored."""
        if isinstance(fn, RegisteredFunction | FunctionSpec):
            item: RegisteredFunction | FunctionSpec = fn
        else:
            name = options.pop("name", None) or fn.__name__
            if "." not in name:
                name = f"{self.namespace}.{name}"
            item = function(name=name, **options)(fn)
        if item.name in self._functions:
            raise ConfigurationError(
                "E901", f"function {item.name!r} is already in registry {self.namespace!r}"
            )
        self._functions[item.name] = item
        return item

    def include(self, other: "FunctionRegistry") -> None:
        """Copy every function of another registry into this one."""
        for item in other:
            self.add(item)

    def register(self, registry: Any) -> None:
        """Plugin protocol: install every function into a Crowley ``Registry``."""
        for item in self:
            registry.add_function(item)

    def __iter__(self) -> Iterator[RegisteredFunction | FunctionSpec]:
        return iter(list(self._functions.values()))

    def __len__(self) -> int:
        return len(self._functions)

    def __contains__(self, name: object) -> bool:
        return name in self._functions

    def __getitem__(self, name: str) -> RegisteredFunction | FunctionSpec:
        return self._functions[name]

    def __repr__(self) -> str:
        return f"FunctionRegistry({self.namespace!r}, {len(self)} function(s))"


def as_registered(
    fn: FunctionSpec | RegisteredFunction | Handler,
) -> FunctionSpec | RegisteredFunction:
    """Accept specs, decorated functions, and plain callables (wrapped with inferred schemas)."""
    if isinstance(fn, FunctionSpec | RegisteredFunction):
        return fn
    if callable(fn):
        return RegisteredFunction.from_callable(fn)
    raise ConfigurationError("E903", f"cannot register {fn!r} as a function")


@overload
def function(handler: Handler, /) -> RegisteredFunction: ...


@overload
def function(
    name: str | None = None,
    /,
    *,
    version: str = ...,
    input: Mapping[str, Any] | None = ...,
    output: Mapping[str, Any] | None = ...,
    kind: str | FunctionKind = ...,
    prev: Mapping[str, Any] | None = ...,
    description: str = ...,
    events: tuple[str, ...] = ...,
    pure: bool = ...,
    body_bindings: tuple[str, ...] = ...,
) -> Callable[[Handler], RegisteredFunction]: ...


@overload
def function(
    *,
    name: str | None = None,
    version: str = ...,
    input: Mapping[str, Any] | None = ...,
    output: Mapping[str, Any] | None = ...,
    kind: str | FunctionKind = ...,
    prev: Mapping[str, Any] | None = ...,
    description: str = ...,
    events: tuple[str, ...] = ...,
    pure: bool = ...,
    body_bindings: tuple[str, ...] = ...,
) -> Callable[[Handler], RegisteredFunction]: ...


def function(
    handler_or_name: Handler | str | None = None,
    /,
    *,
    name: str | None = None,
    version: str = "1.0.0",
    input: Mapping[str, Any] | None = None,
    output: Mapping[str, Any] | None = None,
    kind: str | FunctionKind = FunctionKind.PLAIN,
    prev: Mapping[str, Any] | None = None,
    description: str = "",
    events: tuple[str, ...] = (),
    pure: bool = False,
    body_bindings: tuple[str, ...] = (),
) -> RegisteredFunction | Callable[[Handler], RegisteredFunction]:
    """Declare a Crowley function from a Python handler (docs/SPEC.md §11.1).

    ``@function`` alone, ``@function("acme.slugify")`` or ``@function(name=..., input=...)``.
    Without a name the function is ``app.<function name>``; without ``input``/``output`` the
    schemas are inferred from the type hints. ``Crowley(functions=[...])`` and
    ``crowley.register(...)`` take the result, and ``@crowley.function`` registers in one go.
    """
    if isinstance(handler_or_name, str):
        name = handler_or_name

    def decorate(handler: Handler) -> RegisteredFunction:
        qualified = name or f"{DEFAULT_NAMESPACE}.{handler.__name__}"
        if "." not in qualified and is_function_name(f"{DEFAULT_NAMESPACE}.{qualified}"):
            qualified = f"{DEFAULT_NAMESPACE}.{qualified}"
        inferred = infer_schemas(handler)
        spec = FunctionSpec(
            name=qualified,
            version=SemVer.parse(version),
            kind=FunctionKind(kind),
            input=input if input is not None else inferred.input,
            output=output if output is not None else inferred.output,
            prev=prev if prev is not None else inferred.prev,
            description=description or inspect.getdoc(handler) or "",
            events=tuple(events),
            pure=pure,
            body_bindings=tuple(body_bindings),
        )
        return RegisteredFunction.create(spec, handler)

    if callable(handler_or_name):
        return decorate(handler_or_name)
    return decorate

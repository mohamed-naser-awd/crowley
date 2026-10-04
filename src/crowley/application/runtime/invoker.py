"""Function invocation for ``use`` steps (ARCHITECTURE §4.4, SPEC §11).

evaluate ``with:`` (lazy args stay unevaluated) → schema defaults → ``x-crowley-from-prev`` →
``kwargs = {**args, "prev": prev}`` → validate (E403) → ``function.before`` → revalidate if
changed → ``handler(ctx, **bound)`` → validate result (E407) → ``function.after`` → revalidate
if changed. Handler failures fire ``function.error`` (retry, replace) and become E502.
"""

import asyncio
import copy
import inspect
from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from crowley.application.ports import CompiledSchema, SchemaValidator
from crowley.application.registry import RegisteredFunction, Registry
from crowley.application.runtime.context import Body, FunctionContext
from crowley.application.runtime.evaluation import evaluate_node
from crowley.application.runtime.executor import Executor
from crowley.application.runtime.frame import Frame
from crowley.application.runtime.pipeline import step_info
from crowley.application.runtime.signals import HandlerResult
from crowley.application.runtime.state import RunState
from crowley.domain.errors import (
    ConfigurationError,
    ControlError,
    CrowleyError,
    ExecutionError,
    LimitError,
    ValidationError,
)
from crowley.domain.events import (
    FunctionAfter,
    FunctionBefore,
    FunctionErrorEvent,
    Replace,
    Retry,
    Skip,
)
from crowley.domain.functions import FunctionKind
from crowley.domain.steps import MapNode, UseStep
from crowley.domain.template import Template, TemplateFunction
from crowley.domain.values import Value

if TYPE_CHECKING:
    from crowley.application.adapters.service import AdapterService


@dataclass(frozen=True, slots=True)
class _Schemas:
    input: CompiledSchema
    prev: CompiledSchema | None
    output: CompiledSchema


async def _template_function_placeholder(ctx: FunctionContext, **kwargs: Value) -> Value:
    raise AssertionError("template functions are run by the invoker")  # pragma: no cover


class FunctionInvoker:
    """Runs ``use`` steps: Python functions from the registry and ``local.*`` template functions."""

    def __init__(
        self,
        state: RunState,
        executor: Executor,
        *,
        registry: Registry,
        schemas: SchemaValidator,
        template: Template,
        adapters: "AdapterService | None" = None,
    ) -> None:
        self._adapters = adapters
        self._state = state
        self._executor = executor
        self._registry = registry
        self._schema_validator = schemas
        self._template = template
        self._compiled: dict[str, _Schemas] = {}
        self._bodies: dict[str, TemplateFunction] = {}
        self._locals: dict[str, RegisteredFunction] = {}
        for tf in template.functions.values():
            spec = tf.as_function_spec(template.metadata.version)
            self._locals[spec.name] = RegisteredFunction.create(
                spec, _template_function_placeholder
            )
            self._bodies[spec.name] = tf
        executor.pipeline.register(
            "use", self._use, prepare=self._prepare, revalidate=self._revalidate
        )

    # ── lookup ─────────────────────────────────────────────────────────────────

    def resolve(self, name: str) -> RegisteredFunction:
        found = self._locals.get(name) or self._registry.handler(name)
        if found is not None:
            return found
        if name.startswith("template:"):
            raise ConfigurationError(
                "E903", f"template composition ({name}) is not supported by this runtime yet"
            )
        if self._registry.function(name) is not None:
            raise ConfigurationError(
                "E903",
                f"function {name!r} has no implementation in this runtime",
                hint="register it with a handler, e.g. via @function",
            )
        raise ConfigurationError("E903", f"unknown function {name!r}")

    def _schemas(self, fn: RegisteredFunction) -> _Schemas:
        cached = self._compiled.get(fn.name)
        if cached is None:
            definitions = self._template.schemas if fn.name in self._bodies else None
            compile_ = self._schema_validator.compile
            spec = fn.spec
            cached = _Schemas(
                input=compile_(_without_lazy(spec.input, spec.lazy_args), definitions=definitions),
                prev=compile_(spec.prev, definitions=definitions) if spec.prev else None,
                output=compile_(spec.output, definitions=definitions),
            )
            self._compiled[fn.name] = cached
        return cached

    # ── step hooks ─────────────────────────────────────────────────────────────

    async def _prepare(self, step: UseStep, frame: Frame, prev: Value) -> dict[str, Any]:
        """Evaluate ``with:``; lazy arguments are passed as unevaluated template values."""
        fn = self.resolve(step.uses)
        node = step.args
        if node is None:
            return {}
        env = self._state.env
        if not isinstance(node, MapNode):  # pragma: no cover - the meta-schema requires a mapping
            raise TypeError(f"with: at {node.path} is not a mapping")
        lazy = fn.spec.lazy_args
        scope = frame.scope(prev)
        return {
            key: child if key in lazy else evaluate_node(child, scope, env)
            for key, child in node.entries
        }

    async def _use(
        self, step: UseStep, frame: Frame, prev: Value, kwargs: dict[str, Value] | None
    ) -> HandlerResult:
        fn = self.resolve(step.uses)
        body = None
        if fn.spec.kind is FunctionKind.BLOCK and step.body is not None:
            body = Body(self._executor, step.body, frame, prev)
        value = await self.invoke(fn, step, frame, prev, kwargs or {}, body)
        return HandlerResult(value=value)

    def _revalidate(self, step: UseStep, value: Value) -> None:
        fn = self.resolve(step.uses)
        self._check_result(fn, value, str(step.path))

    # ── invocation ─────────────────────────────────────────────────────────────

    async def invoke(
        self,
        fn: RegisteredFunction,
        step: UseStep,
        frame: Frame,
        prev: Value,
        args: Mapping[str, Value],
        body: Body | None = None,
    ) -> Value:
        state, spec, path = self._state, fn.spec, str(step.path)
        info = step_info(step)
        kwargs: dict[str, Value] = dict(args)
        for name, schema in spec.properties.items():
            if name not in kwargs and isinstance(schema, Mapping) and "default" in schema:
                kwargs[name] = copy.deepcopy(schema["default"])
        for name in spec.from_prev_args:
            if name not in kwargs:
                kwargs[name] = prev
        kwargs["prev"] = prev
        self._check_args(fn, kwargs, path)

        before, outcome = await state.publish(
            FunctionBefore, step=info, frame=frame, scope_prev=prev, function=spec.name,
            kwargs=kwargs,
        )  # fmt: skip
        if isinstance(outcome.action, Skip):
            return outcome.action.result
        kwargs = before.kwargs
        if outcome.changed:
            self._check_args(fn, kwargs, path)
        pending: Replace | None = outcome.action if isinstance(outcome.action, Replace) else None

        retries = 0
        while True:
            if pending is not None:
                result, pending = pending.value, None
            else:
                try:
                    result = await self._call(fn, info, frame, kwargs, body)
                except CrowleyError as exc:
                    _, outcome = await state.publish(
                        FunctionErrorEvent, step=info, frame=frame, scope_prev=prev,
                        function=spec.name, error=exc, attempt=retries + 1,
                    )  # fmt: skip
                    action = outcome.action
                    if isinstance(exc, ControlError | LimitError):
                        raise
                    if isinstance(action, Retry) and retries < state.guard.max_retries:
                        retries += 1
                        state.stats.retries += 1
                        if action.after:
                            await state.sleep(action.after)
                        continue
                    if not isinstance(action, Replace):
                        raise
                    result = action.value
            self._check_result(fn, result, path)
            after, outcome = await state.publish(
                FunctionAfter, step=info, frame=frame, scope_prev=prev, function=spec.name,
                result=result,
            )  # fmt: skip
            action = outcome.action
            if isinstance(action, Retry) and retries < state.guard.max_retries:
                retries += 1
                state.stats.retries += 1
                if action.after:
                    await state.sleep(action.after)
                continue
            result = action.value if isinstance(action, Replace) else after.result
            if outcome.changed:
                self._check_result(fn, result, path)
            return result

    async def _call(
        self,
        fn: RegisteredFunction,
        info: Any,
        frame: Frame,
        kwargs: dict[str, Value],
        body: Body | None,
    ) -> Value:
        local = self._bodies.get(fn.name)
        if local is not None:
            return await self._run_template_function(local, frame, kwargs)
        ctx = FunctionContext(
            state=self._state,
            spec=fn.spec,
            step=info,
            frame=frame,
            prev=kwargs.get("prev"),
            body=body,
            defaults=self._template.defaults,
            adapters=self._adapters,
        )
        bound = fn.binding.bind(kwargs)
        try:
            if fn.binding.is_async:
                result = await fn.handler(ctx, **bound)
            else:
                result = await asyncio.to_thread(fn.handler, ctx, **bound)
                if inspect.isawaitable(result):
                    result = await result
        except CrowleyError:
            raise
        except Exception as exc:  # any handler failure is E502
            raise ExecutionError(
                "E502",
                f"function {fn.name} raised {type(exc).__name__}: {exc}",
                path=info.path,
                cause=exc,
            ) from exc
        return result  # type: ignore[no-any-return]

    async def _run_template_function(
        self, tf: TemplateFunction, caller: Frame, kwargs: dict[str, Value]
    ) -> Value:
        """Run a ``local.*`` body in an isolated scope: ``args``, ``secrets``, ``run`` only."""
        roots = {
            "args": dict(kwargs),
            "secrets": caller.roots.get("secrets", {}),
            "run": caller.roots.get("run", {}),
        }
        frame = Frame(roots=roots, owner=True, depth=caller.depth + 1, iteration=caller.iteration)
        outcome = await self._executor.run_owner(tf.steps, frame, kwargs.get("prev"))
        return outcome.value

    # ── validation ─────────────────────────────────────────────────────────────

    def _check_args(self, fn: RegisteredFunction, kwargs: Mapping[str, Value], path: str) -> None:
        schemas = self._schemas(fn)
        lazy = fn.spec.lazy_args  # present for `required`, unchecked otherwise
        args = {k: (None if k in lazy else v) for k, v in kwargs.items() if k != "prev"}
        for violation in schemas.input.validate(args):
            where = f"{path}.with{_suffix(str(violation.path))}"
            raise ValidationError(
                "E403", f"{fn.name}: invalid argument: {violation.message}", path=where
            )
        if schemas.prev is not None:
            for violation in schemas.prev.validate(kwargs.get("prev")):
                raise ValidationError(
                    "E403", f"{fn.name}: invalid prev: {violation.message}", path=path
                )

    def _check_result(self, fn: RegisteredFunction, result: Value, path: str) -> None:
        for violation in self._schemas(fn).output.validate(result):
            raise ValidationError(
                "E407",
                f"{fn.name}: invalid result{_suffix(str(violation.path), ' at ')}: "
                f"{violation.message}",
                path=path,
                value=result,
            )


def _without_lazy(schema: Mapping[str, Any], lazy: frozenset[str]) -> Mapping[str, Any]:
    """The input schema with lazy properties accepting anything (they're checked by the handler)."""
    if not lazy:
        return schema
    properties = dict(schema.get("properties", {}))
    properties.update({name: {} for name in lazy})
    return {**schema, "properties": properties}


def _suffix(path: str, prefix: str = ".") -> str:
    if not path or path == "<root>":
        return ""
    return path if path.startswith("[") and prefix == "." else f"{prefix}{path}"

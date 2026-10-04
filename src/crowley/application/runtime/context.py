"""What a function handler sees: ``FunctionContext`` and the ``Body`` of block functions (§11.1)."""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, cast

from crowley.application.runtime.evaluation import evaluate_node
from crowley.application.runtime.frame import Frame
from crowley.application.runtime.state import RunState
from crowley.domain.errors import ConfigurationError, ExchangeError
from crowley.domain.events import Action, CustomEvent, LogEvent, StepInfo
from crowley.domain.functions import FunctionSpec, Outcome
from crowley.domain.steps import Block, Expr, ListNode, Lit, MapNode, ValueNode
from crowley.domain.values import Value

if TYPE_CHECKING:
    from contextlib import AbstractAsyncContextManager

    from crowley.application.adapters.base import BaseAdapter
    from crowley.application.adapters.service import AdapterService
    from crowley.application.runtime.executor import Executor
    from crowley.domain.adapters import AdapterSpec

_UNSET: Any = object()


class Body:
    """The ``do:`` block of a block function. Each ``run`` is one block-owner invocation."""

    def __init__(self, executor: "Executor", block: Block, frame: Frame, prev: Value) -> None:
        self._executor = executor
        self._block = block
        self._frame = frame
        self._prev = prev

    async def run(
        self, *, input: Value = _UNSET, bindings: Mapping[str, Value] | None = None
    ) -> Outcome:
        """Run the body once. ``input`` is its initial ``prev`` (default: the function's own)."""
        prev = self._prev if input is _UNSET else input
        frame = self._frame.child(owner=True, bindings=bindings)
        return await self._executor.run_owner(self._block, frame, prev)


@dataclass(frozen=True, slots=True)
class Emitted:
    """Result of ``ctx.emit_event``: the (possibly changed) data and the winning action."""

    data: dict[str, Value]
    action: Action | None


class FunctionContext:
    """Passed as the first argument of every handler."""

    def __init__(
        self,
        *,
        state: RunState,
        spec: FunctionSpec,
        step: StepInfo,
        frame: Frame,
        prev: Value,
        body: Body | None = None,
        defaults: Mapping[str, ValueNode] | None = None,
        adapters: "AdapterService | None" = None,
    ) -> None:
        self._state = state
        self._spec = spec
        self._frame = frame
        self._prev = prev
        self._defaults = defaults or {}
        self._adapters = adapters
        self.step = step
        self.body = body

    @property
    def run(self) -> Mapping[str, Value]:
        """Run info: ``id``, ``template_id``, ``operation``, ``started_at``."""
        run = self._frame.roots.get("run")
        return run if isinstance(run, Mapping) else {}

    @property
    def function(self) -> FunctionSpec:
        return self._spec

    @property
    def cancelled(self) -> bool:
        return self._state.guard.cancel_requested

    @property
    def defaults(self) -> dict[str, Value]:
        """``defaults.<namespace>`` of the template, evaluated in the caller's scope."""
        return self.defaults_for(self._spec.namespace)

    def defaults_for(self, namespace: str) -> dict[str, Value]:
        node = self._defaults.get(namespace)
        if node is None:
            return {}
        value = evaluate_node(node, self._frame.scope(self._prev), self._state.env)
        return value if isinstance(value, dict) else {}

    def evaluate(self, expression: Any, bindings: Mapping[str, Value] | None = None) -> Value:
        """Evaluate a lazy argument (an unevaluated template value) with extra ``bindings``."""
        scope = self._frame.scope(self._prev, bindings)
        if isinstance(expression, Lit | Expr | MapNode | ListNode):
            return evaluate_node(expression, scope, self._state.env)
        return cast(
            Value, expression
        )  # already a plain value (e.g. a literal replaced by a notifier)

    async def emit_event(self, name: str, **data: Value) -> Emitted:
        """Fire a custom event declared in the function's ``events``."""
        if name not in self._spec.events:
            raise ConfigurationError(
                "E903",
                f"{self._spec.name} emitted undeclared event {name!r}",
                hint="list it in the function's events",
            )
        event, outcome = await self._state.publish(
            CustomEvent,
            step=self.step,
            frame=self._frame,
            scope_prev=self._prev,
            event_name=name,
            data=dict(data),
        )
        return Emitted(event.data, outcome.action)

    async def log(self, level: str, message: str) -> None:
        await self._state.publish(
            LogEvent,
            step=self.step,
            frame=self._frame,
            scope_prev=self._prev,
            level=level,
            message=message,
        )

    async def exchange(self, adapter: str, request: Mapping[str, Any], **options: Any) -> Any:
        """Run one exchange through the shared pipeline (SPEC §13.3); returns the response."""
        return await self._service(adapter).exchange(
            adapter,
            request,
            options=options,
            defaults=self.defaults_for(adapter),
            step=self.step,
            frame=self._frame,
            prev=self._prev,
        )

    def adapter(self, name: str) -> "AdapterHandle":
        """This process's view of an adapter: ``.spec``, ``.session()``, ``.exchange()``."""
        service = self._service(name)
        return AdapterHandle(self, service, service.adapter(name))

    def _service(self, adapter: str) -> "AdapterService":
        if self._adapters is None:
            raise ExchangeError("E607", f"adapter {adapter!r} is not available in this runtime")
        return self._adapters

    def extractor(self, name: str) -> Any:
        raise ExchangeError("E607", f"extractor {name!r} is not available in this runtime")


class AdapterHandle:
    """Returned by ``ctx.adapter(name)``."""

    def __init__(
        self, ctx: FunctionContext, service: "AdapterService", adapter: "BaseAdapter"
    ) -> None:
        self._ctx = ctx
        self._service = service
        self.instance = adapter

    @property
    def name(self) -> str:
        return self.instance.name

    @property
    def spec(self) -> "AdapterSpec":
        return self.instance.spec

    async def session(self) -> Any:
        return await self._service.session(self.name)

    async def exchange(self, request: Mapping[str, Any], **options: Any) -> Any:
        return await self._ctx.exchange(self.name, request, **options)

    def isolated(self, **seed: Any) -> "AbstractAsyncContextManager[Any]":
        """A fresh session for the duration of a block (e.g. ``http.session``)."""
        return self._service.isolated(self.name, **seed)

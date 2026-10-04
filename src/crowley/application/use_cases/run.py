"""Running one operation: input/secret resolution, execution and output (ARCHITECTURE §4.3)."""

import asyncio
import copy
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from crowley.application.events import EventBus
from crowley.application.ports import SchemaValidator, SecretsProvider
from crowley.application.registry import Registry
from crowley.application.runtime import (
    Executor,
    Frame,
    FunctionInvoker,
    OutputCollector,
    RunState,
)
from crowley.application.runtime.evaluation import evaluate_node
from crowley.domain.common import TemplatePath
from crowley.domain.errors import ConfigurationError, CrowleyError, LimitError, ValidationError
from crowley.domain.events import (
    InputsResolve,
    OutputBeforeValidate,
    OutputValidated,
    RunEnd,
    RunErrorEvent,
    RunInfo,
    RunStart,
)
from crowley.domain.expressions import EvalEnv
from crowley.domain.services import Clock
from crowley.domain.template import Limits, Operation, OutputMode, Template
from crowley.domain.values import Value, assert_output_safe

REDACTED = "***"


@dataclass(frozen=True, slots=True, kw_only=True)
class RunResult:
    """The outcome of one process run (SPEC §20)."""

    output: Value
    items: list[Value]
    stats: dict[str, int]
    run_id: str
    operation: str
    trace: list[dict[str, Any]] | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class PreparedRun:
    """A selected operation with validated inputs and resolved secrets."""

    template: Template
    operation: Operation
    inputs: dict[str, Value]
    secrets: dict[str, str] = field(repr=False)
    limits: Limits | None = None


class RunOperation:
    """Prepares runs (``E904``/``E401``/``E402``) and executes them."""

    def __init__(
        self,
        *,
        registry: Registry,
        schemas: SchemaValidator,
        env_factory: Callable[[], EvalEnv],
        clock: Clock | None = None,
        limits: Limits | None = None,
        secrets: SecretsProvider | None = None,
    ) -> None:
        self._registry = registry
        self._schemas = schemas
        self._env_factory = env_factory
        self._clock = clock
        self._limits = limits or Limits()
        self._secrets = secrets

    # ── preparation (at cw.init) ───────────────────────────────────────────────

    def prepare(
        self,
        template: Template,
        operation: str | None = None,
        *,
        inputs: Mapping[str, Value] | None = None,
        secrets: Mapping[str, str] | None = None,
        limits: Limits | None = None,
    ) -> PreparedRun:
        op = _select(template, operation)
        return PreparedRun(
            template=template,
            operation=op,
            inputs=self.validate_inputs(template, op, dict(inputs or {})),
            secrets=self._resolve_secrets(template, secrets or {}),
            limits=limits,
        )

    def validate_inputs(
        self, template: Template, op: Operation, inputs: dict[str, Value]
    ) -> dict[str, Value]:
        """Apply input defaults, then validate against the operation's inputs schema (E401)."""
        resolved = dict(inputs)
        for spec in op.inputs:
            if spec.name not in resolved and spec.has_default:
                resolved[spec.name] = copy.deepcopy(spec.default)
        compiled = self._schemas.compile(op.inputs_schema, definitions=template.schemas)
        for violation in compiled.validate(resolved):
            where = str(violation.path)
            raise ValidationError(
                "E401",
                f"invalid inputs for {op.name}: {violation.message}",
                path="inputs" if where == "<root>" else f"inputs.{where}",
            )
        return resolved

    def _resolve_secrets(self, template: Template, given: Mapping[str, str]) -> dict[str, str]:
        resolved: dict[str, str] = {}
        for name, spec in template.secrets.items():
            value = given.get(name)
            if value is None and self._secrets is not None:
                value = self._secrets.get(name)
            if value is None:
                if spec.required:
                    raise ValidationError(
                        "E402",
                        f"secret {name!r} is required but was not provided",
                        path=f"secrets.{name}",
                        hint="pass secrets={...} to init/run, or configure a secrets provider",
                    )
                continue
            if not isinstance(value, str):
                raise ValidationError("E402", f"secret {name!r} must be a string", path=name)
            resolved[name] = value
        return resolved

    # ── execution ──────────────────────────────────────────────────────────────

    async def execute(
        self,
        prepared: PreparedRun,
        *,
        run_id: str,
        bus: EventBus,
        output: OutputCollector,
        on_state: Callable[[RunState], None] | None = None,
    ) -> RunResult:
        template, op = prepared.template, prepared.operation
        limits = Limits.strictest(
            self._limits, template.limits, op.limits, prepared.limits or Limits()
        )
        state = RunState(
            run=RunInfo(
                id=run_id,
                template_id=template.id,
                template_version=str(template.metadata.version),
                operation=op.name,
            ),
            bus=bus,
            limits=limits,
            env=self._env_factory(),
            clock=self._clock,
            validate_item=self._item_validator(template, op),
            output=output,
        )
        if on_state is not None:
            on_state(state)
        started = time.monotonic()
        try:
            try:
                result = await self._run(state, prepared)
            except CrowleyError as exc:
                _redact(exc, prepared.secrets)
                await state.publish(RunErrorEvent, error=exc)
                raise
            state.stats.duration_ms = int((time.monotonic() - started) * 1000)
            return RunResult(
                output=result,
                items=list(state.output.items),
                stats=state.stats.as_dict(),
                run_id=run_id,
                operation=op.name,
            )
        finally:
            output.close()

    async def _run(self, state: RunState, prepared: PreparedRun) -> Value:
        template, op = prepared.template, prepared.operation
        inputs = prepared.inputs
        start, outcome = await state.publish(RunStart, inputs=inputs)
        if outcome.changed:
            inputs = self.validate_inputs(template, op, start.inputs)
        resolve, outcome = await state.publish(InputsResolve, inputs=inputs)
        if outcome.changed:
            inputs = self.validate_inputs(template, op, resolve.inputs)

        executor = Executor(state)
        FunctionInvoker(
            state, executor, registry=self._registry, schemas=self._schemas, template=template
        )
        run_info: dict[str, Value] = {
            "id": state.run.id,
            "template_id": template.id,
            "operation": op.name,
            "started_at": state.now().isoformat(),
        }
        frame = Frame(roots={"inputs": inputs, "secrets": dict(prepared.secrets), "run": run_info})
        duration = state.limits.max_duration
        try:
            async with asyncio.timeout(duration.seconds if duration is not None else None):
                outcome_ = await executor.run_block(op.steps, frame, inputs)
        except TimeoutError:
            raise LimitError(
                "E701", f"limits.max_duration of {duration} exceeded", path=str(op.path)
            ) from None

        match op.output.mode:
            case OutputMode.EMIT:
                output: Value = list(state.output.items)
            case OutputMode.VALUE:
                assert op.output.value is not None
                output = evaluate_node(op.output.value, frame.scope(outcome_.prev), state.env)
            case _:
                output = outcome_.prev
        before, _ = await state.publish(OutputBeforeValidate, output=output)
        output = before.output
        self._validate_output(template, op, output)
        await state.publish(OutputValidated, output=output)
        end, outcome = await state.publish(RunEnd, output=output)
        if outcome.changed:
            self._validate_output(template, op, end.output)
        return end.output

    def _validate_output(self, template: Template, op: Operation, output: Value) -> None:
        assert_output_safe(output)
        compiled = self._schemas.compile(op.output.schema, definitions=template.schemas)
        for violation in compiled.validate(output):
            where = str(violation.path)
            raise ValidationError(
                "E405",
                f"invalid output of {op.name}: {violation.message}",
                path="output" if where == "<root>" else f"output{_join(where)}",
                location=None,
            )

    def _item_validator(
        self, template: Template, op: Operation
    ) -> Callable[[Value, str], None] | None:
        if op.output.mode is not OutputMode.EMIT:
            return None
        schema = op.output.schema.get("items")
        if not isinstance(schema, Mapping):
            return None
        compiled = self._schemas.compile(schema, definitions=template.schemas)

        def validate(item: Value, path: str) -> None:
            assert_output_safe(item, TemplatePath.of("output", "item"))
            for violation in compiled.validate(item):
                raise ValidationError(
                    "E404", f"invalid emitted item: {violation.message}", path=path, value=item
                )

        return validate


def _select(template: Template, operation: str | None) -> Operation:
    if operation is None:
        if len(template.operations) == 1:
            return next(iter(template.operations.values()))
        raise ConfigurationError(
            "E904",
            f"{template.id} has several operations; name one",
            hint=f"operations: {', '.join(template.operations)}",
        )
    op = template.operations.get(operation)
    if op is None:
        raise ConfigurationError(
            "E904",
            f"{template.id} has no operation {operation!r}",
            hint=f"operations: {', '.join(template.operations)}",
        )
    return op


def _join(path: str) -> str:
    return path if path.startswith("[") else f".{path}"


def _redact(error: CrowleyError, secrets: Mapping[str, str]) -> None:
    """Mask secret values in an error's message and offending value."""
    values = [v for v in secrets.values() if v]
    if not values:
        return
    for value in values:
        error.message = error.message.replace(value, REDACTED)
    error.args = (error.message,)
    error.value = _mask(error.value, values)


def _mask(value: Any, secrets: list[str]) -> Any:
    if isinstance(value, str):
        for secret in secrets:
            value = value.replace(secret, REDACTED)
        return value
    if isinstance(value, list):
        return [_mask(v, secrets) for v in value]
    if isinstance(value, dict):
        return {k: _mask(v, secrets) for k, v in value.items()}
    return value

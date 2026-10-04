"""Helpers to run compiled operations through the executor without the Process layer."""

import textwrap
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from crowley import Crowley
from crowley.application.events import EventBus, NotifierSet
from crowley.application.registry import RegisteredFunction
from crowley.application.runtime import Executor, Frame, FunctionInvoker, RunState
from crowley.domain.events import RunInfo
from crowley.domain.expressions import EvalEnv
from crowley.domain.functions import FunctionSpec
from crowley.domain.template import Limits, Template
from crowley.domain.values import Value

HEADER = """\
crowley: 1
id: acme/runtime
version: 1.0.0
name: Runtime
description: Runtime test template.
permissions:
  hosts: [api.example.com]
"""


def template(body: str, functions: Sequence[RegisteredFunction | FunctionSpec] = ()) -> Template:
    """Load ``HEADER + body`` (dedented) through the full validation pipeline."""
    text = HEADER + textwrap.dedent(body)
    return Crowley(functions=functions).load_text(text, name="runtime.yml")


def operation(
    steps: str,
    *,
    output: str = "schema: {}",
    extra: str = "",
    functions: Sequence[RegisteredFunction | FunctionSpec] = (),
) -> Template:
    """A template with one operation ``op`` made of ``steps`` (YAML list, dedented)."""
    indented_steps = textwrap.indent(textwrap.dedent(steps).strip(), "      ")
    indented_output = textwrap.indent(textwrap.dedent(output).strip(), "      ")
    return template(
        f"{textwrap.dedent(extra)}\noperations:\n  op:\n    description: d\n"
        f"    steps:\n{indented_steps}\n    output:\n{indented_output}\n",
        functions,
    )


@dataclass
class Run:
    state: RunState
    prev: Value
    frame: Frame

    @property
    def items(self) -> list[Value]:
        return self.state.output.items


async def execute(
    tpl: Template,
    *,
    inputs: dict[str, Value] | None = None,
    notifiers: NotifierSet | None = None,
    limits: Limits | None = None,
    functions: Sequence[RegisteredFunction | FunctionSpec] = (),
    **state_options: Any,
) -> Run:
    op = tpl.operation("op")
    bus = EventBus(notifiers or NotifierSet("global"))
    state = RunState(
        run=RunInfo(id="run-1", template_id=tpl.id, operation=op.name),
        bus=bus,
        limits=Limits.strictest(tpl.limits_for(op.name), limits or Limits()),
        env=EvalEnv(),
        **state_options,
    )
    inputs = inputs or {}
    frame = Frame(roots={"inputs": inputs, "secrets": {}, "run": {"id": "run-1"}})
    executor = Executor(state)
    crowley = Crowley(functions=functions)
    FunctionInvoker(
        state, executor, registry=crowley.registry, schemas=crowley.schemas, template=tpl
    )
    outcome = await executor.run_block(op.steps, frame, inputs)
    return Run(state=state, prev=outcome.prev, frame=frame)

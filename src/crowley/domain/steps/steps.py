"""Step AST (docs/SPEC.md §7): one immutable class per step kind, plus blocks and error policies."""

from dataclasses import dataclass
from enum import Enum
from typing import Literal, TypeAlias, Union

from crowley.domain.common import Duration, TemplatePath, TemplateRef
from crowley.domain.errors import SourceLocation
from crowley.domain.steps.values import Expr, ValueNode

STEP_KINDS = (
    "use",
    "if",
    "for_each",
    "while",
    "set",
    "emit",
    "return",
    "break",
    "continue",
    "fail",
    "assert",
    "log",
)
"""The kind key of every step. A step mapping has exactly one of these (SPEC §7.2)."""

ON_ERROR_FORBIDDEN = frozenset({"break", "continue", "return", "set"})
"""Step kinds that may not carry ``on_error`` (SPEC §7.1, E329)."""

LOOP_KINDS = frozenset({"for_each", "while"})


# ── error policy (SPEC §16.3) ─────────────────────────────────────────────────


@dataclass(frozen=True, slots=True, kw_only=True)
class RetrySpec:
    times: int
    backoff: Literal["fixed", "exponential"] = "exponential"
    delay: Duration = Duration(1.0)
    max_delay: Duration = Duration(30.0)


@dataclass(frozen=True, slots=True, kw_only=True)
class UseDefault:
    """Outcome ``{ default: <value> }``."""

    value: ValueNode


ErrorOutcome = Literal["fail", "skip"] | UseDefault


@dataclass(frozen=True, slots=True, kw_only=True)
class ErrorPolicy:
    """``on_error``: optionally ``catch`` only some codes, ``retry``, then an outcome."""

    outcome: ErrorOutcome = "fail"
    retry: RetrySpec | None = None
    catch: tuple[str, ...] = ()
    path: TemplatePath = TemplatePath()


# ── common fields ─────────────────────────────────────────────────────────────


class LogLevel(Enum):
    DEBUG = "debug"
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


@dataclass(frozen=True, slots=True, kw_only=True)
class StepBase:
    """Fields every step kind may have (SPEC §7.1)."""

    id: str | None = None
    name: str | None = None
    description: str | None = None
    when: Expr | None = None
    on_error: ErrorPolicy | None = None
    timeout: Duration | None = None
    path: TemplatePath = TemplatePath()
    location: SourceLocation | None = None

    kind: Literal[""] = ""  # overridden by each subclass

    @property
    def label(self) -> str:
        """Human label for traces: ``id``, ``name`` or the step kind."""
        return self.id or self.name or self.kind


@dataclass(frozen=True, slots=True, kw_only=True)
class Block:
    steps: tuple["Step", ...]
    path: TemplatePath = TemplatePath()


# ── step kinds ────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True, kw_only=True)
class UseStep(StepBase):
    """``use: ns.fn | local.fn | template:<ref>#<op>`` with ``with:`` args and optional ``do:``."""

    uses: str
    args: ValueNode | None = None
    body: Block | None = None
    secrets: ValueNode | None = None
    template_ref: TemplateRef | None = None
    kind: Literal["use"] = "use"  # type: ignore[assignment]

    @property
    def is_template_call(self) -> bool:
        return self.template_ref is not None

    @property
    def is_local_call(self) -> bool:
        return self.uses.startswith("local.")


@dataclass(frozen=True, slots=True, kw_only=True)
class IfStep(StepBase):
    condition: Expr
    then: Block
    otherwise: Block | None = None
    kind: Literal["if"] = "if"  # type: ignore[assignment]


@dataclass(frozen=True, slots=True, kw_only=True)
class ForEachStep(StepBase):
    items: ValueNode
    body: Block
    as_name: str = "item"
    index_as: str | None = None
    concurrency: ValueNode | None = None
    kind: Literal["for_each"] = "for_each"  # type: ignore[assignment]


@dataclass(frozen=True, slots=True, kw_only=True)
class WhileStep(StepBase):
    condition: Expr
    max_iterations: int
    body: Block
    kind: Literal["while"] = "while"  # type: ignore[assignment]


@dataclass(frozen=True, slots=True, kw_only=True)
class SetStep(StepBase):
    assignments: tuple[tuple[str, ValueNode], ...]
    kind: Literal["set"] = "set"  # type: ignore[assignment]


@dataclass(frozen=True, slots=True, kw_only=True)
class EmitStep(StepBase):
    value: ValueNode
    kind: Literal["emit"] = "emit"  # type: ignore[assignment]


@dataclass(frozen=True, slots=True, kw_only=True)
class ReturnStep(StepBase):
    value: ValueNode
    kind: Literal["return"] = "return"  # type: ignore[assignment]


@dataclass(frozen=True, slots=True, kw_only=True)
class BreakStep(StepBase):
    kind: Literal["break"] = "break"  # type: ignore[assignment]


@dataclass(frozen=True, slots=True, kw_only=True)
class ContinueStep(StepBase):
    kind: Literal["continue"] = "continue"  # type: ignore[assignment]


@dataclass(frozen=True, slots=True, kw_only=True)
class FailStep(StepBase):
    message: ValueNode
    code: str | None = None
    kind: Literal["fail"] = "fail"  # type: ignore[assignment]


@dataclass(frozen=True, slots=True, kw_only=True)
class AssertStep(StepBase):
    condition: Expr
    message: ValueNode | None = None
    kind: Literal["assert"] = "assert"  # type: ignore[assignment]


@dataclass(frozen=True, slots=True, kw_only=True)
class LogStep(StepBase):
    message: ValueNode
    level: LogLevel = LogLevel.INFO
    kind: Literal["log"] = "log"  # type: ignore[assignment]


Step: TypeAlias = Union[  # noqa: UP007 - forward references
    UseStep,
    IfStep,
    ForEachStep,
    WhileStep,
    SetStep,
    EmitStep,
    ReturnStep,
    BreakStep,
    ContinueStep,
    FailStep,
    AssertStep,
    LogStep,
]


def child_blocks(step: Step) -> tuple[Block, ...]:
    """The nested blocks of a step (``do``, ``then``, ``else``)."""
    match step:
        case UseStep(body=body) | ForEachStep(body=body) | WhileStep(body=body):
            return (body,) if body is not None else ()
        case IfStep(then=then, otherwise=otherwise):
            return (then,) if otherwise is None else (then, otherwise)
        case _:
            return ()


def iter_steps(block: Block) -> list[Step]:
    """All steps in ``block`` and its nested blocks, depth-first in document order."""
    out: list[Step] = []
    for step in block.steps:
        out.append(step)
        for child in child_blocks(step):
            out.extend(iter_steps(child))
    return out

"""Template, operations and template functions (docs/SPEC.md §2, §3, §11.4, §14, §19)."""

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from crowley.domain.common import SemVer, TemplatePath
from crowley.domain.errors import SourceLocation
from crowley.domain.functions import FunctionKind, FunctionSpec, Requirement
from crowley.domain.steps import Block, Expr, MapNode, ValueNode
from crowley.domain.template.limits import Limits
from crowley.domain.template.permissions import Permissions
from crowley.domain.values import Value

SPEC_VERSION = 1


@dataclass(frozen=True, slots=True, kw_only=True)
class Metadata:
    id: str
    version: SemVer
    name: str
    description: str
    tags: tuple[str, ...] = ()
    authors: tuple[str, ...] = ()
    license: str | None = None
    homepage: str | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class InputSpec:
    """One entry of ``inputs:``: a JSON Schema plus ``default``/``description``/``examples``."""

    name: str
    schema: Mapping[str, Any]

    @property
    def has_default(self) -> bool:
        return "default" in self.schema

    @property
    def default(self) -> Value:
        return self.schema.get("default")

    @property
    def description(self) -> str | None:
        return self.schema.get("description")


@dataclass(frozen=True, slots=True, kw_only=True)
class SecretSpec:
    name: str
    description: str | None = None
    required: bool = True


class OutputMode(Enum):
    EMIT = "emit"
    VALUE = "value"
    PIPE = "pipe"


@dataclass(frozen=True, slots=True, kw_only=True)
class OutputSpec:
    """``output:`` of an operation (SPEC §14). ``mode`` is decided by the compiler."""

    schema: Mapping[str, Any]
    mode: OutputMode
    value: ValueNode | None = None
    path: TemplatePath = TemplatePath()


@dataclass(frozen=True, slots=True, kw_only=True)
class TestExpectation:
    __test__ = False  # not a pytest class

    min_items: int | None = None
    max_items: int | None = None
    snapshot: str | None = None
    assertions: tuple[Expr, ...] = ()
    error: str | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class TestCase:
    """One entry of an operation's ``tests:`` (SPEC §19)."""

    __test__ = False  # not a pytest class

    name: str
    inputs: Mapping[str, Value] = field(default_factory=dict)
    secrets: Mapping[str, str] = field(default_factory=dict)
    fixtures: str | None = None
    match: tuple[str, ...] = ("method", "url")
    expect: TestExpectation = TestExpectation()
    path: TemplatePath = TemplatePath()


@dataclass(frozen=True, slots=True, kw_only=True)
class Operation:
    name: str
    description: str
    steps: Block
    output: OutputSpec
    title: str | None = None
    tags: tuple[str, ...] = ()
    inputs: tuple[InputSpec, ...] = ()
    limits: Limits = Limits()
    tests: tuple[TestCase, ...] = ()
    path: TemplatePath = TemplatePath()
    location: SourceLocation | None = None

    @property
    def display_name(self) -> str:
        return self.title or self.name

    def input(self, name: str) -> InputSpec | None:
        return next((i for i in self.inputs if i.name == name), None)

    @property
    def inputs_schema(self) -> Mapping[str, Any]:
        """The object schema callers' inputs are validated against (SPEC §3.1)."""
        return {
            "type": "object",
            "properties": {i.name: dict(i.schema) for i in self.inputs},
            "required": [i.name for i in self.inputs if not i.has_default],
            "additionalProperties": False,
        }


@dataclass(frozen=True, slots=True, kw_only=True)
class TemplateFunction:
    """A named step group under ``functions:``, called as ``use: local.<name>`` (SPEC §11.4)."""

    name: str
    description: str
    steps: Block
    output: Mapping[str, Any]
    input: Mapping[str, Any] = field(default_factory=lambda: {"type": "object"})
    prev: Mapping[str, Any] | None = None
    path: TemplatePath = TemplatePath()
    location: SourceLocation | None = None

    @property
    def qualified_name(self) -> str:
        return f"local.{self.name}"

    def as_function_spec(self, version: SemVer) -> FunctionSpec:
        return FunctionSpec(
            name=self.qualified_name,
            version=version,
            kind=FunctionKind.PLAIN,
            input=self.input,
            output=self.output,
            prev=self.prev,
            description=self.description,
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class Template:
    metadata: Metadata
    operations: Mapping[str, Operation]
    permissions: Permissions = Permissions()
    spec_version: int = SPEC_VERSION
    requires: tuple[Requirement, ...] = ()
    secrets: Mapping[str, SecretSpec] = field(default_factory=dict)
    defaults: Mapping[str, MapNode] = field(default_factory=dict)
    limits: Limits = Limits()
    schemas: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)
    functions: Mapping[str, TemplateFunction] = field(default_factory=dict)
    extensions: Mapping[str, Value] = field(default_factory=dict)
    """``x-*`` keys, kept verbatim for tools and the hub."""
    source_file: str | None = None
    content_hash: str = ""

    @property
    def id(self) -> str:
        return self.metadata.id

    def operation(self, name: str | None = None) -> Operation:
        """The named operation, or the only one when ``name`` is omitted. ``KeyError`` otherwise."""
        if name is None:
            if len(self.operations) != 1:
                raise KeyError("this template has several operations; name one")
            return next(iter(self.operations.values()))
        return self.operations[name]

    def limits_for(self, operation: str) -> Limits:
        return Limits.strictest(self.limits, self.operations[operation].limits)

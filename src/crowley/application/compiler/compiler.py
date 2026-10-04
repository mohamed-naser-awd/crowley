"""Compile a parsed template into the domain model: stages 1 and 2 of docs/SPEC.md §15.

The compiler is registry-free. Everything that needs to know which functions, adapters or
extractors exist is checked later by the static validator.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from crowley.application.ports import RawDocument, SchemaValidator, SchemaViolation
from crowley.domain.common import (
    ByteSize,
    Duration,
    Rate,
    SemVer,
    TemplatePath,
    TemplateRef,
    is_function_name,
    parse_template_ref,
)
from crowley.domain.errors import CODES, CrowleyError, Diagnostic
from crowley.domain.expressions import Compiled, Text, parse_expression, parse_scalar
from crowley.domain.functions import Requirement
from crowley.domain.steps import (
    ON_ERROR_FORBIDDEN,
    STEP_KINDS,
    AssertStep,
    Block,
    BreakStep,
    ContinueStep,
    EmitStep,
    ErrorOutcome,
    ErrorPolicy,
    Expr,
    FailStep,
    ForEachStep,
    IfStep,
    ListNode,
    Lit,
    LogLevel,
    LogStep,
    MapNode,
    RetrySpec,
    ReturnStep,
    SetStep,
    Step,
    UseDefault,
    UseStep,
    ValueNode,
    WhileStep,
    iter_steps,
)
from crowley.domain.template import (
    SPEC_VERSION,
    HostPattern,
    InputSpec,
    Limits,
    Metadata,
    Operation,
    OutputMode,
    OutputSpec,
    Permissions,
    SecretSpec,
    Template,
    TemplateFunction,
    TestCase,
    TestExpectation,
)
from crowley.domain.template.meta_schema import META_SCHEMA
from crowley.domain.values import Value

COMMON_STEP_FIELDS = frozenset({"id", "name", "description", "when", "on_error", "timeout"})
KIND_FIELDS: Mapping[str, frozenset[str]] = {
    "use": frozenset({"with", "do", "secrets"}),
    "if": frozenset({"then", "else"}),
    "for_each": frozenset({"as", "index_as", "concurrency", "do"}),
    "while": frozenset({"max_iterations", "do"}),
    "set": frozenset(),
    "emit": frozenset(),
    "return": frozenset(),
    "break": frozenset(),
    "continue": frozenset(),
    "fail": frozenset({"code"}),
    "assert": frozenset({"message"}),
    "log": frozenset({"level"}),
}
_SCHEMA_KEYWORD_CODES = {"additionalProperties": "E201", "required": "E202"}
SHADOWING_FORBIDDEN = frozenset(
    {"prev", "inputs", "secrets", "steps", "vars", "args", "run", "loop", "error"}
)
"""Roots a loop variable may not shadow (``item``, ``page`` and ``result`` are binding names)."""


@dataclass(frozen=True, slots=True, kw_only=True)
class CompileResult:
    template: Template | None
    diagnostics: tuple[Diagnostic, ...]


class Compiler:
    def __init__(self, schema_validator: SchemaValidator) -> None:
        self._meta_schema = schema_validator.compile(META_SCHEMA)

    def compile(self, document: RawDocument, *, content_hash: str = "") -> CompileResult:
        builder = _Builder(document)
        data = document.data
        if not isinstance(data, dict):
            builder.diag("E205", "a template must be a YAML mapping", TemplatePath())
            return builder.result(None)
        if not builder.check_version(data):
            return builder.result(None)
        violations = self._meta_schema.validate(data)
        for violation in violations:
            builder.schema_violation(violation)
        if isinstance(data.get("operations"), dict) and not data["operations"]:
            builder.diag(
                "E204",
                "a template needs at least one operation",
                TemplatePath.of("operations"),
                hint="add an entry under 'operations:', e.g. 'get_items:'",
            )
        if builder.diagnostics:
            return builder.result(None)
        return builder.result(builder.template(data, content_hash))


class _Builder:
    def __init__(self, document: RawDocument) -> None:
        self.document = document
        self.positions = document.positions
        self.diagnostics: list[Diagnostic] = []

    # ── diagnostics ───────────────────────────────────────────────────────────
    def diag(
        self,
        code: str,
        message: str,
        path: TemplatePath,
        *,
        hint: str | None = None,
        key: bool = False,
    ) -> None:
        location = self.positions.key_at(path) if key else self.positions.value_at(path)
        self.diagnostics.append(
            Diagnostic(code=code, message=message, path=str(path), location=location, hint=hint)
        )

    def error(self, error: CrowleyError, path: TemplatePath) -> None:
        self.diag(error.code, error.message, path, hint=error.hint)

    def schema_violation(self, violation: SchemaViolation) -> None:
        code = _SCHEMA_KEYWORD_CODES.get(violation.keyword, "E205")
        self.diag(code, violation.message, violation.path, key=code == "E201")

    def result(self, template: Template | None) -> CompileResult:
        return CompileResult(template=template, diagnostics=tuple(self.diagnostics))

    def check_version(self, data: Mapping[str, Value]) -> bool:
        path = TemplatePath.of("crowley")
        if "crowley" not in data:
            self.diag(
                "E202",
                "missing required key 'crowley' (the spec version)",
                TemplatePath(),
                hint=f"add 'crowley: {SPEC_VERSION}' at the top of the template",
            )
            return False
        version = data["crowley"]
        if isinstance(version, bool) or version != SPEC_VERSION:
            self.diag(
                "E103",
                f"unsupported spec version {version!r}; this SDK supports crowley: {SPEC_VERSION}",
                path,
            )
            return False
        return True

    # ── template ──────────────────────────────────────────────────────────────
    def template(self, data: dict[str, Any], content_hash: str) -> Template:
        metadata = Metadata(
            id=data["id"],
            version=SemVer.parse(data["version"]),
            name=data["name"],
            description=data["description"],
            tags=tuple(data.get("tags", ())),
            authors=tuple(data.get("authors", ())),
            license=data.get("license"),
            homepage=data.get("homepage"),
        )
        return Template(
            metadata=metadata,
            spec_version=SPEC_VERSION,
            requires=self.requires(data.get("requires", [])),
            secrets={
                name: SecretSpec(
                    name=name,
                    description=raw.get("description"),
                    required=raw.get("required", True),
                )
                for name, raw in data.get("secrets", {}).items()
            },
            permissions=self.permissions(data["permissions"]),
            defaults={
                ns: self.mapping(raw, TemplatePath.of("defaults", ns))
                for ns, raw in data.get("defaults", {}).items()
            },
            limits=self.limits(data.get("limits", {}), TemplatePath.of("limits")),
            schemas=dict(data.get("schemas", {})),
            functions={
                name: self.function(name, raw, TemplatePath.of("functions", name))
                for name, raw in data.get("functions", {}).items()
            },
            operations={
                name: self.operation(name, raw, TemplatePath.of("operations", name))
                for name, raw in data["operations"].items()
            },
            extensions={k: v for k, v in data.items() if k.startswith("x-")},
            source_file=self.document.origin.file,
            content_hash=content_hash,
        )

    def requires(self, raw: list[str]) -> tuple[Requirement, ...]:
        out: list[Requirement] = []
        for i, text in enumerate(raw):
            try:
                out.append(Requirement.parse(text))
            except ValueError as exc:
                self.diag("E205", str(exc), TemplatePath.of("requires", i))
        return tuple(out)

    def permissions(self, raw: dict[str, Any]) -> Permissions:
        hosts: list[HostPattern] = []
        for i, text in enumerate(raw.get("hosts", [])):
            try:
                hosts.append(HostPattern.parse(text))
            except ValueError as exc:
                self.diag("E205", str(exc), TemplatePath.of("permissions", "hosts", i))
        return Permissions(hosts=tuple(hosts))

    def limits(self, raw: dict[str, Any], path: TemplatePath) -> Limits:
        def parse(key: str, parser: Any) -> Any:
            if key not in raw:
                return None
            try:
                return parser(raw[key])
            except ValueError as exc:  # pragma: no cover - the meta-schema patterns prevent this
                self.diag("E205", str(exc), path.key(key))
                return None

        rate = raw.get("rate", {})
        return Limits(
            max_requests=raw.get("max_requests"),
            max_duration=parse("max_duration", Duration.parse),
            max_items=raw.get("max_items"),
            max_depth=raw.get("max_depth"),
            max_loop_iterations=raw.get("max_loop_iterations"),
            max_concurrency=raw.get("max_concurrency"),
            max_response_bytes=parse("max_response_bytes", ByteSize.parse),
            max_retries_per_step=raw.get("max_retries_per_step"),
            rate_per_host=Rate.parse(rate["per_host"]) if "per_host" in rate else None,
        )

    def function(self, name: str, raw: dict[str, Any], path: TemplatePath) -> TemplateFunction:
        return TemplateFunction(
            name=name,
            description=raw["description"],
            steps=self.block(raw["steps"], path.key("steps")),
            output=_schema(raw["output"]),
            input=raw.get("input", {"type": "object"}),
            prev=_schema(raw["prev"]) if "prev" in raw else None,
            path=path,
            location=self.positions.key_at(path),
        )

    def operation(self, name: str, raw: dict[str, Any], path: TemplatePath) -> Operation:
        steps = self.block(raw["steps"], path.key("steps"))
        return Operation(
            name=name,
            description=raw["description"],
            steps=steps,
            output=self.output(raw["output"], path.key("output"), steps),
            title=raw.get("name"),
            tags=tuple(raw.get("tags", ())),
            inputs=tuple(
                InputSpec(name=input_name, schema=_schema(schema))
                for input_name, schema in raw.get("inputs", {}).items()
            ),
            limits=self.limits(raw.get("limits", {}), path.key("limits")),
            tests=tuple(
                self.test(t, path.key("tests").index(i)) for i, t in enumerate(raw.get("tests", []))
            ),
            path=path,
            location=self.positions.key_at(path),
        )

    def output(self, raw: dict[str, Any], path: TemplatePath, steps: Block) -> OutputSpec:
        value = self.value(raw["value"], path.key("value")) if "value" in raw else None
        has_emit = any(isinstance(s, EmitStep) for s in iter_steps(steps))
        mode = (
            OutputMode.EMIT
            if has_emit
            else OutputMode.VALUE
            if value is not None
            else OutputMode.PIPE
        )
        return OutputSpec(schema=_schema(raw["schema"]), mode=mode, value=value, path=path)

    def test(self, raw: dict[str, Any], path: TemplatePath) -> TestCase:
        expect = raw.get("expect", {})
        assert_path = path.key("expect").key("assert")
        return TestCase(
            name=raw["name"],
            inputs=raw.get("inputs", {}),
            secrets=raw.get("secrets", {}),
            fixtures=raw.get("fixtures"),
            match=tuple(raw.get("match", ("method", "url"))),
            expect=TestExpectation(
                min_items=expect.get("min_items"),
                max_items=expect.get("max_items"),
                snapshot=expect.get("snapshot"),
                assertions=tuple(
                    self.condition(text, assert_path.index(i))
                    for i, text in enumerate(expect.get("assert", []))
                ),
                error=expect.get("error"),
            ),
            path=path,
        )

    # ── steps ─────────────────────────────────────────────────────────────────
    def block(self, raw: list[Any], path: TemplatePath) -> Block:
        steps = (self.step(item, path.index(i)) for i, item in enumerate(raw))
        return Block(steps=tuple(s for s in steps if s is not None), path=path)

    def step(self, raw: dict[str, Any], path: TemplatePath) -> Step | None:
        kinds = [k for k in STEP_KINDS if k in raw]
        if not kinds:
            self.diag(
                "E202",
                f"a step needs exactly one kind key: {', '.join(STEP_KINDS)}",
                path,
                hint="e.g. '- use: http.get' or '- if: ${{ ... }}'",
            )
            return None
        if len(kinds) > 1:
            self.diag(
                "E205",
                f"a step has exactly one kind; found {' and '.join(kinds)}",
                path,
                hint="split it into separate steps",
            )
            return None
        kind = kinds[0]
        allowed = COMMON_STEP_FIELDS | {kind} | KIND_FIELDS[kind]
        for key in raw:
            if key not in allowed:
                where = [k for k, fields in KIND_FIELDS.items() if key in fields]
                hint = f"{key!r} belongs to {', '.join(where)} steps" if where else None
                self.diag(
                    "E201",
                    f"{key!r} is not allowed on a {kind!r} step",
                    path.key(key),
                    key=True,
                    hint=hint,
                )
        if "on_error" in raw and kind in ON_ERROR_FORBIDDEN:
            self.diag(
                "E329",
                f"on_error is not allowed on a {kind!r} step",
                path.key("on_error"),
                key=True,
            )

        common: dict[str, Any] = {
            "id": raw.get("id"),
            "name": raw.get("name"),
            "description": raw.get("description"),
            "when": self.condition(raw["when"], path.key("when")) if "when" in raw else None,
            "on_error": self.on_error(raw["on_error"], path.key("on_error"))
            if "on_error" in raw
            else None,
            "timeout": Duration.parse(raw["timeout"]) if "timeout" in raw else None,
            "path": path,
            "location": self.positions.value_at(path),
        }

        def required_block(key: str) -> Block:
            if key not in raw:
                self.diag("E202", f"a {kind!r} step needs '{key}:'", path)
                return Block(steps=(), path=path.key(key))
            return self.block(raw[key], path.key(key))

        match kind:
            case "use":
                return self.use_step(raw, path, common)
            case "if":
                return IfStep(
                    condition=self.condition(raw["if"], path.key("if")),
                    then=required_block("then"),
                    otherwise=self.block(raw["else"], path.key("else")) if "else" in raw else None,
                    **common,
                )
            case "for_each":
                return ForEachStep(
                    items=self.loop_items(raw["for_each"], path.key("for_each")),
                    body=required_block("do"),
                    as_name=self.binding_name(raw.get("as", "item"), path.key("as")),
                    index_as=self.binding_name(raw["index_as"], path.key("index_as"))
                    if "index_as" in raw
                    else None,
                    concurrency=self.concurrency(raw["concurrency"], path.key("concurrency"))
                    if "concurrency" in raw
                    else None,
                    **common,
                )
            case "while":
                if "max_iterations" not in raw:
                    self.diag(
                        "E202",
                        "a 'while' step needs 'max_iterations:'",
                        path,
                        hint="a cap keeps the loop from running forever",
                    )
                return WhileStep(
                    condition=self.condition(raw["while"], path.key("while")),
                    max_iterations=raw.get("max_iterations", 1),
                    body=required_block("do"),
                    **common,
                )
            case "set":
                return SetStep(
                    assignments=tuple(
                        (k, self.value(v, path.key("set").key(k))) for k, v in raw["set"].items()
                    ),
                    **common,
                )
            case "emit":
                return EmitStep(value=self.value(raw["emit"], path.key("emit")), **common)
            case "return":
                return ReturnStep(value=self.value(raw["return"], path.key("return")), **common)
            case "break":
                return BreakStep(**common)
            case "continue":
                return ContinueStep(**common)
            case "fail":
                return FailStep(
                    message=self.value(raw["fail"], path.key("fail")),
                    code=raw.get("code"),
                    **common,
                )
            case "assert":
                return AssertStep(
                    condition=self.condition(raw["assert"], path.key("assert")),
                    message=self.value(raw["message"], path.key("message"))
                    if "message" in raw
                    else None,
                    **common,
                )
            case _:  # "log"
                return LogStep(
                    message=self.value(raw["log"], path.key("log")),
                    level=LogLevel(raw.get("level", "info")),
                    **common,
                )

    def use_step(self, raw: dict[str, Any], path: TemplatePath, common: dict[str, Any]) -> UseStep:
        uses: str = raw["use"]
        ref: TemplateRef | None = None
        if uses.startswith("template:"):
            try:
                ref = parse_template_ref(uses.removeprefix("template:"))
            except ValueError as exc:
                self.diag("E324", str(exc), path.key("use"))
        elif not is_function_name(uses):
            self.diag(
                "E205",
                f"invalid function name {uses!r}",
                path.key("use"),
                hint="use '<namespace>.<name>' (e.g. http.get), 'local.<name>' "
                "or 'template:<ref>#<operation>'",
            )
        return UseStep(
            uses=uses,
            args=self.mapping(raw["with"], path.key("with")) if "with" in raw else None,
            body=self.block(raw["do"], path.key("do")) if "do" in raw else None,
            secrets=self.mapping(raw["secrets"], path.key("secrets")) if "secrets" in raw else None,
            template_ref=ref,
            **common,
        )

    def on_error(self, raw: Any, path: TemplatePath) -> ErrorPolicy:
        if isinstance(raw, str):
            return ErrorPolicy(outcome=raw, path=path)  # type: ignore[arg-type]
        for i, code in enumerate(raw.get("catch", [])):
            entry = CODES.get(code)
            if entry is None or not entry.catchable:
                self.diag(
                    "E205",
                    f"{code} can never be caught by on_error",
                    path.key("catch").index(i),
                    hint="only E5xx and E6xx (except E604, E605, E607) are catchable",
                )
        if "default" in raw and "then" in raw:
            self.diag("E205", "use either 'default:' or 'then:', not both", path)
        outcome: ErrorOutcome = "fail"
        if "default" in raw:
            outcome = UseDefault(value=self.value(raw["default"], path.key("default")))
        elif "then" in raw:
            then = raw["then"]
            if then in ("fail", "skip"):
                outcome = then
            else:
                outcome = UseDefault(
                    value=self.value(then["default"], path.key("then").key("default"))
                )
        retry = None
        if "retry" in raw:
            r = raw["retry"]
            retry = RetrySpec(
                times=r["times"],
                backoff=r.get("backoff", "exponential"),
                delay=Duration.parse(r.get("delay", 1)),
                max_delay=Duration.parse(r.get("max_delay", 30)),
            )
        return ErrorPolicy(
            outcome=outcome, retry=retry, catch=tuple(raw.get("catch", ())), path=path
        )

    # ── values ────────────────────────────────────────────────────────────────
    def value(self, raw: Any, path: TemplatePath) -> ValueNode:
        location = self.positions.value_at(path)
        if isinstance(raw, dict):
            return self.mapping(raw, path)
        if isinstance(raw, list):
            return ListNode(
                items=tuple(self.value(v, path.index(i)) for i, v in enumerate(raw)),
                path=path,
                location=location,
            )
        if isinstance(raw, str):
            try:
                scalar = parse_scalar(raw)
            except CrowleyError as exc:
                self.error(exc, path)
                return Lit(value=raw, path=path, location=location)
            if isinstance(scalar, Text):
                return Lit(value=scalar.value, path=path, location=location)
            return Expr(scalar=scalar, path=path, location=location)
        return Lit(value=raw, path=path, location=location)

    def mapping(self, raw: dict[str, Any], path: TemplatePath) -> MapNode:
        return MapNode(
            entries=tuple((k, self.value(v, path.key(k))) for k, v in raw.items()),
            path=path,
            location=self.positions.value_at(path),
        )

    def condition(self, raw: Any, path: TemplatePath) -> Expr:
        """``if``/``while``/``when``/``assert``: a single ``${{ }}`` expression or true/false."""
        location = self.positions.value_at(path)
        if isinstance(raw, bool):
            return _constant(raw, path, location)
        node = self.value(raw, path)
        if isinstance(node, Expr) and isinstance(node.scalar, Compiled):
            return node
        if isinstance(raw, str) and not self._has_error_at(path):
            self.diag(
                "E205",
                "a condition must be a single ${{ ... }} expression or true/false",
                path,
                hint=f"write '${{{{ {raw} }}}}'"
                if "${{" not in raw
                else "remove the surrounding text",
            )
        return _constant(False, path, location)

    def loop_items(self, raw: Any, path: TemplatePath) -> ValueNode:
        node = self.value(raw, path)
        if isinstance(node, Lit) and not self._has_error_at(path):
            self.diag("E205", "for_each needs a list or a ${{ ... }} expression", path)
        return node

    def concurrency(self, raw: Any, path: TemplatePath) -> ValueNode:
        node = self.value(raw, path)
        if isinstance(node, Lit) and not isinstance(node.value, int):
            self.diag(
                "E205", "concurrency must be a positive integer or a ${{ ... }} expression", path
            )
        return node

    def binding_name(self, name: str, path: TemplatePath) -> str:
        if name in SHADOWING_FORBIDDEN:
            self.diag(
                "E205",
                f"{name!r} is a reserved name and cannot be used as a loop variable",
                path,
                hint="pick another name, e.g. 'row' or 'entry'",
            )
        return name

    def _has_error_at(self, path: TemplatePath) -> bool:
        text = str(path)
        return any(d.path == text for d in self.diagnostics)


def _constant(value: bool, path: TemplatePath, location: Any) -> Expr:
    expression = parse_expression("true" if value else "false")
    return Expr(scalar=Compiled(expression=expression), path=path, location=location)


def _schema(raw: Any) -> Mapping[str, Any]:
    """Boolean schemas as objects: ``true`` → ``{}``, ``false`` → ``{"not": {}}``."""
    if raw is True:
        return {}
    if raw is False:
        return {"not": {}}
    return dict(raw)

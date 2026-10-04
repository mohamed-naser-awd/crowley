"""Static checks: stage 3 of docs/SPEC.md §15 (E3xx and warnings). No I/O."""

import difflib
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, TypeGuard
from urllib.parse import urlsplit

from crowley.application.ports import PositionMap, SchemaValidator
from crowley.application.registry import Registry
from crowley.domain.adapters import TargetKind
from crowley.domain.common import TemplatePath
from crowley.domain.errors import ConfigurationError, Diagnostic, SourceLocation
from crowley.domain.expressions import Expression, analyze
from crowley.domain.extractors import FieldSpecError, iter_fields, parse_fields
from crowley.domain.functions import FunctionKind, FunctionSpec
from crowley.domain.steps import (
    AssertStep,
    Block,
    BreakStep,
    ContinueStep,
    EmitStep,
    ErrorPolicy,
    Expr,
    FailStep,
    ForEachStep,
    IfStep,
    ListNode,
    Lit,
    LogStep,
    MapNode,
    ReturnStep,
    SetStep,
    Step,
    UseDefault,
    UseStep,
    ValueNode,
    WhileStep,
    is_literal,
    iter_steps,
    literal_value,
    partial_literal,
    walk_expressions,
)
from crowley.domain.template import Operation, OutputMode, Template, TemplateFunction

OPERATION_ROOTS = frozenset({"prev", "inputs", "secrets", "steps", "vars", "run"})
FUNCTION_ROOTS = frozenset({"prev", "args", "secrets", "steps", "vars", "run"})
DEFAULTS_ROOTS = frozenset({"secrets", "run"})
TEST_ROOTS = frozenset({"output"})
_DEFAULT_PORTS = {"http": 80, "https": 443}


@dataclass(frozen=True, slots=True, kw_only=True)
class _Ctx:
    """Where an expression or step sits: who owns it and what is in scope."""

    owner: Operation | TemplateFunction
    roots: frozenset[str]
    visible_steps: frozenset[str] = frozenset()
    in_loop: bool = False
    is_owner: bool = False
    concurrent_depth: int | None = None
    """Index in the var-scope stack where the innermost concurrent loop body starts."""

    @property
    def is_function(self) -> bool:
        return isinstance(self.owner, TemplateFunction)


@dataclass(slots=True)
class _OwnerInfo:
    step_ids: dict[str, list[TemplatePath]] = field(default_factory=dict)
    assigned_vars: set[str] = field(default_factory=set)


class StaticValidator:
    def __init__(self, registry: Registry, schemas: SchemaValidator) -> None:
        self.registry = registry
        self.schemas = schemas

    def validate(
        self, template: Template, positions: PositionMap | None = None
    ) -> tuple[Diagnostic, ...]:
        run = _Run(self, template, positions)
        run.check()
        return tuple(run.diagnostics)


class _Run:
    def __init__(
        self, validator: StaticValidator, template: Template, positions: PositionMap | None
    ) -> None:
        self.registry = validator.registry
        self.schemas = validator.schemas
        self.template = template
        self.positions = positions
        self.diagnostics: list[Diagnostic] = []
        self._seen: set[tuple[str, str | None, str]] = set()
        self.var_scopes: list[set[str]] = []
        self.info = _OwnerInfo()

    # ── diagnostics ───────────────────────────────────────────────────────────
    def diag(
        self,
        code: str,
        message: str,
        path: TemplatePath,
        location: SourceLocation | None = None,
        hint: str | None = None,
    ) -> None:
        key = (code, str(path), message)
        if key in self._seen:
            return
        self._seen.add(key)
        if location is None and self.positions is not None:
            location = self.positions.value_at(path)
        self.diagnostics.append(
            Diagnostic(code=code, message=message, path=str(path), location=location, hint=hint)
        )

    # ── entry point ───────────────────────────────────────────────────────────
    def check(self) -> None:
        t = self.template
        self.check_requires()
        self.check_schemas()
        self.check_defaults()
        for function in t.functions.values():
            self.check_owner(function, function.steps, FUNCTION_ROOTS)
        for operation in t.operations.values():
            self.check_owner(operation, operation.steps, OPERATION_ROOTS)
            self.check_output(operation)
            self.check_tests(operation)
        self.check_function_graph()

    # ── template level ────────────────────────────────────────────────────────
    def check_requires(self) -> None:
        for i, requirement in enumerate(self.template.requires):
            problem = self.registry.requirement_problem(requirement)
            if problem:
                self.diag(
                    "E302",
                    f"requirement {requirement} not satisfied: {problem}",
                    TemplatePath.of("requires", i),
                )

    def check_schemas(self) -> None:
        t = self.template
        for name, schema in t.schemas.items():
            self.check_schema(schema, TemplatePath.of("schemas", name))
        for fname, function in t.functions.items():
            base = TemplatePath.of("functions", fname)
            parts: tuple[tuple[str, Mapping[str, Any] | None], ...] = (
                ("input", function.input),
                ("output", function.output),
                ("prev", function.prev),
            )
            for key, part in parts:
                if part is not None:
                    self.check_schema(part, base.key(key))
            try:
                function.as_function_spec(t.metadata.version)
            except ConfigurationError as exc:
                self.diag("E319", exc.message, base.key("input"), function.location, exc.hint)
        for oname, operation in t.operations.items():
            base = TemplatePath.of("operations", oname)
            for spec in operation.inputs:
                self.check_schema(spec.schema, base.key("inputs").key(spec.name))
            self.check_schema(operation.output.schema, base.key("output").key("schema"))

    def check_schema(self, schema: Mapping[str, Any] | bool, path: TemplatePath) -> None:
        for violation in self.schemas.check_schema(schema, definitions=self.template.schemas):
            where = TemplatePath((*path.parts, *violation.path.parts))
            self.diag("E319", f"invalid JSON Schema: {violation.message}", where)

    def check_defaults(self) -> None:
        for namespace, node in self.template.defaults.items():
            path = TemplatePath.of("defaults", namespace)
            adapter = self.registry.adapter(namespace)
            if adapter is None:
                self.diag(
                    "E203",
                    f"defaults for unknown namespace {namespace!r}",
                    path,
                    hint=_suggest(namespace, self.registry.adapters)
                    or "defaults apply to adapters, e.g. 'http'",
                )
            else:
                self.check_literal_schema(
                    node, adapter.defaults_schema, "E205", f"defaults.{namespace}"
                )
            for expression, holder in walk_expressions(node):
                self.check_roots(expression, holder, DEFAULTS_ROOTS, None, "defaults")

    def check_function_graph(self) -> None:
        calls: dict[str, set[str]] = {}
        for name, function in self.template.functions.items():
            calls[name] = {
                s.uses.removeprefix("local.") for s in iter_steps(function.steps) if _is_local(s)
            }
        used: set[str] = set()
        frontier = [
            s.uses.removeprefix("local.")
            for operation in self.template.operations.values()
            for s in iter_steps(operation.steps)
            if _is_local(s)
        ]
        while frontier:
            name = frontier.pop()
            if name in used:
                continue
            used.add(name)
            frontier.extend(calls.get(name, ()))
        for name, function in self.template.functions.items():
            if name not in used:
                self.diag(
                    "W003",
                    f"template function {name!r} is not used by any operation",
                    TemplatePath.of("functions", name),
                    function.location,
                )
            cycle = _find_cycle(name, calls)
            if cycle:
                self.diag(
                    "E317",
                    f"template function {name!r} calls itself ({' → '.join(cycle)})",
                    TemplatePath.of("functions", name),
                    function.location,
                    hint="template functions cannot be recursive",
                )

    # ── operations & functions ────────────────────────────────────────────────
    def check_owner(
        self, owner: Operation | TemplateFunction, steps: Block, roots: frozenset[str]
    ) -> None:
        self.info = _OwnerInfo()
        for step in iter_steps(steps):
            if step.id is not None:
                self.info.step_ids.setdefault(step.id, []).append(step.path)
            if isinstance(step, SetStep):
                self.info.assigned_vars.update(name for name, _ in step.assignments)
        for step_id, paths in self.info.step_ids.items():
            for duplicate in paths[1:]:
                self.diag(
                    "E307",
                    f"duplicate step id {step_id!r} (first used at {paths[0]})",
                    duplicate.key("id"),
                )
        self.var_scopes = []
        ctx = _Ctx(owner=owner, roots=roots, is_owner=isinstance(owner, TemplateFunction))
        self.walk_block(steps, ctx)

    def walk_block(self, block: Block, ctx: _Ctx) -> None:
        self.var_scopes.append(set())
        seen: set[str] = set()
        terminated = False
        for index, step in enumerate(block.steps):
            if terminated:
                self.diag(
                    "W001",
                    "unreachable step",
                    step.path,
                    step.location,
                    hint="remove it or move it above",
                )
                terminated = False  # report once per block
            step_ctx = replace(ctx, visible_steps=ctx.visible_steps | seen)
            self.check_step(step, step_ctx)
            self.check_unused_result(block.steps, index, ctx)
            if step.id is not None:
                seen.add(step.id)
            if (
                isinstance(step, ReturnStep | BreakStep | ContinueStep | FailStep)
                and step.when is None
            ):
                terminated = index < len(block.steps) - 1
        self.var_scopes.pop()

    def check_step(self, step: Step, ctx: _Ctx) -> None:
        if step.when is not None:
            self.check_value(step.when, ctx)
        if step.on_error is not None:
            self.check_policy(step.on_error, ctx)
        match step:
            case UseStep():
                self.check_use(step, ctx)
            case IfStep():
                self.check_value(step.condition, ctx)
                self.walk_block(step.then, ctx)
                if step.otherwise is not None:
                    self.walk_block(step.otherwise, ctx)
            case ForEachStep():
                self.check_for_each(step, ctx)
            case WhileStep():
                self.check_value(step.condition, ctx)
                body_ctx = replace(ctx, roots=ctx.roots | {"loop"}, in_loop=True, is_owner=True)
                self.walk_block(step.body, body_ctx)
            case SetStep():
                for name, node in step.assignments:
                    self.check_value(node, ctx)
                    self.assign(name, step.path.key("set").key(name), ctx)
            case EmitStep():
                if ctx.is_function:
                    self.diag(
                        "E323",
                        "emit is not allowed inside a template function",
                        step.path,
                        step.location,
                        hint="return the value and emit it from the operation",
                    )
                self.check_value(step.value, ctx)
            case ReturnStep():
                if not ctx.is_owner:
                    self.diag(
                        "E309",
                        "return outside a loop, block body or template function",
                        step.path,
                        step.location,
                        hint="use output.value to shape an operation's output",
                    )
                self.check_value(step.value, ctx)
            case BreakStep() | ContinueStep():
                if not ctx.in_loop:
                    self.diag(
                        "E308",
                        f"{step.kind} outside a loop or block-function body",
                        step.path,
                        step.location,
                    )
            case FailStep() | LogStep():
                self.check_value(step.message, ctx)
            case AssertStep():
                self.check_value(step.condition, ctx)
                if step.message is not None:
                    self.check_value(step.message, ctx)

    def check_for_each(self, step: ForEachStep, ctx: _Ctx) -> None:
        self.check_value(step.items, ctx)
        concurrent = False
        if step.concurrency is not None:
            self.check_value(step.concurrency, ctx)
            concurrent = not (isinstance(step.concurrency, Lit) and step.concurrency.value == 1)
        bindings = {step.as_name, "loop"} | ({step.index_as} if step.index_as else set())
        body_ctx = replace(
            ctx,
            roots=ctx.roots | bindings,
            in_loop=True,
            is_owner=True,
            concurrent_depth=len(self.var_scopes) if concurrent else ctx.concurrent_depth,
        )
        self.walk_block(step.body, body_ctx)

    def assign(self, name: str, path: TemplatePath, ctx: _Ctx) -> None:
        """``set`` writes to the nearest scope that has ``name``, else declares it (SPEC §8.2)."""
        for depth in range(len(self.var_scopes) - 1, -1, -1):
            if name in self.var_scopes[depth]:
                if ctx.concurrent_depth is not None and depth < ctx.concurrent_depth:
                    self.diag(
                        "E320",
                        f"variable {name!r} from outside is assigned inside a concurrent for_each",
                        path,
                        hint="emit or return values instead, or set concurrency: 1",
                    )
                return
        self.var_scopes[-1].add(name)

    def check_policy(self, policy: ErrorPolicy, ctx: _Ctx) -> None:
        if isinstance(policy.outcome, UseDefault):
            self.check_value(policy.outcome.value, replace(ctx, roots=ctx.roots | {"error"}))

    # ── use steps ─────────────────────────────────────────────────────────────
    def check_use(self, step: UseStep, ctx: _Ctx) -> None:
        if step.template_ref is not None:
            self.check_template_call(step, ctx)
            return
        if step.secrets is not None:
            self.diag(
                "E201",
                "'secrets:' is only allowed on template: calls",
                step.secrets.path,
                step.secrets.location,
            )
        spec = self.function_spec(step)
        if spec is None:
            if step.args is not None:
                self.check_value(step.args, ctx)
            if step.body is not None:
                self.walk_block(step.body, replace(ctx, in_loop=True, is_owner=True))
            return
        if spec.kind is FunctionKind.BLOCK and step.body is None:
            self.diag(
                "E304", f"{spec.name} is a block function and needs 'do:'", step.path, step.location
            )
        if spec.kind is FunctionKind.PLAIN and step.body is not None:
            self.diag(
                "E304", f"{spec.name} takes no 'do:' block", step.path.key("do"), step.location
            )
        self.check_args(step, spec, ctx)
        if step.body is not None:
            body_ctx = replace(
                ctx, roots=ctx.roots | set(spec.body_bindings), in_loop=True, is_owner=True
            )
            self.walk_block(step.body, body_ctx)

    def function_spec(self, step: UseStep) -> FunctionSpec | None:
        if step.is_local_call:
            name = step.uses.removeprefix("local.")
            function = self.template.functions.get(name)
            if function is None:
                self.diag(
                    "E301",
                    f"unknown template function {name!r}",
                    step.path.key("use"),
                    step.location,
                    hint=_suggest(name, self.template.functions) or "declare it under 'functions:'",
                )
                return None
            try:
                return function.as_function_spec(self.template.metadata.version)
            except ConfigurationError:
                return None  # reported as E319 by check_schemas
        spec = self.registry.function(step.uses)
        if spec is None:
            self.diag(
                "E301",
                f"unknown function {step.uses!r}",
                step.path.key("use"),
                step.location,
                hint=_suggest(step.uses, self.registry.functions),
            )
        return spec

    def check_template_call(self, step: UseStep, ctx: _Ctx) -> None:
        ref = step.template_ref
        assert ref is not None
        same_id = ref.template_id is not None and ref.template_id == self.template.id
        same_file = (
            ref.path is not None
            and self.template.source_file is not None
            and _same_file(self.template.source_file, ref.path)
        )
        if same_id or same_file:
            self.diag(
                "E324",
                "a template cannot call its own operations through template:",
                step.path.key("use"),
                step.location,
                hint="share logic between operations with template functions (use: local.<name>)",
            )
        if step.body is not None:
            self.diag(
                "E304", "template: calls take no 'do:' block", step.path.key("do"), step.location
            )
        for node in (step.args, step.secrets):
            if node is not None:
                self.check_value(node, ctx)

    def check_args(self, step: UseStep, spec: FunctionSpec, ctx: _Ctx) -> None:
        args = step.args if isinstance(step.args, MapNode) else None
        if args is not None:
            for key, node in args.entries:
                if key in spec.lazy_args:
                    extra = set(spec.arg_bindings.get(key, ()))
                    self.check_value(node, replace(ctx, roots=ctx.roots | extra))
                else:
                    self.check_value(node, ctx)
        schema = dict(spec.input)
        properties = dict(schema.get("properties", {}))
        for lazy in spec.lazy_args:
            properties[lazy] = {}
        given = set(args.keys()) if args is not None else set()
        schema["properties"] = properties
        schema["required"] = [
            r for r in schema.get("required", []) if r not in spec.from_prev_args or r in given
        ]
        base = step.args.path if step.args is not None else step.path.key("with")
        checked = args
        if args is not None:  # a literal null optional argument counts as omitted
            checked = replace(
                args,
                entries=tuple(
                    (key, node)
                    for key, node in args.entries
                    if not (
                        isinstance(node, Lit)
                        and node.value is None
                        and key not in spec.required_args
                    )
                ),
            )
        self.check_literal_schema(checked, schema, "E303", spec.name, base, step.location)
        if args is not None:
            self.check_targets(args, spec)
            self.check_extraction(args, spec)

    def check_literal_schema(
        self,
        node: ValueNode | None,
        schema: Mapping[str, Any],
        code: str,
        what: str,
        base: TemplatePath | None = None,
        location: SourceLocation | None = None,
    ) -> None:
        """Validate literal parts of ``node``; expression values are only checked for presence."""
        instance = partial_literal(node) if node is not None else {}
        root = base if base is not None else (node.path if node is not None else TemplatePath())
        dynamic = (
            [holder.path.parts for _, holder in walk_expressions(node)] if node is not None else []
        )
        try:
            compiled = self.schemas.compile(schema, definitions=self.template.schemas)
            violations = compiled.validate(instance)
        except Exception:  # a broken schema is reported as E319 by check_schemas
            return
        for violation in violations:
            full = (*root.parts, *violation.path.parts)
            if any(full[: len(d)] == d for d in dynamic):
                continue
            self.diag(code, f"{what}: {violation.message}", TemplatePath(full), location)

    def check_targets(self, args: MapNode, spec: FunctionSpec) -> None:
        adapter = self.registry.adapter(spec.adapter) if spec.adapter else None
        if adapter is None or adapter.spec.target_kind is not TargetKind.NETWORK:
            return
        for name in spec.target_args:
            node = args.get(name)
            text = _literal_prefix(node)
            if text is None:
                continue
            parts = urlsplit(text)
            if parts.scheme not in adapter.spec.schemes or not parts.hostname:
                continue
            try:
                port = parts.port
            except ValueError:
                self.diag("E303", f"invalid port in URL {text!r}", node.path if node else args.path)
                continue
            if port == _DEFAULT_PORTS.get(parts.scheme):
                port = None
            if not self.template.permissions.allows(parts.hostname, port):
                shown = parts.hostname + (f":{port}" if port else "")
                self.diag(
                    "E318",
                    f"host {shown!r} is not in permissions.hosts",
                    node.path if node else args.path,
                    node.location if node else None,
                    hint=f"add '{shown}' to permissions.hosts",
                )

    def check_extraction(self, args: MapNode, spec: FunctionSpec) -> None:
        extractor_name = spec.extractor
        if spec.name == "extract.auto":
            using = args.get("using")
            extractor_name = (
                using.value if isinstance(using, Lit) and isinstance(using.value, str) else None
            )
            if extractor_name is not None and self.registry.extractor(extractor_name) is None:
                self.diag(
                    "E326",
                    f"extractor {extractor_name!r} is not registered",
                    using.path,  # type: ignore[union-attr]
                    hint=_suggest(extractor_name, self.registry.extractors),
                )
                extractor_name = None
        fields_node = args.get("fields")
        fields = None
        if fields_node is not None and is_literal(fields_node):
            try:
                fields = parse_fields(literal_value(fields_node), fields_node.path)
            except FieldSpecError as exc:
                self.diag("E303", f"{spec.name}: {exc}", exc.path, fields_node.location)
        extractor = self.registry.extractor(extractor_name) if extractor_name else None
        if extractor is None:
            return
        ext = extractor.spec

        def check(language: str, source: str, path: TemplatePath) -> None:
            if language not in ext.query_languages:
                self.diag(
                    "E326",
                    f"the {ext.name} extractor does not support {language!r} queries",
                    path,
                    hint=f"supported: {', '.join(ext.query_languages)}",
                )
                return
            problem = extractor.check_query(language, source)
            if problem:
                self.diag("E327", problem, path)

        def check_attr(attr: str, path: TemplatePath) -> None:
            if not ext.supports_attribute(attr):
                self.diag(
                    "E326",
                    f"the {ext.name} extractor cannot read attribute {attr!r}",
                    path,
                    hint=f"readable: {', '.join(ext.attributes)}",
                )

        root = args.get("root")
        for source, path in _literal_queries(root):
            check(ext.default_language, source, path)
        selector = args.get("selector")
        language_node = args.get("language")
        language = (
            language_node.value
            if isinstance(language_node, Lit) and isinstance(language_node.value, str)
            else ext.default_language
        )
        for source, path in _literal_queries(selector):
            check(language, source, path)
        attr = args.get("attr")
        if isinstance(attr, Lit) and isinstance(attr.value, str):
            check_attr(attr.value, attr.path)
        for f in iter_fields(fields or ()):
            for i, query in enumerate(f.queries):
                key = f.path.key(query.language or "selector")
                check(
                    query.language or ext.default_language,
                    query.source,
                    key.index(i) if len(f.queries) > 1 else key,
                )
            if f.attr is not None:
                check_attr(f.attr, f.path.key("attr"))

    def check_unused_result(self, steps: tuple[Step, ...], index: int, ctx: _Ctx) -> None:
        """W002: a pure function's result that is provably discarded."""
        step = steps[index]
        if not isinstance(step, UseStep) or step.id is not None or index + 1 >= len(steps):
            return
        spec = self.registry.function(step.uses)
        if spec is None or not spec.pure:
            return
        nxt = steps[index + 1]
        if "prev" in _step_roots(nxt):
            return
        if isinstance(nxt, UseStep):
            nxt_spec = self.registry.function(nxt.uses)
            given = set(nxt.args.keys()) if isinstance(nxt.args, MapNode) else set()
            discards = (
                nxt_spec is not None
                and nxt_spec.pure
                and not (nxt_spec.from_prev_args - given)
                and nxt.when is None
            )
        else:
            discards = (
                isinstance(nxt, ReturnStep | FailStep | BreakStep | ContinueStep)
                and nxt.when is None
            )
        if discards:
            self.diag(
                "W002",
                f"the result of {spec.name} is never used",
                step.path,
                step.location,
                hint="remove the step, give it an id, or use prev in the next step",
            )

    # ── outputs & tests ───────────────────────────────────────────────────────
    def check_output(self, operation: Operation) -> None:
        output = operation.output
        if output.mode is OutputMode.EMIT:
            if output.value is not None:
                self.diag(
                    "E310",
                    "output.value cannot be used together with emit",
                    output.value.path,
                    output.value.location,
                    hint="remove output.value, or replace emit steps with a value",
                )
            schema = self.resolve_schema(output.schema)
            if schema.get("type") != "array":
                self.diag(
                    "E311",
                    "an operation that emits items needs an array output schema",
                    output.path.key("schema"),
                    hint="use 'type: array' with an 'items:' schema",
                )
        if output.value is not None:
            ids = frozenset(s.id for s in operation.steps.steps if s.id is not None)
            ctx = _Ctx(owner=operation, roots=OPERATION_ROOTS, visible_steps=ids)
            self.check_value(output.value, ctx)

    def resolve_schema(self, schema: Mapping[str, Any]) -> Mapping[str, Any]:
        ref = schema.get("$ref")
        if isinstance(ref, str) and ref.startswith("#/schemas/"):
            target = self.template.schemas.get(ref.removeprefix("#/schemas/"))
            if isinstance(target, Mapping):
                return target
        return schema

    def check_tests(self, operation: Operation) -> None:
        for test in operation.tests:
            for assertion in test.expect.assertions:
                for expression, holder in walk_expressions(assertion):
                    self.check_roots(expression, holder, TEST_ROOTS, None, "test assertions")

    # ── expressions ───────────────────────────────────────────────────────────
    def check_value(self, node: ValueNode, ctx: _Ctx) -> None:
        for expression, holder in walk_expressions(node):
            self.check_roots(expression, holder, ctx.roots, ctx, None)

    def check_roots(
        self,
        expression: Expression,
        holder: Expr,
        roots: Iterable[str],
        ctx: _Ctx | None,
        where: str | None,
    ) -> None:
        allowed = frozenset(roots)
        analysis = analyze(expression)
        path, location = holder.path, holder.location
        for ref in analysis.references:
            root = ref.root
            if root not in allowed:
                self.unknown_root(root, path, location, ctx, where, allowed)
                continue
            if ctx is None or not ref.path:
                continue
            head = ref.path[0]
            if root == "steps":
                self.check_step_ref(head, path, location, ctx)
            elif root == "inputs" and isinstance(ctx.owner, Operation):
                if not isinstance(head, str) or ctx.owner.input(head) is None:
                    self.diag(
                        "E315",
                        f"operation {ctx.owner.name!r} has no input {head!r}",
                        path,
                        location,
                        hint=_suggest(str(head), [i.name for i in ctx.owner.inputs]),
                    )
            elif root == "secrets" and head not in self.template.secrets:
                self.diag(
                    "E316",
                    f"secret {head!r} is not declared",
                    path,
                    location,
                    hint=f"declare it under 'secrets:' ({head}: {{description: ...}})",
                )
            elif root == "vars" and head not in self.info.assigned_vars:
                self.diag(
                    "E306",
                    f"variable {head!r} is never assigned",
                    path,
                    location,
                    hint=_suggest(str(head), self.info.assigned_vars)
                    or "assign it with a 'set:' step",
                )
        for call in analysis.calls:
            spec = self.registry.helpers.get(call.name)
            if spec is None:
                self.diag(
                    "E313",
                    f"unknown helper {call.name!r}",
                    path,
                    location,
                    hint=_suggest(call.name, self.registry.helpers.helpers),
                )
                continue
            if not spec.accepts(call.arg_count):
                self.diag(
                    "E314",
                    f"{call.name}() takes {spec.arity_text}, got {call.arg_count}",
                    path,
                    location,
                )
            for position in call.lambda_positions:
                if position not in spec.lambda_args:
                    self.diag(
                        "E322",
                        f"argument {position + 1} of {call.name}() cannot be a lambda",
                        path,
                        location,
                    )

    def unknown_root(
        self,
        root: str,
        path: TemplatePath,
        location: SourceLocation | None,
        ctx: _Ctx | None,
        where: str | None,
        allowed: frozenset[str],
    ) -> None:
        if root == "inputs" and ctx is not None and ctx.is_function:
            self.diag(
                "E315",
                "inputs is not available inside template functions",
                path,
                location,
                hint="pass the values you need as arguments and read them from args",
            )
            return
        if root == "args" and ctx is not None and not ctx.is_function:
            message = "args is only available inside template functions"
        elif root == "error":
            message = "error is only available inside on_error"
        elif where is not None:
            message = f"{root!r} is not available in {where}"
        else:
            message = f"unknown name {root!r}"
        self.diag(
            "E328",
            message,
            path,
            location,
            hint=_suggest(root, allowed) or f"available: {', '.join(sorted(allowed))}",
        )

    def check_step_ref(
        self, head: str | int, path: TemplatePath, location: SourceLocation | None, ctx: _Ctx
    ) -> None:
        if isinstance(head, str) and head in ctx.visible_steps:
            return
        if isinstance(head, str) and head in self.info.step_ids:
            message = f"step {head!r} is not visible here (it is later or inside another block)"
        else:
            message = f"unknown step {head!r}"
        self.diag("E305", message, path, location, hint=_suggest(str(head), ctx.visible_steps))


# ── helpers ───────────────────────────────────────────────────────────────────


def _is_local(step: Step) -> TypeGuard[UseStep]:
    return isinstance(step, UseStep) and step.is_local_call


def _find_cycle(start: str, calls: Mapping[str, set[str]]) -> list[str] | None:
    stack: list[tuple[str, list[str]]] = [(start, [start])]
    seen: set[str] = set()
    while stack:
        name, trail = stack.pop()
        for callee in sorted(calls.get(name, ())):
            if callee == start:
                return [*trail, start]
            if callee not in seen:
                seen.add(callee)
                stack.append((callee, [*trail, callee]))
    return None


def _suggest(name: str, candidates: Iterable[str]) -> str | None:
    matches = difflib.get_close_matches(name, list(candidates), n=1, cutoff=0.6)
    return f"did you mean {matches[0]!r}?" if matches else None


def _same_file(template_file: str, ref_path: str) -> bool:
    base = Path(template_file)
    return (base.parent / ref_path).resolve() == base.resolve()


def _literal_prefix(node: ValueNode | None) -> str | None:
    """The literal text of a URL argument, if its host part is fully literal."""
    if isinstance(node, Lit) and isinstance(node.value, str):
        return node.value
    if isinstance(node, Expr) and not node.is_typed:
        first = node.scalar.parts[0]  # type: ignore[union-attr]
        if isinstance(first, str) and "://" in first:
            rest = first.split("://", 1)[1]
            if any(c in rest for c in "/?#:"):
                return first
    return None


def _literal_queries(node: ValueNode | None) -> list[tuple[str, TemplatePath]]:
    if isinstance(node, Lit) and isinstance(node.value, str):
        return [(node.value, node.path)]
    if isinstance(node, ListNode):
        return [
            (n.value, n.path) for n in node.items if isinstance(n, Lit) and isinstance(n.value, str)
        ]
    return []


def _step_roots(step: Step) -> frozenset[str]:
    """Roots referenced by a step's own expressions (not its nested blocks)."""
    nodes: list[ValueNode] = []
    if step.when is not None:
        nodes.append(step.when)
    match step:
        case UseStep(args=args, secrets=secrets):
            nodes.extend(n for n in (args, secrets) if n is not None)
        case IfStep(condition=c) | WhileStep(condition=c) | AssertStep(condition=c):
            nodes.append(c)
        case ForEachStep(items=items):
            nodes.append(items)
        case SetStep(assignments=assignments):
            nodes.extend(n for _, n in assignments)
        case EmitStep(value=v) | ReturnStep(value=v):
            nodes.append(v)
        case FailStep(message=m) | LogStep(message=m):
            nodes.append(m)
        case _:
            pass
    roots: set[str] = set()
    for node in nodes:
        for expression, _ in walk_expressions(node):
            roots |= analyze(expression).roots
    return frozenset(roots)


__all__ = ["StaticValidator"]

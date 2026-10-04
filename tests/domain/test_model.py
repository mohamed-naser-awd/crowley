from typing import Any

import pytest

from crowley.domain.adapters import AdapterSpec, TargetKind
from crowley.domain.common import ByteSize, Duration, Rate, SemVer, TemplatePath
from crowley.domain.errors import ConfigurationError
from crowley.domain.expressions import parse_scalar
from crowley.domain.extractors import ExtractorSpec, FieldSpecError, iter_fields, parse_fields
from crowley.domain.functions import FunctionKind, FunctionSpec, Outcome, Requirement
from crowley.domain.steps import (
    Block,
    EmitStep,
    Expr,
    ForEachStep,
    IfStep,
    ListNode,
    Lit,
    MapNode,
    UseStep,
    child_blocks,
    is_literal,
    iter_steps,
    literal_value,
    partial_literal,
    walk_expressions,
)
from crowley.domain.template import (
    DEFAULT_LIMITS,
    HostPattern,
    InputSpec,
    Limits,
    Metadata,
    Operation,
    OutputMode,
    OutputSpec,
    Permissions,
    Template,
    TemplateFunction,
)

V1 = SemVer.parse("1.0.0")
ROOT = TemplatePath.of("x")

# ── permissions ───────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("pattern", "host", "port", "allowed"),
    [
        ("api.example.com", "api.example.com", None, True),
        ("api.example.com", "API.Example.com.", None, True),
        ("api.example.com", "api.example.com", 8443, False),
        ("api.example.com", "evil.com", None, False),
        ("*.example.com", "a.example.com", None, True),
        ("*.example.com", "a.b.example.com", None, True),
        ("*.example.com", "example.com", None, False),
        ("*.example.com", "badexample.com", None, False),
        ("example.com:8443", "example.com", 8443, True),
        ("example.com:8443", "example.com", None, False),
        ("10.0.0.1", "10.0.0.1", None, True),
        ("[::1]:8080", "[::1]", 8080, True),
        ("[::1]:8080", "::1", 8080, True),
        ("::1", "0:0:0:0:0:0:0:1", None, True),
        ("*.example.com", "10.0.0.1", None, False),
    ],
)
def test_host_patterns(pattern: str, host: str, port: int | None, allowed: bool) -> None:
    assert HostPattern.parse(pattern).matches(host, port) is allowed


@pytest.mark.parametrize(
    "pattern",
    [
        "",
        "*",
        "*.com",
        "a.*.com",
        "ex ample.com",
        "-bad.com",
        "x.com:0",
        "x.com:70000",
        "[::1",
        "[::1]x",
        "[::1]:ab",
    ],
)
def test_host_pattern_rejects(pattern: str) -> None:
    with pytest.raises(ValueError, match=r"host|port|broad|pattern"):
        HostPattern.parse(pattern)


def test_permissions_allows_any_matching_pattern() -> None:
    perms = Permissions(hosts=(HostPattern.parse("a.com"), HostPattern.parse("*.b.com")))
    assert perms.allows("a.com")
    assert perms.allows("x.b.com")
    assert not perms.allows("b.com")
    assert str(perms.hosts[1]) == "*.b.com"
    assert not Permissions().allows("a.com")


# ── limits ────────────────────────────────────────────────────────────────────


def test_limits_strictest_and_resolved() -> None:
    sdk = Limits(max_requests=500, rate_per_host=Rate.parse("5/s"))
    template = Limits(
        max_requests=200, max_duration=Duration.parse("10m"), rate_per_host=Rate.parse("600/m")
    )
    operation = Limits(max_requests=300, max_response_bytes=ByteSize.parse("1MB"))
    combined = Limits.strictest(sdk, template, operation)
    assert combined.max_requests == 200
    assert combined.max_duration == Duration(600.0)
    assert combined.max_response_bytes == ByteSize(1024**2)
    assert combined.rate_per_host == Rate.parse("5/s")
    assert combined.max_items is None
    resolved = combined.resolved()
    assert resolved.max_items == DEFAULT_LIMITS.max_items
    assert resolved.max_requests == 200
    assert Limits().resolved() == DEFAULT_LIMITS


# ── requirements & function specs ─────────────────────────────────────────────


@pytest.mark.parametrize(
    ("text", "kind", "name", "rendered"),
    [
        ("mycorp.decode@^1", "function", "mycorp.decode", "mycorp.decode@^1"),
        ("mycorp.*@^2", "function", "mycorp.*", "mycorp.*@^2"),
        ("adapter:ws@^1", "adapter", "ws", "adapter:ws@^1"),
        ("extractor:pdf", "extractor", "pdf", "extractor:pdf"),
    ],
)
def test_requirement_parse(text: str, kind: str, name: str, rendered: str) -> None:
    req = Requirement.parse(text)
    assert (req.kind, req.name, str(req)) == (kind, name, rendered)


@pytest.mark.parametrize(
    "text", ["mycorp", "adapter:a.b", "thing:x", "Mycorp.x", "mycorp.x@^zz", "a.b.c"]
)
def test_requirement_rejects(text: str) -> None:
    with pytest.raises(ValueError, match="invalid requirement"):
        Requirement.parse(text)


def test_requirement_matching() -> None:
    assert Requirement.parse("mycorp.*").matches_name("mycorp.anything")
    assert not Requirement.parse("mycorp.*").matches_name("other.x")
    assert Requirement.parse("mycorp.a").matches_name("mycorp.a")
    assert not Requirement.parse("mycorp.a").matches_name("mycorp.b")


def _spec(**kwargs: Any) -> FunctionSpec:
    return FunctionSpec(name=kwargs.pop("name", "acme.fn"), version=V1, **kwargs)


def test_function_spec_markers() -> None:
    spec = _spec(
        kind=FunctionKind.BLOCK,
        body_bindings=("page",),
        input={
            "type": "object",
            "required": ["url"],
            "properties": {
                "url": {"type": "string", "x-crowley-target": True},
                "source": {"x-crowley-from-prev": True},
                "stop_when": {"x-crowley-lazy": True, "x-crowley-bindings": ["result", "page"]},
                "plain": {"type": "integer"},
            },
        },
    )
    assert spec.namespace == "acme"
    assert spec.required_args == {"url"}
    assert spec.target_args == {"url"}
    assert spec.from_prev_args == {"source"}
    assert spec.lazy_args == {"stop_when"}
    assert spec.arg_bindings == {"stop_when": ("result", "page")}


@pytest.mark.parametrize(
    "kwargs",
    [
        {"name": "nonamespace"},
        {"input": {"type": "array"}},
        {"input": {"type": "object", "properties": {"prev": {}}}},
        {"input": {"type": "object", "properties": {"x": {"x-crowley-bindings": "result"}}}},
        {"body_bindings": ("page",)},
    ],
)
def test_function_spec_rejects(kwargs: dict[str, Any]) -> None:
    with pytest.raises(ConfigurationError) as info:
        _spec(**kwargs)
    assert info.value.code == "E903"


def test_function_spec_tolerates_odd_properties() -> None:
    spec = _spec(input={"type": "object", "properties": "nope"})
    assert spec.properties == {}


def test_outcome_flags() -> None:
    assert Outcome(value=1).has_value
    assert Outcome(has_value=False).is_continue
    assert not Outcome(has_value=False, is_break=True).is_continue


# ── adapter / extractor specs ─────────────────────────────────────────────────


def test_adapter_spec_validation() -> None:
    AdapterSpec(name="http", version=V1, schemes=("http", "https"))
    AdapterSpec(name="stub", version=V1, target_kind=TargetKind.NONE)
    for kwargs in (
        {"name": "Bad-Name", "schemes": ("x",)},
        {"name": "files", "target_kind": TargetKind.LOCAL},
        {"name": "ws"},
    ):
        with pytest.raises(ConfigurationError):
            AdapterSpec(version=V1, **kwargs)  # type: ignore[arg-type]


def test_extractor_spec_validation() -> None:
    spec = ExtractorSpec(
        name="text",
        version=V1,
        query_languages=("regex",),
        default_language="regex",
        attributes=("text", "group:"),
    )
    assert spec.supports_attribute("text")
    assert spec.supports_attribute("group:1")
    assert not spec.supports_attribute("group:")
    assert not spec.supports_attribute("href")
    star = ExtractorSpec(
        name="html", version=V1, query_languages=("css",), default_language="css", attributes=("*",)
    )
    assert star.supports_attribute("data-id")
    for kwargs in (
        {"name": "X"},
        {"query_languages": ()},
        {"default_language": "xpath"},
        {"query_languages": ("css", "selector"), "default_language": "css"},
    ):
        base: dict[str, Any] = {
            "name": "html",
            "query_languages": ("css",),
            "default_language": "css",
        }
        with pytest.raises(ConfigurationError):
            ExtractorSpec(version=V1, **{**base, **kwargs})


# ── field specs ───────────────────────────────────────────────────────────────


def test_parse_fields() -> None:
    fields = parse_fields(
        {
            "title": "h1",
            "price": {"xpath": "//span/@content", "required": True},
            "tags": {"selector": [".tag", ".label"], "all": True},
            "stock": {"selector": ".stock", "default": "unknown"},
            "self": {"attr": "data-id"},
            "author": {"selector": ".byline", "fields": {"name": {"selector": ".name"}}},
        },
        TemplatePath.of("with", "fields"),
    )
    by_name = {f.name: f for f in fields}
    assert by_name["title"].queries[0].language == ""
    assert by_name["price"].queries[0].language == "xpath"
    assert by_name["price"].required
    assert [q.source for q in by_name["tags"].queries] == [".tag", ".label"]
    assert by_name["tags"].all
    assert by_name["stock"].has_default
    assert by_name["stock"].default == "unknown"
    assert by_name["self"].queries == ()
    nested = by_name["author"].fields
    assert nested is not None
    assert str(nested[0].path) == "with.fields.author.fields.name"
    assert [f.name for f in iter_fields(fields)] == [
        "title",
        "price",
        "tags",
        "stock",
        "self",
        "author",
        "name",
    ]


@pytest.mark.parametrize(
    ("raw", "path"),
    [
        ({}, "f"),
        ([], "f"),
        ({"a": 1}, "f.a"),
        ({"a": {"selector": "x", "xpath": "y"}}, "f.a"),
        ({"a": {"selector": ""}}, "f.a.selector"),
        ({"a": {"selector": []}}, "f.a.selector"),
        ({"a": {"attr": ""}}, "f.a.attr"),
        ({"a": {"all": "yes"}}, "f.a.all"),
        ({"a": {"required": True, "default": 1}}, "f.a"),
        ({"a": {"attr": "x", "fields": {"b": "c"}}}, "f.a"),
        ({"a": {"fields": {}}}, "f.a.fields"),
    ],
)
def test_parse_fields_rejects(raw: Any, path: str) -> None:
    with pytest.raises(FieldSpecError) as info:
        parse_fields(raw, TemplatePath.of("f"))
    assert str(info.value.path) == path


# ── value nodes & steps ───────────────────────────────────────────────────────


def _expr(text: str, path: TemplatePath = ROOT) -> Expr:
    scalar = parse_scalar(text)
    assert not isinstance(scalar, str)
    return Expr(scalar=scalar, path=path)  # type: ignore[arg-type]


def test_value_node_helpers() -> None:
    literal = MapNode(
        entries=(
            ("a", Lit(value=1, path=ROOT)),
            ("b", ListNode(items=(Lit(value="x", path=ROOT),), path=ROOT)),
        ),
        path=ROOT,
    )
    assert is_literal(literal)
    assert literal_value(literal) == {"a": 1, "b": ["x"]}
    assert literal.keys() == ("a", "b")
    assert literal.get("a") == Lit(value=1, path=ROOT)
    assert literal.get("zz") is None

    mixed = MapNode(
        entries=(
            ("a", Lit(value=1, path=ROOT)),
            ("b", _expr("${{ x }}")),
            ("c", _expr("p/${{ y }}/${{ z }}")),
        ),
        path=ROOT,
    )
    assert not is_literal(mixed)
    with pytest.raises(ValueError, match="contains expressions"):
        literal_value(mixed)
    assert partial_literal(mixed) == {"a": 1, "b": None, "c": None}
    assert [str(e) for e, _ in walk_expressions(ListNode(items=(mixed,), path=ROOT))] == [
        "x",
        "y",
        "z",
    ]
    assert _expr("${{ x }}").is_typed
    assert not _expr("a${{ x }}").is_typed


def test_steps_tree_walk() -> None:
    inner = EmitStep(value=Lit(value=1, path=ROOT), id="e")
    loop = ForEachStep(items=_expr("${{ xs }}"), body=Block(steps=(inner,)), id="loop")
    branch = IfStep(
        condition=_expr("${{ c }}"), then=Block(steps=(loop,)), otherwise=Block(steps=())
    )
    call = UseStep(uses="http.get", body=None, id="get")
    block = Block(steps=(call, branch))
    assert [s.label for s in iter_steps(block)] == ["get", "if", "loop", "e"]
    assert child_blocks(call) == ()
    assert len(child_blocks(branch)) == 2
    assert UseStep(uses="local.f").is_local_call
    assert not call.is_template_call
    assert loop.as_name == "item"
    assert loop.kind == "for_each"


# ── template model ────────────────────────────────────────────────────────────


def _operation(name: str, **kwargs: Any) -> Operation:
    return Operation(
        name=name,
        description="d",
        steps=Block(steps=()),
        output=OutputSpec(schema={"type": "object"}, mode=OutputMode.PIPE),
        **kwargs,
    )


def test_operation_inputs_schema() -> None:
    op = _operation(
        "get_people",
        inputs=(
            InputSpec(name="company", schema={"type": "string", "description": "slug"}),
            InputSpec(name="max_pages", schema={"type": "integer", "default": 5}),
        ),
        title="Get people",
    )
    assert op.inputs_schema == {
        "type": "object",
        "properties": {
            "company": {"type": "string", "description": "slug"},
            "max_pages": {"type": "integer", "default": 5},
        },
        "required": ["company"],
        "additionalProperties": False,
    }
    assert op.input("max_pages") is not None
    assert op.input("nope") is None
    assert op.inputs[0].description == "slug"
    assert op.inputs[1].default == 5
    assert op.display_name == "Get people"
    assert _operation("x").display_name == "x"


def test_template_operation_lookup_and_limits() -> None:
    meta = Metadata(id="acme/site", version=V1, name="Site", description="d")
    single = Template(metadata=meta, operations={"only": _operation("only")})
    assert single.operation().name == "only"
    assert single.id == "acme/site"
    multi = Template(
        metadata=meta,
        operations={"a": _operation("a", limits=Limits(max_requests=5)), "b": _operation("b")},
        limits=Limits(max_requests=50),
    )
    with pytest.raises(KeyError):
        multi.operation()
    assert multi.operation("b").name == "b"
    assert multi.limits_for("a").max_requests == 5
    assert multi.limits_for("b").max_requests == 50


def test_template_function_spec() -> None:
    fn = TemplateFunction(
        name="fetch_json", description="d", steps=Block(steps=()), output={"type": "object"}
    )
    spec = fn.as_function_spec(V1)
    assert spec.name == "local.fetch_json"
    assert spec.kind is FunctionKind.PLAIN
    assert fn.qualified_name == "local.fetch_json"

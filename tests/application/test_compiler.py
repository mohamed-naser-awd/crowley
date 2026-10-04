from pathlib import Path
from typing import Any

import pytest

from crowley.application.compiler import Compiler
from crowley.application.ports import Origin, TemplateText
from crowley.application.use_cases import LoadResult, LoadTemplate
from crowley.domain.errors import TemplateParseError, TemplateSemanticError
from crowley.domain.steps import (
    AssertStep,
    EmitStep,
    Expr,
    FailStep,
    ForEachStep,
    IfStep,
    Lit,
    LogLevel,
    LogStep,
    MapNode,
    SetStep,
    UseDefault,
    UseStep,
    WhileStep,
)
from crowley.domain.template import OutputMode
from crowley.infrastructure.schema import JsonSchemaValidator
from crowley.infrastructure.sources import FileSource, InMemorySource
from crowley.infrastructure.yaml import RuamelTemplateParser

EXAMPLE = Path(__file__).parents[2] / "examples" / "company-directory" / "company-directory.yml"

HEADER = """\
crowley: 1
id: acme/site
version: 1.0.0
name: Site
description: Test template.
permissions:
  hosts: [api.example.com]
"""


def loader(**templates: str) -> LoadTemplate:
    return LoadTemplate(
        sources=[InMemorySource(templates), FileSource()],
        parser=RuamelTemplateParser(),
        compiler=Compiler(JsonSchemaValidator()),
    )


def compile_text(text: str) -> LoadResult:
    return loader().compile_text(TemplateText(text=text, origin=Origin(name="t.yml", file="t.yml")))


def with_steps(
    steps: str, *, output: str = "    output:\n      schema: {}\n", extra: str = ""
) -> str:
    body = "\n".join("      " + line if line else line for line in steps.strip("\n").splitlines())
    return f"{HEADER}{extra}operations:\n  op:\n    description: d\n    steps:\n{body}\n{output}"


def codes(result: LoadResult) -> list[str]:
    return [d.code for d in result.report.diagnostics]


def only(result: LoadResult, code: str) -> Any:
    assert codes(result) == [code], [str(d) for d in result.report.diagnostics]
    return result.report.diagnostics[0]


# ── the SPEC example ──────────────────────────────────────────────────────────


async def test_compiles_the_spec_example() -> None:
    result = await loader().report(str(EXAMPLE))
    assert result.report.ok, [str(d) for d in result.report.diagnostics]
    t = result.template
    assert t is not None
    assert t.id == "examples/company-directory"
    assert str(t.metadata.version) == "1.0.0"
    assert t.metadata.tags == ("companies", "example")
    assert t.source_file == str(EXAMPLE.resolve())
    assert len(t.content_hash) == 64
    assert list(t.operations) == ["get_page_info", "get_page_people"]
    assert list(t.functions) == ["fetch_json", "normalize_person"]
    assert t.permissions.allows("directory.example.com")
    assert t.secrets["API_TOKEN"].required
    assert t.limits.max_requests == 200
    assert set(t.schemas) == {"person"}
    assert t.defaults["http"].keys() == ("headers", "timeout", "retry")

    info = t.operations["get_page_info"]
    assert info.output.mode is OutputMode.VALUE
    assert isinstance(info.output.value, MapNode)
    assert info.inputs_schema["required"] == ["company"]
    assert info.tests[0].fixtures == "fixtures/page-info-acme.cassette.json"

    people = t.operations["get_page_people"]
    assert people.output.mode is OutputMode.EMIT
    paginate, loop = people.steps.steps
    assert isinstance(paginate, UseStep)
    assert paginate.uses == "paginate.by_cursor"
    assert paginate.body is not None
    assert isinstance(loop, ForEachStep)
    assert loop.as_name == "person"
    assert isinstance(loop.items, Expr)
    assert loop.items.is_typed
    assert people.tests[0].expect.min_items == 1
    assert len(people.tests[0].expect.assertions) == 1
    assert people.location is not None
    assert people.location.line > 1

    fetch = t.functions["fetch_json"]
    assert fetch.input["required"] == ["path"]
    assert fetch.location is not None


# ── stages 1 and 2 ────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("text", "code"),
    [
        ("- just a list\n", "E205"),
        ("id: acme/site\n", "E202"),
        (HEADER.replace("crowley: 1", "crowley: 2"), "E103"),
        (HEADER.replace("crowley: 1", "crowley: true"), "E103"),
        (HEADER + "operations: {}\n", "E204"),
    ],
)
def test_version_and_shape(text: str, code: str) -> None:
    only(compile_text(text), code)


def test_meta_schema_errors_are_mapped_and_located() -> None:
    text = (
        with_steps("- log: hi")
        .replace("name: Site", "name: Site\nbogus: 1")
        .replace("version: 1.0.0", "version: 1.0")
    )
    result = compile_text(text)
    assert result.template is None
    by_code = {d.code: d for d in result.report.diagnostics}
    assert by_code["E201"].path == "bogus"
    assert by_code["E201"].location.line == 5
    assert by_code["E205"].path == "version"
    missing = compile_text(
        HEADER.replace("permissions:\n  hosts: [api.example.com]\n", "")
        + "operations:\n  op: {description: d, steps: [], output: {schema: {}}}\n"
    )
    assert only(missing, "E202").message == "missing required key 'permissions'"


def test_parse_errors_become_reports() -> None:
    result = compile_text("a: [1\n")
    assert codes(result) == ["E101"]
    assert result.template is None


# ── steps ─────────────────────────────────────────────────────────────────────


def test_all_step_kinds_compile() -> None:
    result = compile_text(
        with_steps(
            """
- id: get
  use: http.get
  with: {url: "https://api.example.com/x?p=${{ inputs.p }}"}
  timeout: 5s
  on_error: skip
- if: ${{ prev.status == 200 }}
  then:
    - set: {count: 1}
  else:
    - fail: nope
      code: bad
- for_each: [1, 2]
  as: n
  index_as: i
  concurrency: 2
  do:
    - emit: ${{ n }}
- while: false
  max_iterations: 3
  do:
    - break: true
- assert: true
  message: always
- log: "page ${{ 1 }}"
  level: debug
- continue: true
  when: ${{ false }}
- return: ${{ 1 }}
"""
        )
    )
    assert result.report.ok, [str(d) for d in result.report.diagnostics]
    assert result.template is not None
    steps = result.template.operations["op"].steps.steps
    kinds = [s.kind for s in steps]
    assert kinds == ["use", "if", "for_each", "while", "assert", "log", "continue", "return"]
    use, branch, loop, wloop, check, log, cont, _ = steps
    assert isinstance(use, UseStep)
    assert use.timeout is not None
    assert use.timeout.seconds == 5
    assert use.on_error is not None
    assert use.on_error.outcome == "skip"
    assert isinstance(use.args, MapNode)
    url = use.args.get("url")
    assert isinstance(url, Expr)
    assert not url.is_typed
    assert isinstance(branch, IfStep)
    assert isinstance(branch.then.steps[0], SetStep)
    assert branch.otherwise is not None
    fail = branch.otherwise.steps[0]
    assert isinstance(fail, FailStep)
    assert fail.code == "bad"
    assert isinstance(loop, ForEachStep)
    assert (loop.as_name, loop.index_as) == ("n", "i")
    assert isinstance(loop.concurrency, Lit)
    assert isinstance(loop.body.steps[0], EmitStep)
    assert isinstance(wloop, WhileStep)
    assert wloop.max_iterations == 3
    assert isinstance(check, AssertStep)
    assert isinstance(log, LogStep)
    assert log.level is LogLevel.DEBUG
    assert cont.when is not None
    assert result.template.operations["op"].output.mode is OutputMode.EMIT


@pytest.mark.parametrize(
    ("steps", "code", "path"),
    [
        ("- name: no kind", "E202", "operations.op.steps[0]"),
        ("- use: http.get\n  if: ${{ true }}", "E205", "operations.op.steps[0]"),
        ("- log: x\n  then: []", "E201", "operations.op.steps[0].then"),
        ("- set: {a: 1}\n  on_error: skip", "E329", "operations.op.steps[0].on_error"),
        ("- if: ${{ true }}", "E202", "operations.op.steps[0]"),
        ("- for_each: [1]", "E202", "operations.op.steps[0]"),
        ("- while: ${{ true }}\n  do: []", "E202", "operations.op.steps[0]"),
        ("- for_each: abc\n  do: []", "E205", "operations.op.steps[0].for_each"),
        ("- for_each: [1]\n  as: prev\n  do: []", "E205", "operations.op.steps[0].as"),
        (
            "- for_each: [1]\n  concurrency: lots\n  do: []",
            "E205",
            "operations.op.steps[0].concurrency",
        ),
        ("- use: nonamespace", "E205", "operations.op.steps[0].use"),
        ("- use: 'template:Bad Ref'", "E324", "operations.op.steps[0].use"),
        ("- if: x == 1\n  then: []", "E205", "operations.op.steps[0].if"),
        ("- if: 'a ${{ b }}'\n  then: []", "E205", "operations.op.steps[0].if"),
        ("- log: ${{ 1 + }}", "E321", "operations.op.steps[0].log"),
        ("- log: ${{ x => x }}", "E322", "operations.op.steps[0].log"),
        (
            "- use: x.y\n  on_error: {catch: [E405]}",
            "E205",
            "operations.op.steps[0].on_error.catch[0]",
        ),
        (
            "- use: x.y\n  on_error: {default: 1, then: skip}",
            "E205",
            "operations.op.steps[0].on_error",
        ),
    ],
)
def test_step_errors(steps: str, code: str, path: str) -> None:
    diagnostic = only(compile_text(with_steps(steps)), code)
    assert diagnostic.path == path
    assert diagnostic.location is not None


def test_wrong_field_hint_names_the_right_kind() -> None:
    diagnostic = only(compile_text(with_steps("- log: x\n  max_iterations: 3")), "E201")
    assert diagnostic.hint == "'max_iterations' belongs to while steps"


def test_unquoted_condition_hint() -> None:
    diagnostic = only(compile_text(with_steps("- if: x == 1\n  then: []")), "E205")
    assert diagnostic.hint == "write '${{ x == 1 }}'"


def test_expression_error_location_points_at_the_value() -> None:
    diagnostic = only(compile_text(with_steps("- log: ${{ 1 + }}")), "E321")
    assert (diagnostic.location.line, diagnostic.location.column) == (12, 14)


def test_error_policies() -> None:
    result = compile_text(
        with_steps(
            """
- use: x.a
  on_error: {default: []}
- use: x.b
  on_error:
    retry: {times: 3, backoff: fixed, delay: 2s}
    then: skip
- use: x.c
  on_error:
    catch: [E601, E603]
    then: {default: null}
- use: x.d
  on_error: {retry: {times: 1}}
"""
        )
    )
    assert result.report.ok, [str(d) for d in result.report.diagnostics]
    assert result.template is not None
    a, b, c, d = (s.on_error for s in result.template.operations["op"].steps.steps)
    assert a is not None
    assert isinstance(a.outcome, UseDefault)
    assert b is not None
    assert b.outcome == "skip"
    assert b.retry is not None
    assert (b.retry.times, b.retry.backoff, b.retry.delay.seconds) == (3, "fixed", 2.0)
    assert c is not None
    assert c.catch == ("E601", "E603")
    assert isinstance(c.outcome, UseDefault)
    assert d is not None
    assert d.outcome == "fail"
    assert d.retry is not None


# ── template-level parts ──────────────────────────────────────────────────────


def test_template_level_parts() -> None:
    text = with_steps(
        "- log: x",
        output=(
            "    output:\n      schema: true\n      value: ${{ prev }}\n"
            "    inputs:\n      flag: true\n"
        ),
        extra="""\
requires: [mycorp.decode@^1, "adapter:ws"]
limits:
  max_duration: 2m
  max_response_bytes: 1MB
  rate: {per_host: 2/s}
x-hub: {category: test}
functions:
  f:
    description: d
    output: false
    prev: {type: string}
    steps: []
""",
    )
    result = compile_text(text)
    assert result.report.ok, [str(d) for d in result.report.diagnostics]
    t = result.template
    assert t is not None
    assert [str(r) for r in t.requires] == ["mycorp.decode@^1", "adapter:ws"]
    assert t.limits.max_duration is not None
    assert t.limits.max_duration.seconds == 120
    assert t.limits.rate_per_host is not None
    assert t.limits.rate_per_host.per_second == 2
    assert t.extensions == {"x-hub": {"category": "test"}}
    assert t.functions["f"].output == {"not": {}}
    assert t.functions["f"].prev == {"type": "string"}
    op = t.operations["op"]
    assert op.output.mode is OutputMode.VALUE
    assert op.output.schema == {}
    assert op.inputs[0].schema == {}


@pytest.mark.parametrize(
    ("extra", "code", "path"),
    [
        ("requires: [nonamespace]\n", "E205", "requires[0]"),
    ],
)
def test_template_level_errors(extra: str, code: str, path: str) -> None:
    diagnostic = only(compile_text(with_steps("- log: x", extra=extra)), code)
    assert diagnostic.path == path


def test_invalid_host_pattern() -> None:
    result = compile_text(
        with_steps("- log: x").replace("hosts: [api.example.com]", "hosts: ['*']")
    )
    assert only(result, "E205").path == "permissions.hosts[0]"


def test_test_assertions_must_be_expressions() -> None:
    output = (
        "    output:\n      schema: {}\n    tests:\n      - name: t\n"
        "        expect:\n          assert: [len(output) > 0]\n"
    )
    diagnostic = only(compile_text(with_steps("- log: x", output=output)), "E205")
    assert diagnostic.path == "operations.op.tests[0].expect.assert[0]"


# ── use case ──────────────────────────────────────────────────────────────────


async def test_load_template_use_case(tmp_path: Path) -> None:
    good = with_steps("- log: x")
    load = loader(good=good, bad="crowley: 1\n")
    template = await load("good")
    assert template.id == "acme/site"
    with pytest.raises(TemplateSemanticError):
        await loader(bad=with_steps("- log: ${{ 1 + }}"))("bad")
    report = await load.report("missing.yml")
    assert codes(report) == ["E104"]
    report = await load.report("not-a-ref")
    assert codes(report) == ["E104"]
    with pytest.raises(TemplateParseError):
        await load("not-a-ref")


def test_load_sync(tmp_path: Path) -> None:
    path = tmp_path / "t.yml"
    path.write_text(with_steps("- log: x"), encoding="utf-8")
    assert loader().load_sync(str(path)).id == "acme/site"

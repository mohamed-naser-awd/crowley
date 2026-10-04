"""``crowley run`` (SPEC §21)."""

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from crowley.domain.errors import CrowleyError
from crowley.interface.cli import exit_code_for, main

TEMPLATE = """\
crowley: 1
id: acme/cli
version: 1.0.0
name: CLI
description: CLI run tests.
permissions:
  hosts: [api.example.com]
secrets:
  TOKEN: { required: false }
operations:
  greet:
    description: Emit greetings.
    inputs:
      names: { type: array, items: { type: string } }
      punctuation: { type: string, default: "!" }
    steps:
      - for_each: ${{ inputs.names }}
        do:
          - emit: ${{ 'hello ' + item + inputs.punctuation }}
    output:
      schema: { type: array, items: { type: string } }
  value:
    description: A value output.
    steps:
      - log: x
    output:
      value: { token: "${{ secrets?.TOKEN }}" }
      schema: {}
  broken:
    description: Fails on purpose.
    steps:
      - fail: nope
    output:
      schema: {}
"""


@pytest.fixture
def template(tmp_path: Path) -> Path:
    path = tmp_path / "cli.yml"
    path.write_text(TEMPLATE, encoding="utf-8")
    return path


def invoke(*args: str, env: dict[str, str] | None = None) -> tuple[int, str, str]:
    result = CliRunner().invoke(main, list(args), env=env)
    return result.exit_code, result.stdout, result.stderr


def test_run_json(template: Path) -> None:
    code, out, _ = invoke("run", str(template), "greet", "--input", 'names=["ada", "alan"]')
    assert code == 0
    assert json.loads(out) == ["hello ada!", "hello alan!"]


def test_run_jsonl_with_inputs_file_and_out(template: Path, tmp_path: Path) -> None:
    inputs = tmp_path / "inputs.json"
    inputs.write_text(json.dumps({"names": ["x"], "punctuation": "?"}), encoding="utf-8")
    target = tmp_path / "out.jsonl"
    code, out, err = invoke(
        "run",
        str(template),
        "greet",
        "--inputs-file",
        str(inputs),
        "--input",
        "punctuation=.",
        "--format",
        "jsonl",
        "--out",
        str(target),
    )
    assert code == 0
    assert out == ""
    assert "1 item(s)" in err
    assert target.read_text(encoding="utf-8") == '"hello x."\n'


def test_run_value_operation_with_secrets(template: Path) -> None:
    code, out, _ = invoke(
        "run", str(template), "value", "--secret", "TOKEN=abc", "--format", "jsonl"
    )
    assert code == 0
    assert out == '{"token": "***"}\n' or out == '{"token": "abc"}\n'
    code, out, _ = invoke(
        "run", f"{template}#value", "--secrets-env", "CW_", env={"CW_TOKEN": "env"}
    )
    assert code == 0
    assert json.loads(out) == {"token": "env"}


@pytest.mark.parametrize(
    ("args", "code", "message"),
    [
        (("greet",), 2, "E401"),
        (("broken",), 3, "E503"),
        (("nope",), 4, "E904"),
    ],
)
def test_run_failures_exit_codes(
    template: Path, args: tuple[str, ...], code: int, message: str
) -> None:
    exit_code, _, err = invoke("run", str(template), *args)
    assert exit_code == code
    assert message in err


def test_run_invalid_template_and_bad_options(tmp_path: Path, template: Path) -> None:
    broken = tmp_path / "broken.yml"
    broken.write_text("crowley: 1\n", encoding="utf-8")
    code, _, err = invoke("run", str(broken))
    assert code == 1
    assert "E202" in err
    code, _, err = invoke("run", str(template), "greet", "--input", "novalue")
    assert code == 2
    assert "KEY=VALUE" in err
    code, _, err = invoke("run", str(template), "greet", "--limit", "max_bananas=1")
    assert "unknown limit" in err
    code, _, err = invoke("run", str(template), "greet", "--limit", "max_duration=forever")
    assert "--limit" in err
    not_object = tmp_path / "list.json"
    not_object.write_text("[1]", encoding="utf-8")
    code, _, err = invoke("run", str(template), "greet", "--inputs-file", str(not_object))
    assert "JSON object" in err


def test_run_limits(template: Path) -> None:
    code, _, err = invoke(
        "run", str(template), "greet", "--input", 'names=["a","b"]', "--limit", "max_items=1"
    )
    assert code == 4
    assert "E701" in err
    code, _, _ = invoke(
        "run",
        str(template),
        "greet",
        "--input",
        'names=["a"]',
        "--limit",
        "max_duration=10s",
        "--limit",
        "max_response_bytes=1MB",
        "--limit",
        "rate_per_host=5/s",
    )
    assert code == 0


@pytest.mark.parametrize(
    ("code", "expected"),
    [("E101", 1), ("E305", 1), ("E405", 2), ("E502", 3), ("E601", 3), ("E701", 4), ("E801", 4)],
)
def test_exit_code_mapping(code: str, expected: int) -> None:
    assert exit_code_for(CrowleyError.from_code(code, "x")) == expected

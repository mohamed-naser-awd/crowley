import json
from pathlib import Path

from click.testing import CliRunner

from crowley import __version__
from crowley.domain.template.meta_schema import META_SCHEMA
from crowley.interface.cli import _printable, main

ROOT = Path(__file__).parents[2]
EXAMPLE = ROOT / "examples" / "company-directory" / "company-directory.yml"
INVALID = ROOT / "tests" / "fixtures" / "invalid"


def invoke(*args: str) -> tuple[int, str]:
    result = CliRunner().invoke(main, list(args))
    return result.exit_code, result.output


def test_version_option() -> None:
    code, output = invoke("--version")
    assert code == 0
    assert output.strip() == f"crowley, version {__version__}"


def test_validate_ok() -> None:
    code, output = invoke("validate", str(EXAMPLE))
    assert code == 0
    assert output.startswith("OK ")
    assert "0 error(s), 0 warning(s)" in output


def test_validate_reports_errors_with_locations() -> None:
    fixture = INVALID / "E305_unknown_step.yml"
    code, output = invoke("validate", str(fixture))
    assert code == 1
    assert output.startswith("FAIL ")
    assert "E305 at operations.op.steps[0].log (" in output
    assert ":13:" in output


def test_warnings_do_not_fail() -> None:
    code, output = invoke("validate", str(INVALID / "W003_unused_function.yml"))
    assert code == 0
    assert "W003" in output
    assert "1 warning(s)" in output


def test_missing_file_exits_4(tmp_path: Path) -> None:
    code, output = invoke("validate", str(EXAMPLE), str(tmp_path / "missing.yml"))
    assert code == 4
    assert "E104" in output
    assert "2 template(s)" in output


def test_validate_json() -> None:
    code, output = invoke(
        "validate", "--format", "json", str(EXAMPLE), str(INVALID / "E316_undeclared_secret.yml")
    )
    assert code == 1
    payload = json.loads(output)
    assert [entry["ok"] for entry in payload] == [True, False]
    error = payload[1]["errors"][0]
    assert error["code"] == "E316"
    assert error["line"] == 13
    assert error["file"].endswith("E316_undeclared_secret.yml")
    assert payload[1]["warnings"] == []


def test_schema_stdout_and_file(tmp_path: Path) -> None:
    code, output = invoke("schema")
    assert code == 0
    assert json.loads(output) == META_SCHEMA
    out = tmp_path / "s.json"
    code, output = invoke("schema", "--out", str(out))
    assert code == 0
    assert "wrote" in output
    assert out.read_text(encoding="utf-8") == (ROOT / "schema" / "crowley-1.schema.json").read_text(
        encoding="utf-8"
    )


def test_printable_falls_back_to_ascii(monkeypatch: object) -> None:
    import sys

    class Cp1252:
        encoding = "cp1252"

    monkeypatch.setattr(sys, "stdout", Cp1252())  # type: ignore[attr-defined]
    assert _printable("a — b → c …") == "a - b -> c ..."
    monkeypatch.setattr(sys, "stdout", type("U", (), {"encoding": "utf-8"})())  # type: ignore[attr-defined]
    assert _printable("a — b") == "a — b"

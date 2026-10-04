import re
from pathlib import Path

import pytest

from crowley.domain.errors import (
    CODES,
    ConfigurationError,
    CrowleyError,
    Diagnostic,
    ErrorClass,
    ExchangeError,
    ExecutionError,
    LimitError,
    Severity,
    SourceLocation,
    TemplateSemanticError,
    ValidationError,
    ValidationReport,
    lookup,
    preview,
)

SPEC = Path(__file__).parents[2] / "docs" / "SPEC.md"


def test_every_code_mentioned_in_the_spec_is_in_the_catalog() -> None:
    mentioned = set(re.findall(r"\b([EW]\d{3})\b", SPEC.read_text(encoding="utf-8")))
    assert mentioned, "SPEC.md should mention error codes"
    assert mentioned <= CODES.keys(), sorted(mentioned - CODES.keys())


def test_every_catalog_code_is_documented_in_the_spec() -> None:
    mentioned = set(re.findall(r"\b([EW]\d{3})\b", SPEC.read_text(encoding="utf-8")))
    assert CODES.keys() <= mentioned, sorted(CODES.keys() - mentioned)


@pytest.mark.parametrize("code", sorted(CODES))
def test_code_prefix_matches_its_family(code: str) -> None:
    assert code.startswith(CODES[code].error_class.value)


@pytest.mark.parametrize(
    ("code", "catchable"),
    [
        ("E401", False),
        ("E405", False),
        ("E501", True),
        ("E504", True),
        ("E601", True),
        ("E602", True),
        ("E603", True),
        ("E604", False),
        ("E605", False),
        ("E606", True),
        ("E607", False),
        ("E701", False),
        ("E703", False),
        ("E801", False),
        ("E803", False),
    ],
)
def test_catchability_follows_spec_16_2(code: str, catchable: bool) -> None:
    assert lookup(code).catchable is catchable


def test_only_execution_and_exchange_codes_are_catchable() -> None:
    catchable_families = {e.error_class for e in CODES.values() if e.catchable}
    assert catchable_families == {ErrorClass.EXECUTION, ErrorClass.EXCHANGE}


def test_warnings_have_warning_severity() -> None:
    assert lookup("W001").severity is Severity.WARNING
    assert lookup("E301").severity is Severity.ERROR


@pytest.mark.parametrize(
    ("code", "cls"),
    [
        ("E305", TemplateSemanticError),
        ("E405", ValidationError),
        ("E501", ExecutionError),
        ("E604", ExchangeError),
        ("E701", LimitError),
        ("E903", ConfigurationError),
    ],
)
def test_from_code_picks_the_family_class(code: str, cls: type[CrowleyError]) -> None:
    error = CrowleyError.from_code(code, "boom")
    assert type(error) is cls
    assert error.code == code


def test_subclass_rejects_codes_of_another_family() -> None:
    with pytest.raises(TypeError):
        ValidationError("E501", "wrong family")


def test_unknown_code_is_rejected() -> None:
    with pytest.raises(KeyError):
        CrowleyError("E999", "nope")


def test_rendering_includes_path_location_and_hint() -> None:
    error = ValidationError(
        "E405",
        "expected integer",
        path="operations.x.output[3].rank",
        location=SourceLocation(file="t.yml", line=12, column=5),
        hint="cast with int()",
    )
    assert (
        str(error)
        == "E405 at operations.x.output[3].rank (t.yml:12:5): expected integer — cast with int()"
    )


def test_rendering_without_context() -> None:
    assert str(ExecutionError("E501", "bad")) == "E501: bad"
    assert str(SourceLocation(file=None, line=1, column=2)) == "<memory>:1:2"


def test_with_context_only_fills_missing_fields() -> None:
    loc = SourceLocation(file="a.yml", line=1, column=1)
    error = ExecutionError("E501", "bad", path="keep").with_context(path="other", location=loc)
    assert error.path == "keep"
    assert error.location == loc


def test_cause_is_chained() -> None:
    cause = ValueError("inner")
    error = ExecutionError("E502", "outer", cause=cause)
    assert error.__cause__ is cause
    assert error.catchable


def test_preview_truncates_and_flattens() -> None:
    assert preview("a\nb") == "'a\\nb'"
    long = preview("x" * 500, limit=20)
    assert len(long) == 20
    assert long.endswith("…")


def test_validation_report_splits_errors_and_warnings() -> None:
    report = ValidationReport(
        diagnostics=(
            Diagnostic(code="W001", message="unreachable", path="operations.a.steps[2]"),
            Diagnostic(code="E305", message="unknown step", path="operations.a.steps[1]"),
            Diagnostic(code="E316", message="undeclared secret"),
        )
    )
    assert not report.ok
    assert [d.code for d in report.errors] == ["E305", "E316"]
    assert [d.code for d in report.warnings] == ["W001"]
    with pytest.raises(TemplateSemanticError) as info:
        report.raise_if_errors()
    assert info.value.code == "E305"
    assert [r.code for r in info.value.related] == ["E316"]


def test_empty_report_is_ok() -> None:
    report = ValidationReport()
    assert report.ok
    report.raise_if_errors()


def test_diagnostic_rendering() -> None:
    loc = SourceLocation(file="t.yml", line=3, column=7)
    warning = Diagnostic(
        code="W003", message="unused", path="functions.f", location=loc, hint="remove it"
    )
    assert str(warning) == "W003 at functions.f (t.yml:3:7): unused — remove it"
    error = Diagnostic.from_error(
        TemplateSemanticError("E307", "duplicate", path="operations.a.steps[1].id")
    )
    assert str(error) == "E307 at operations.a.steps[1].id: duplicate"
    assert error.severity is Severity.ERROR

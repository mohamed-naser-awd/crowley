"""Every diagnostic code has a minimal fixture that produces exactly that one diagnostic."""

from pathlib import Path

import pytest

from crowley import Crowley
from crowley.domain.errors import CODES

FIXTURES = Path(__file__).parents[1] / "fixtures" / "invalid"
ALL = sorted(FIXTURES.glob("*.yml"))
STATIC_ONLY = {"E104"}  # covered by a missing-file test, not a fixture
RUNTIME = {c for c in CODES if c[1] in "456789"}
NOT_YET = {"E325"}  # reserved numbers are not in the catalog


@pytest.fixture(scope="module")
def crowley() -> Crowley:
    return Crowley()


def _expectation(path: Path) -> tuple[str, str | None]:
    first = path.read_text(encoding="utf-8").splitlines()[0]
    _, _, rest = first.partition("# expect: ")
    code, _, where = rest.partition(" ")
    return code, None if where == "-" else where


def test_every_load_time_code_has_a_fixture() -> None:
    covered = {p.name.split("_")[0] for p in ALL}
    load_time = {c for c in CODES if c not in RUNTIME} - STATIC_ONLY
    assert load_time <= covered, sorted(load_time - covered)


@pytest.mark.parametrize("fixture", ALL, ids=[p.stem for p in ALL])
def test_fixture_produces_exactly_its_code(crowley: Crowley, fixture: Path) -> None:
    code, where = _expectation(fixture)
    report = crowley.validate(str(fixture))
    found = [(d.code, d.path) for d in report.diagnostics]
    assert [c for c, _ in found] == [code], [str(d) for d in report.diagnostics]
    diagnostic = report.diagnostics[0]
    if where is None:
        assert diagnostic.path is None
    else:
        assert diagnostic.path is not None
        assert diagnostic.path.startswith(where), diagnostic.path
    assert diagnostic.location is not None
    assert diagnostic.location.file == str(fixture.resolve())
    assert report.ok is code.startswith("W")


def test_missing_file_is_e104(crowley: Crowley, tmp_path: Path) -> None:
    report = crowley.validate(str(tmp_path / "missing.yml"))
    assert [d.code for d in report.errors] == ["E104"]

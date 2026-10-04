from pathlib import Path

import pytest

from crowley import Crowley
from crowley.domain.errors import TemplateSemanticError
from crowley.infrastructure.sources import InMemorySource

ROOT = Path(__file__).parents[2]
EXAMPLE = ROOT / "examples" / "company-directory" / "company-directory.yml"
BROKEN = ROOT / "tests" / "fixtures" / "invalid" / "E305_unknown_step.yml"


def test_load_and_validate() -> None:
    crowley = Crowley()
    template = crowley.load(str(EXAMPLE))
    assert template.id == "examples/company-directory"
    assert crowley.validate(str(EXAMPLE)).ok
    with pytest.raises(TemplateSemanticError) as info:
        crowley.load(str(BROKEN))
    assert info.value.code == "E305"


def test_custom_sources() -> None:
    crowley = Crowley(sources=[InMemorySource({"site": EXAMPLE.read_text(encoding="utf-8")})])
    assert crowley.validate("site").ok


def test_validate_text() -> None:
    report = Crowley().validate_text("crowley: 1\n", name="inline.yml")
    assert not report.ok
    assert report.errors[0].code == "E202"


async def test_sync_loading_works_inside_an_event_loop() -> None:
    crowley = Crowley()
    assert crowley.validate(str(EXAMPLE)).ok
    assert crowley.load(str(EXAMPLE)).id == "examples/company-directory"
    assert crowley.init(
        f"{EXAMPLE}#get_page_info", inputs={"company": "a"}, secrets={"API_TOKEN": "t"}
    )
    assert (await crowley.validate_async(str(EXAMPLE))).ok
    assert (await crowley.load_async(str(EXAMPLE))).id == "examples/company-directory"


def test_public_api_exports() -> None:
    import crowley

    for name in crowley.__all__:
        assert hasattr(crowley, name), name

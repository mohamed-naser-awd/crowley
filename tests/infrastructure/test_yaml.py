import pytest

from crowley.application.ports import Origin
from crowley.domain.common import TemplatePath
from crowley.domain.errors import TemplateParseError, TemplateSchemaError
from crowley.infrastructure.yaml import RuamelTemplateParser

ORIGIN = Origin(name="t.yml", file="t.yml")


def parse(text: str) -> object:
    return RuamelTemplateParser().parse(text, ORIGIN)


def test_plain_data_and_positions() -> None:
    doc = RuamelTemplateParser().parse(
        "crowley: 1\nname: x\nsteps:\n  - use: http.get\n    with: {url: 'https://a.com'}\n"
        "  - 2.5\nflag: yes\nnone: ~\n",
        ORIGIN,
    )
    assert doc.data == {
        "crowley": 1,
        "name": "x",
        "steps": [{"use": "http.get", "with": {"url": "https://a.com"}}, 2.5],
        "flag": "yes",
        "none": None,
    }
    assert type(doc.data["crowley"]) is int  # type: ignore[index]
    pos = doc.positions
    loc = pos.value_at(TemplatePath.of("steps", 0, "use"))
    assert loc is not None
    assert (loc.file, loc.line, loc.column) == ("t.yml", 4, 10)
    key = pos.key_at(TemplatePath.of("steps", 0, "with"))
    assert key is not None
    assert (key.line, key.column) == (5, 5)
    item = pos.value_at(TemplatePath.of("steps", 1))
    assert item is not None
    assert (item.line, item.column) == (6, 5)
    # Unknown paths fall back to the nearest ancestor.
    deep = pos.value_at(TemplatePath.of("steps", 0, "with", "url", "nope"))
    assert deep is not None
    assert deep.line == 5
    assert pos.value_at(TemplatePath.of("missing")) is not None


def test_anchors_and_aliases_are_allowed() -> None:
    doc = RuamelTemplateParser().parse("a: &h {x: 1}\nb: *h\n", ORIGIN)
    assert doc.data == {"a": {"x": 1}, "b": {"x": 1}}


@pytest.mark.parametrize(
    ("text", "code", "line"),
    [
        ("a: [1\n", "E101", 2),
        ("a: 1\n---\nb: 2\n", "E101", None),
        ("a: !custom x\n", "E101", 1),
        ("a: !custom {x: 1}\n", "E101", 1),
        ("base: &b {x: 1}\nother:\n  <<: *b\n  y: 2\n", "E101", 3),
        ("a: 1\nb: 2\na: 3\n", "E102", 3),
    ],
)
def test_parse_errors(text: str, code: str, line: int | None) -> None:
    with pytest.raises(TemplateParseError) as info:
        parse(text)
    assert info.value.code == code
    if line is not None:
        assert info.value.location is not None
        assert info.value.location.line == line


@pytest.mark.parametrize(
    ("text", "path"),
    [
        ("d: 2026-01-01\n", "d"),
        ("t: 2026-01-01T10:00:00Z\n", "t"),
        ("x: [.inf]\n", "x[0]"),
        ("x: .nan\n", "x"),
        ("1: a\n", "<root>"),
    ],
)
def test_non_plain_values_are_e205(text: str, path: str) -> None:
    with pytest.raises(TemplateSchemaError) as info:
        parse(text)
    assert info.value.code == "E205"
    assert info.value.path == path
    assert info.value.hint


def test_float_version_stays_a_float_for_the_meta_schema_to_reject() -> None:
    doc = RuamelTemplateParser().parse("version: 1.0\nmode: 0o17\n", ORIGIN)
    assert doc.data == {"version": 1.0, "mode": 15}

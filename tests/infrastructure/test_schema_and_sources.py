from pathlib import Path

import pytest

from crowley.application.ports import Origin
from crowley.domain.errors import TemplateParseError
from crowley.infrastructure.schema import JsonSchemaValidator
from crowley.infrastructure.sources import FileSource, InMemorySource

V = JsonSchemaValidator()
PERSON = {"type": "object", "required": ["id"], "properties": {"id": {"type": "string"}}}


def test_validation_reports_exact_paths() -> None:
    schema = {
        "type": "object",
        "required": ["a", "b"],
        "properties": {
            "a": {"type": "integer"},
            "c": {"type": "object", "properties": {"d": {"format": "uri"}}},
        },
        "patternProperties": {"^x-": {}},
        "additionalProperties": False,
    }
    violations = V.compile(schema).validate(
        {"a": "no", "zz": 1, "x-ok": 1, "c": {"d": "not a uri"}}
    )
    found = {(str(v.path), v.keyword) for v in violations}
    assert ("a", "type") in found
    assert ("zz", "additionalProperties") in found
    assert ("<root>", "required") in found
    assert ("c.d", "format") in found
    assert not any(str(v.path) == "x-ok" for v in violations)
    missing = next(v for v in violations if v.keyword == "required")
    assert "'b'" in missing.message


def test_template_schema_refs() -> None:
    schemas = {"person": PERSON, "people": {"type": "array", "items": {"$ref": "#/schemas/person"}}}
    compiled = V.compile({"$ref": "#/schemas/people"}, definitions=schemas)
    assert compiled.validate([{"id": "1"}]) == []
    bad = compiled.validate([{"id": 1}])
    assert [str(v.path) for v in bad] == ["[0].id"]
    assert V.check_schema({"$ref": "#/schemas/people"}, definitions=schemas) == []


@pytest.mark.parametrize(
    ("schema", "keyword"),
    [
        ({"$ref": "#/schemas/nope"}, "$ref"),
        ({"items": {"$ref": "https://evil.example/schema.json"}}, "$ref"),
        ({"type": "nonsense"}, "anyOf"),
        ({"minimum": "a"}, "type"),
    ],
)
def test_check_schema_problems(schema: dict[str, object], keyword: str) -> None:
    problems = V.check_schema(schema, definitions={"person": PERSON})
    assert problems
    assert problems[0].keyword == keyword


def test_boolean_schemas_and_local_refs() -> None:
    assert V.check_schema(True) == []
    assert V.compile(False).validate(1)
    local = {"$defs": {"n": {"type": "integer"}}, "$ref": "#/$defs/n"}
    assert V.compile(local).validate(3) == []
    assert V.check_schema(local) == []


async def test_file_source(tmp_path: Path) -> None:
    (tmp_path / "a.yml").write_text("x: 1\n", encoding="utf-8")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "b.yml").write_text("y: 2\n", encoding="utf-8")
    source = FileSource(base_dir=tmp_path)
    assert source.can_resolve("a.yml")
    assert source.can_resolve("./child")
    assert not source.can_resolve("acme/login")
    text = await source.resolve("a.yml")
    assert text.text == "x: 1\n"
    assert text.origin.file == str((tmp_path / "a.yml").resolve())
    sibling = await source.resolve("sub/b.yml")
    relative = await source.resolve("./b.yml", relative_to=sibling.origin)
    assert relative.text == "y: 2\n"
    absolute = await FileSource().resolve(str(tmp_path / "a.yml"))
    assert absolute.text == "x: 1\n"


async def test_file_source_errors(tmp_path: Path) -> None:
    with pytest.raises(TemplateParseError) as info:
        await FileSource(base_dir=tmp_path).resolve("missing.yml")
    assert info.value.code == "E104"
    (tmp_path / "bin.yml").write_bytes(b"\xff\xfe\x00bad")
    with pytest.raises(TemplateParseError) as info:
        await FileSource(base_dir=tmp_path).resolve("bin.yml")
    assert info.value.code == "E104"


async def test_in_memory_source() -> None:
    source = InMemorySource({"t": "a: 1\n"})
    source.add("u", "b: 2\n")
    assert source.can_resolve("u")
    assert (await source.resolve("t")).origin == Origin(name="t")
    with pytest.raises(TemplateParseError):
        await source.resolve("nope")

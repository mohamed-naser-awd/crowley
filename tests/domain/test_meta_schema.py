import json
from pathlib import Path

from jsonschema import Draft202012Validator

from crowley.domain.steps import STEP_KINDS
from crowley.domain.template.meta_schema import META_SCHEMA

PUBLISHED = Path(__file__).parents[2] / "schema" / "crowley-1.schema.json"


def test_meta_schema_is_a_valid_json_schema() -> None:
    Draft202012Validator.check_schema(META_SCHEMA)


def test_published_file_matches_the_meta_schema() -> None:
    published = json.loads(PUBLISHED.read_text(encoding="utf-8"))
    assert published == META_SCHEMA, "run `crowley schema --out schema/crowley-1.schema.json`"


def test_every_step_kind_is_described() -> None:
    step_fields = META_SCHEMA["$defs"]["step"]["properties"]
    assert set(STEP_KINDS) <= set(step_fields)

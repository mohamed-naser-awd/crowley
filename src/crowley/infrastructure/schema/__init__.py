"""JSON Schema 2020-12 provider (jsonschema) with template-scoped ``#/schemas/<name>`` refs."""

import copy
import re
from collections.abc import Mapping
from typing import Any

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError
from jsonschema.exceptions import ValidationError as JsonSchemaError

from crowley.application.ports import SchemaViolation
from crowley.domain.common import TemplatePath

_TEMPLATE_REF = "#/schemas/"
_DEFS_PREFIX = "crowley__"


class _RefProblemError(Exception):
    def __init__(self, message: str, path: TemplatePath) -> None:
        super().__init__(message)
        self.path = path


def _rewrite(node: Any, names: frozenset[str], path: TemplatePath) -> Any:
    """Turn ``#/schemas/x`` into ``#/$defs/crowley__x``; reject external and unknown refs."""
    if isinstance(node, dict):
        out: dict[str, Any] = {}
        for key, value in node.items():
            if key == "$ref" and isinstance(value, str):
                out[key] = _rewrite_ref(value, names, path.key(key))
            else:
                out[key] = _rewrite(value, names, path.key(key))
        return out
    if isinstance(node, list):
        return [_rewrite(v, names, path.index(i)) for i, v in enumerate(node)]
    return node


def _rewrite_ref(ref: str, names: frozenset[str], path: TemplatePath) -> str:
    if ref.startswith(_TEMPLATE_REF):
        name = ref[len(_TEMPLATE_REF) :]
        if name not in names:
            known = ", ".join(sorted(names)) or "none declared"
            raise _RefProblemError(f"unknown shared schema {name!r} (known: {known})", path)
        return f"#/$defs/{_DEFS_PREFIX}{name}"
    if not ref.startswith("#"):
        raise _RefProblemError(
            f"external $ref {ref!r} is not allowed; use '#/schemas/<name>'", path
        )
    return ref


def _prepare(
    schema: Mapping[str, Any] | bool, definitions: Mapping[str, Any] | None, *, hoist: bool = True
) -> Mapping[str, Any] | bool:
    """Rewrite template refs; with ``hoist``, copy the shared schemas into ``$defs``."""
    defs = dict(definitions or {})
    names = frozenset(defs)
    if isinstance(schema, bool):
        return schema
    rewritten: dict[str, Any] = _rewrite(copy.deepcopy(dict(schema)), names, TemplatePath())
    if defs and hoist:
        hoisted = {
            f"{_DEFS_PREFIX}{name}": _rewrite(
                copy.deepcopy(value), names, TemplatePath.of("schemas", name)
            )
            for name, value in defs.items()
        }
        rewritten["$defs"] = {**rewritten.get("$defs", {}), **hoisted}
    return rewritten


def _path(parts: Any) -> TemplatePath:
    return TemplatePath(tuple(p if isinstance(p, int) else str(p) for p in parts))


def _violations(error: JsonSchemaError) -> list[SchemaViolation]:
    """One violation per problem, pointing at the exact key for unknown/missing properties."""
    base = _path(error.absolute_path)
    keyword = str(error.validator)
    instance = error.instance
    if (
        keyword == "additionalProperties"
        and isinstance(instance, dict)
        and isinstance(error.schema, dict)
    ):
        allowed = set(error.schema.get("properties", {}))
        patterns = list(error.schema.get("patternProperties", {}))
        extra = [
            k for k in instance if k not in allowed and not any(re.search(p, k) for p in patterns)
        ]
        if extra:
            return [
                SchemaViolation(path=base.key(k), message=f"unknown key {k!r}", keyword=keyword)
                for k in extra
            ]
    if (
        keyword == "required"
        and isinstance(error.validator_value, list)
        and isinstance(instance, dict)
    ):
        missing = [k for k in error.validator_value if k not in instance]
        return [
            SchemaViolation(path=base, message=f"missing required key {k!r}", keyword=keyword)
            for k in missing
        ]
    return [SchemaViolation(path=base, message=error.message, keyword=keyword)]


class _Compiled:
    def __init__(self, validator: Draft202012Validator) -> None:
        self._validator = validator

    def validate(self, instance: Any) -> list[SchemaViolation]:
        out: list[SchemaViolation] = []
        errors = sorted(
            self._validator.iter_errors(instance), key=lambda e: list(map(str, e.absolute_path))
        )
        for error in errors:
            for violation in _violations(error):
                if violation not in out:
                    out.append(violation)
        return out


class JsonSchemaValidator:
    """``SchemaValidator`` using jsonschema's Draft 2020-12 with format checking."""

    def check_schema(
        self, schema: Mapping[str, Any] | bool, *, definitions: Mapping[str, Any] | None = None
    ) -> list[SchemaViolation]:
        # Shared schemas are checked on their own; here only this schema's structure and refs.
        try:
            prepared = _prepare(schema, definitions, hoist=False)
        except _RefProblemError as exc:
            return [SchemaViolation(path=exc.path, message=str(exc), keyword="$ref")]
        if not isinstance(prepared, bool | Mapping):
            return [
                SchemaViolation(
                    path=TemplatePath(), message="a schema must be an object", keyword="type"
                )
            ]
        try:
            Draft202012Validator.check_schema(prepared)
        except SchemaError as exc:
            return [
                SchemaViolation(
                    path=_path(exc.absolute_path), message=exc.message, keyword=str(exc.validator)
                )
            ]
        return []

    def compile(
        self, schema: Mapping[str, Any] | bool, *, definitions: Mapping[str, Any] | None = None
    ) -> _Compiled:
        prepared = _prepare(schema, definitions)
        return _Compiled(
            Draft202012Validator(prepared, format_checker=Draft202012Validator.FORMAT_CHECKER)
        )


__all__ = ["JsonSchemaValidator"]

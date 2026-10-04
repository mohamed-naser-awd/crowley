"""YAML template parser (ruamel.yaml, YAML 1.2) with source positions for every key and value."""

import math
from datetime import date, datetime
from typing import Any

from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap, CommentedSeq, TaggedScalar
from ruamel.yaml.constructor import DuplicateKeyError
from ruamel.yaml.error import MarkedYAMLError, YAMLError

from crowley.application.ports import Origin, PositionMap, RawDocument
from crowley.domain.common import PathPart, TemplatePath
from crowley.domain.errors import SourceLocation, TemplateParseError, TemplateSchemaError
from crowley.domain.values import Value


def _mark_location(error: MarkedYAMLError, file: str | None) -> SourceLocation | None:
    mark = error.problem_mark or error.context_mark
    if mark is None:
        return None
    return SourceLocation(file=file, line=mark.line + 1, column=mark.column + 1)


class RuamelTemplateParser:
    """``TemplateParser`` that only produces plain JSON data.

    Rejected with ``E101``: syntax errors, several documents, custom tags (``!foo``) and merge
    keys (``<<``). ``E102``: duplicate keys. ``E205``: values that are not plain JSON data, such
    as unquoted dates, ``.inf``/``.nan`` or non-string mapping keys.
    """

    def parse(self, text: str, origin: Origin) -> RawDocument:
        yaml = YAML(typ="rt")
        yaml.allow_duplicate_keys = False
        try:
            loaded = yaml.load(text)
        except DuplicateKeyError as exc:
            raise TemplateParseError(
                "E102",
                f"duplicate key: {exc.problem}",
                location=_mark_location(exc, origin.file),
            ) from None
        except MarkedYAMLError as exc:
            raise TemplateParseError(
                "E101",
                f"YAML syntax error: {exc.problem or exc.context}",
                location=_mark_location(exc, origin.file),
            ) from None
        except YAMLError as exc:
            raise TemplateParseError("E101", f"YAML error: {exc}") from None
        positions = PositionMap(file=origin.file)
        data = _Converter(positions).convert(loaded, (), (1, 1))
        return RawDocument(data=data, positions=positions, origin=origin)


class _Converter:
    def __init__(self, positions: PositionMap) -> None:
        self.positions = positions

    def _location(self, at: tuple[int, int]) -> SourceLocation:
        return SourceLocation(file=self.positions.file, line=at[0], column=at[1])

    def _schema_error(
        self, message: str, path: tuple[PathPart, ...], at: tuple[int, int], hint: str
    ) -> TemplateSchemaError:
        return TemplateSchemaError(
            "E205", message, path=str(TemplatePath(path)), location=self._location(at), hint=hint
        )

    def convert(self, node: Any, path: tuple[PathPart, ...], at: tuple[int, int]) -> Value:
        self.positions.values[path] = at
        tag = getattr(getattr(node, "tag", None), "value", None)
        if isinstance(node, TaggedScalar) or (
            tag is not None and isinstance(node, CommentedMap | CommentedSeq)
        ):
            raise TemplateParseError(
                "E101",
                f"custom YAML tag {tag!r} is not supported",
                path=str(TemplatePath(path)),
                location=self._location(at),
            )
        if isinstance(node, CommentedMap):
            return self._mapping(node, path)
        if isinstance(node, CommentedSeq):
            return [
                self.convert(item, (*path, i), (node.lc.data[i][0] + 1, node.lc.data[i][1] + 1))
                for i, item in enumerate(node)
            ]
        if isinstance(node, datetime | date):
            raise self._schema_error(
                "dates and timestamps must be quoted strings",
                path,
                at,
                hint='quote the value, e.g. "2026-01-01"',
            )
        if isinstance(node, bool) or node is None:
            return node
        if isinstance(node, int):
            return int(node)
        if isinstance(node, float):
            if not math.isfinite(node):
                raise self._schema_error(
                    "infinity and NaN are not valid JSON values",
                    path,
                    at,
                    hint="use a string or a number",
                )
            return float(node)
        if isinstance(node, str):
            return str(node)
        raise self._schema_error(  # pragma: no cover - ruamel produces no other plain types
            f"unsupported YAML value of type {type(node).__name__}",
            path,
            at,
            hint="use plain JSON data",
        )

    def _mapping(self, node: CommentedMap, path: tuple[PathPart, ...]) -> dict[str, Value]:
        if getattr(node, "merge", None):
            line, col = node.lc.line + 1, node.lc.col + 1
            raise TemplateParseError(
                "E101",
                "YAML merge keys ('<<') are not supported",
                path=str(TemplatePath(path)),
                location=self._location((line, col)),
                hint="repeat the values or use defaults/template functions instead",
            )
        out: dict[str, Value] = {}
        for key, value in node.items():
            key_line, key_col, value_line, value_col = node.lc.data[key]
            if not isinstance(key, str):
                raise self._schema_error(
                    f"mapping keys must be strings, got {key!r}",
                    path,
                    (key_line + 1, key_col + 1),
                    hint="quote the key",
                )
            child = (*path, str(key))
            self.positions.keys[child] = (key_line + 1, key_col + 1)
            out[str(key)] = self.convert(value, child, (value_line + 1, value_col + 1))
        return out


__all__ = ["RuamelTemplateParser"]

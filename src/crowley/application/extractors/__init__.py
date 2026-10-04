"""BaseExtractor and the functions generated for every extractor (docs/SPEC.md §13.8, §13.9).

M1 needs the declarative part plus ``check_query`` (for E327). ``parse``/``select``/``read``
and the field-extraction engine arrive in M3.
"""

from typing import Any, ClassVar

from crowley.domain.common import SemVer
from crowley.domain.extractors import ExtractorSpec
from crowley.domain.functions import FROM_PREV, FunctionKind, FunctionSpec


class BaseExtractor:
    """Subclass to add a content format: set the class attributes, implement ``check_query``."""

    name: ClassVar[str]
    version: ClassVar[str]
    media_types: ClassVar[tuple[str, ...]] = ()
    query_languages: ClassVar[tuple[str, ...]] = ()
    default_language: ClassVar[str] = ""
    attributes: ClassVar[tuple[str, ...]] = ("text",)
    default_attribute: ClassVar[str] = "text"
    builtin: ClassVar[bool] = False

    @property
    def spec(self) -> ExtractorSpec:
        return ExtractorSpec(
            name=self.name,
            version=SemVer.parse(self.version),
            media_types=self.media_types,
            query_languages=self.query_languages,
            default_language=self.default_language,
            attributes=self.attributes,
            default_attribute=self.default_attribute,
            builtin=self.builtin,
        )

    def check_query(self, language: str, source: str) -> str | None:
        """An error message if ``source`` is not a valid ``language`` query, else ``None``."""
        return None


_SOURCE: dict[str, Any] = {
    "description": "Content to read. Defaults to prev (a response's body, a string or a node).",
    FROM_PREV: True,
}
_QUERY: dict[str, Any] = {"type": "string", "minLength": 1}
_QUERIES: dict[str, Any] = {"anyOf": [_QUERY, {"type": "array", "items": _QUERY, "minItems": 1}]}


def extractor_function_specs(spec: ExtractorSpec) -> list[FunctionSpec]:
    """``<name>.parse``, ``<name>.select``, ``<name>.select_all`` and ``<name>.extract``."""
    common: dict[str, Any] = {
        "version": spec.version,
        "kind": FunctionKind.PLAIN,
        "pure": True,
        "extractor": spec.name,
        "builtin": spec.builtin,
    }
    select_input = {
        "type": "object",
        "required": ["source", "selector"],
        "properties": {
            "source": _SOURCE,
            "selector": _QUERY,
            "language": {"enum": list(spec.query_languages)},
            "attr": {"type": "string", "minLength": 1},
        },
        "additionalProperties": False,
    }
    return [
        FunctionSpec(
            name=f"{spec.name}.parse",
            description=f"Parse content into a {spec.name} node handle.",
            input={
                "type": "object",
                "required": ["source"],
                "properties": {"source": _SOURCE},
                "additionalProperties": False,
            },
            **common,
        ),
        FunctionSpec(
            name=f"{spec.name}.select",
            description="Value of the first match, or null.",
            input=select_input,
            **common,
        ),
        FunctionSpec(
            name=f"{spec.name}.select_all",
            description="Values of every match.",
            input=select_input,
            output={"type": "array"},
            **common,
        ),
        FunctionSpec(
            name=f"{spec.name}.extract",
            description="Extract fields: one object, or one object per root match.",
            input={
                "type": "object",
                "required": ["source", "fields"],
                "properties": {
                    "source": _SOURCE,
                    "root": _QUERIES,
                    "fields": {"type": "object", "minProperties": 1},
                },
                "additionalProperties": False,
            },
            output={"type": ["object", "array"]},
            **common,
        ),
    ]


__all__ = ["BaseExtractor", "extractor_function_specs"]

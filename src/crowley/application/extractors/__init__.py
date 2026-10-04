"""Extractors: the BaseExtractor contract, the shared field engine and generated functions."""

from crowley.application.extractors.base import BaseExtractor
from crowley.application.extractors.engine import ExtractionEngine, make_query, route
from crowley.application.extractors.functions import (
    extractor_function_specs,
    extractor_functions,
)

__all__ = [
    "BaseExtractor",
    "ExtractionEngine",
    "extractor_function_specs",
    "extractor_functions",
    "make_query",
    "route",
]

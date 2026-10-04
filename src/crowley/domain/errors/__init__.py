"""CrowleyError hierarchy, error codes, source locations and validation reports."""

from crowley.domain.errors.codes import CODES, ErrorClass, ErrorCode, Severity, lookup
from crowley.domain.errors.errors import (
    ConfigurationError,
    ControlError,
    CrowleyError,
    ExchangeError,
    ExecutionError,
    LimitError,
    TemplateParseError,
    TemplateSchemaError,
    TemplateSemanticError,
    ValidationError,
    preview,
)
from crowley.domain.errors.location import SourceLocation
from crowley.domain.errors.report import Diagnostic, ValidationReport

__all__ = [
    "CODES",
    "ConfigurationError",
    "ControlError",
    "CrowleyError",
    "Diagnostic",
    "ErrorClass",
    "ErrorCode",
    "ExchangeError",
    "ExecutionError",
    "LimitError",
    "Severity",
    "SourceLocation",
    "TemplateParseError",
    "TemplateSchemaError",
    "TemplateSemanticError",
    "ValidationError",
    "ValidationReport",
    "lookup",
    "preview",
]

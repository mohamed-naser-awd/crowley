"""Crowley: declarative, template-driven web scraping SDK.

The public API is re-exported from here (see docs/SPEC.md §20).
"""

__version__ = "0.0.1"

from crowley.application.adapters import BaseAdapter
from crowley.application.extractors import BaseExtractor
from crowley.application.registry import Plugin, RegisteredFunction, Registry, function
from crowley.application.runtime import Body, FunctionContext
from crowley.domain.errors import CrowleyError, Diagnostic, ValidationReport
from crowley.domain.functions import FunctionKind, FunctionSpec, Requirement
from crowley.domain.template import Template
from crowley.interface.sdk import Crowley

__all__ = [
    "BaseAdapter",
    "BaseExtractor",
    "Body",
    "Crowley",
    "CrowleyError",
    "Diagnostic",
    "FunctionContext",
    "FunctionKind",
    "FunctionSpec",
    "Plugin",
    "RegisteredFunction",
    "Registry",
    "Requirement",
    "Template",
    "ValidationReport",
    "__version__",
    "function",
]

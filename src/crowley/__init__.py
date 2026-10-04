"""Crowley: declarative, template-driven web scraping SDK.

The public API is re-exported from here (see docs/SPEC.md §20).
"""

__version__ = "0.1.0"

from crowley.application.adapters import BaseAdapter
from crowley.application.events import Notifier, NotifierHandle, NotifierRegistry
from crowley.application.extractors import BaseExtractor
from crowley.application.registry import Plugin, RegisteredFunction, Registry, function
from crowley.application.runtime import Body, FunctionContext
from crowley.application.use_cases import Process, RunResult
from crowley.domain.errors import CrowleyError, Diagnostic, ValidationReport
from crowley.domain.events import Abort, Event, Replace, Retry, Skip
from crowley.domain.functions import FunctionKind, FunctionSpec, Requirement
from crowley.domain.template import Limits, Template
from crowley.infrastructure.secrets import DictSecrets, EnvSecrets
from crowley.interface.sdk import Crowley

__all__ = [
    "Abort",
    "BaseAdapter",
    "BaseExtractor",
    "Body",
    "Crowley",
    "CrowleyError",
    "Diagnostic",
    "DictSecrets",
    "EnvSecrets",
    "Event",
    "FunctionContext",
    "FunctionKind",
    "FunctionSpec",
    "Limits",
    "Notifier",
    "NotifierHandle",
    "NotifierRegistry",
    "Plugin",
    "Process",
    "RegisteredFunction",
    "Registry",
    "Replace",
    "Requirement",
    "Retry",
    "RunResult",
    "Skip",
    "Template",
    "ValidationReport",
    "__version__",
    "function",
]

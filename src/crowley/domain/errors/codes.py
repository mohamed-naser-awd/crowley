"""Catalog of every Crowley error and warning code (docs/SPEC.md §15, §16)."""

from dataclasses import dataclass
from enum import Enum


class ErrorClass(Enum):
    """Error families, one per code range."""

    TEMPLATE_PARSE = "E1"
    TEMPLATE_SCHEMA = "E2"
    TEMPLATE_SEMANTIC = "E3"
    VALIDATION = "E4"
    EXECUTION = "E5"
    EXCHANGE = "E6"
    LIMIT = "E7"
    CONTROL = "E8"
    CONFIGURATION = "E9"
    WARNING = "W0"


class Severity(Enum):
    ERROR = "error"
    WARNING = "warning"


@dataclass(frozen=True, slots=True, kw_only=True)
class ErrorCode:
    code: str
    title: str
    error_class: ErrorClass
    catchable: bool = False

    @property
    def severity(self) -> Severity:
        return Severity.WARNING if self.error_class is ErrorClass.WARNING else Severity.ERROR


def _entries() -> dict[str, ErrorCode]:
    c = ErrorClass
    rows: list[tuple[str, str, ErrorClass, bool]] = [
        # E1xx: parse / load
        ("E101", "YAML syntax error or unsupported YAML feature", c.TEMPLATE_PARSE, False),
        ("E102", "Duplicate key", c.TEMPLATE_PARSE, False),
        ("E103", "Unsupported spec version", c.TEMPLATE_PARSE, False),
        ("E104", "Template not found", c.TEMPLATE_PARSE, False),
        # E2xx: meta-schema
        ("E201", "Unknown key", c.TEMPLATE_SCHEMA, False),
        ("E202", "Missing required key", c.TEMPLATE_SCHEMA, False),
        ("E203", "Defaults for an unknown namespace", c.TEMPLATE_SCHEMA, False),
        ("E204", "Template has no operations", c.TEMPLATE_SCHEMA, False),
        ("E205", "Wrong type, pattern or format", c.TEMPLATE_SCHEMA, False),
        # E3xx: static semantic checks
        ("E301", "Unknown function", c.TEMPLATE_SEMANTIC, False),
        ("E302", "Requirement missing or incompatible", c.TEMPLATE_SEMANTIC, False),
        (
            "E303",
            "Literal argument violates the function's input schema",
            c.TEMPLATE_SEMANTIC,
            False,
        ),
        ("E304", "do: block does not match the function kind", c.TEMPLATE_SEMANTIC, False),
        ("E305", "Unknown or invisible step reference", c.TEMPLATE_SEMANTIC, False),
        ("E306", "Variable is never assigned", c.TEMPLATE_SEMANTIC, False),
        ("E307", "Duplicate step id", c.TEMPLATE_SEMANTIC, False),
        ("E308", "break/continue outside a loop or block body", c.TEMPLATE_SEMANTIC, False),
        ("E309", "return outside a block owner", c.TEMPLATE_SEMANTIC, False),
        ("E310", "Both emit and output.value used", c.TEMPLATE_SEMANTIC, False),
        ("E311", "emit used but output.schema is not an array schema", c.TEMPLATE_SEMANTIC, False),
        ("E313", "Unknown expression helper", c.TEMPLATE_SEMANTIC, False),
        ("E314", "Wrong number of helper arguments", c.TEMPLATE_SEMANTIC, False),
        ("E315", "Undeclared or inaccessible input", c.TEMPLATE_SEMANTIC, False),
        ("E316", "Undeclared secret", c.TEMPLATE_SEMANTIC, False),
        ("E317", "Recursion or composition cycle", c.TEMPLATE_SEMANTIC, False),
        ("E318", "Literal host not permitted", c.TEMPLATE_SEMANTIC, False),
        ("E319", "Invalid JSON Schema", c.TEMPLATE_SEMANTIC, False),
        ("E320", "Outer variable assigned in a concurrent loop", c.TEMPLATE_SEMANTIC, False),
        ("E321", "Expression syntax error", c.TEMPLATE_SEMANTIC, False),
        ("E322", "Lambda outside a helper argument", c.TEMPLATE_SEMANTIC, False),
        ("E323", "emit inside a template function", c.TEMPLATE_SEMANTIC, False),
        ("E324", "Invalid operation reference", c.TEMPLATE_SEMANTIC, False),
        ("E326", "Unsupported query language or attribute", c.TEMPLATE_SEMANTIC, False),
        ("E327", "Extractor query does not compile", c.TEMPLATE_SEMANTIC, False),
        ("E328", "Unknown expression root or binding", c.TEMPLATE_SEMANTIC, False),
        ("E329", "on_error not allowed on this step kind", c.TEMPLATE_SEMANTIC, False),
        # E4xx: runtime validation (never catchable)
        ("E401", "Invalid inputs", c.VALIDATION, False),
        ("E402", "Missing or invalid secrets", c.VALIDATION, False),
        ("E403", "Invalid function arguments", c.VALIDATION, False),
        ("E404", "Invalid emitted item", c.VALIDATION, False),
        ("E405", "Invalid output", c.VALIDATION, False),
        ("E406", "Assertion failed", c.VALIDATION, False),
        ("E407", "Invalid function result", c.VALIDATION, False),
        # E5xx: execution (catchable)
        ("E501", "Expression evaluation error", c.EXECUTION, True),
        ("E502", "Function raised an error", c.EXECUTION, True),
        ("E503", "fail step", c.EXECUTION, True),
        ("E504", "Step timeout", c.EXECUTION, True),
        # E6xx: exchanges (catchable except E604, E605, E607)
        ("E601", "Unexpected status or failure response", c.EXCHANGE, True),
        ("E602", "Connection or transport error", c.EXCHANGE, True),
        ("E603", "Exchange timeout", c.EXCHANGE, True),
        ("E604", "Target not permitted", c.EXCHANGE, False),
        ("E605", "No cassette match in replay", c.EXCHANGE, False),
        ("E606", "Response too large", c.EXCHANGE, True),
        ("E607", "Adapter not available", c.EXCHANGE, False),
        # E7xx: limits (never catchable)
        ("E701", "Limit exceeded", c.LIMIT, False),
        ("E702", "while hit max_iterations", c.LIMIT, False),
        ("E703", "Expression budget or regex timeout exceeded", c.LIMIT, False),
        ("E705", "Pagination loop detected", c.LIMIT, False),
        # E8xx: control (never catchable)
        ("E801", "Aborted by notifier", c.CONTROL, False),
        ("E802", "Notifier raised an error", c.CONTROL, False),
        ("E803", "Cancelled", c.CONTROL, False),
        # E9xx: configuration
        ("E901", "Registry conflict", c.CONFIGURATION, False),
        ("E902", "Invalid SDK configuration", c.CONFIGURATION, False),
        ("E903", "Invalid function spec or handler signature", c.CONFIGURATION, False),
        ("E904", "Operation not specified or unknown", c.CONFIGURATION, False),
        ("E905", "Process already started", c.CONFIGURATION, False),
        ("E906", "Adapter or extractor conflict or incompatible replace", c.CONFIGURATION, False),
        # Warnings
        ("W001", "Unreachable steps", c.WARNING, False),
        ("W002", "Unused result of a pure function", c.WARNING, False),
        ("W003", "Unused template function", c.WARNING, False),
    ]
    return {
        code: ErrorCode(code=code, title=title, error_class=cls, catchable=catchable)
        for code, title, cls, catchable in rows
    }


CODES: dict[str, ErrorCode] = _entries()


def lookup(code: str) -> ErrorCode:
    """Return the catalog entry for ``code``; raises ``KeyError`` for unknown codes."""
    return CODES[code]

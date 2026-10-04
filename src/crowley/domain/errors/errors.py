"""The CrowleyError hierarchy (docs/SPEC.md §16)."""

from typing import Any, ClassVar, Self

from crowley.domain.errors.codes import ErrorClass, ErrorCode, lookup
from crowley.domain.errors.location import SourceLocation

_VALUE_PREVIEW_LIMIT = 200


def preview(value: Any, limit: int = _VALUE_PREVIEW_LIMIT) -> str:
    """Short, single-line ``repr`` of an offending value for messages."""
    text = repr(value).replace("\n", "\\n")
    return text if len(text) <= limit else text[: limit - 1] + "…"


class CrowleyError(Exception):
    """Base class for every error Crowley raises."""

    error_class: ClassVar[ErrorClass | None] = None

    def __init__(
        self,
        code: str,
        message: str,
        *,
        path: str | None = None,
        location: SourceLocation | None = None,
        value: Any = None,
        hint: str | None = None,
        cause: BaseException | None = None,
        related: tuple["CrowleyError", ...] = (),
        user_code: str | None = None,
    ) -> None:
        entry = lookup(code)
        if self.error_class is not None and entry.error_class is not self.error_class:
            raise TypeError(f"{code} does not belong to {type(self).__name__}")
        super().__init__(message)
        self.code = code
        self.message = message
        self.path = path
        self.location = location
        self.value = value
        self.hint = hint
        self.cause = cause
        self.related = related
        self.user_code = user_code
        """Template-defined code of a ``fail`` step (``error.user_code``)."""
        if cause is not None:
            self.__cause__ = cause

    @property
    def entry(self) -> ErrorCode:
        return lookup(self.code)

    @property
    def catchable(self) -> bool:
        """Whether a step's ``on_error`` policy may handle this error."""
        return self.entry.catchable

    def as_value(self) -> dict[str, Any]:
        """The ``error`` object visible to ``on_error`` expressions (SPEC §16.3)."""
        return {
            "code": self.code,
            "message": self.message,
            "path": self.path,
            "user_code": self.user_code,
        }

    @classmethod
    def from_code(cls, code: str, message: str, **kwargs: Any) -> "CrowleyError":
        """Build an instance of the subclass that owns ``code``."""
        return _CLASS_BY_FAMILY[lookup(code).error_class](code, message, **kwargs)

    def with_context(
        self, *, path: str | None = None, location: SourceLocation | None = None
    ) -> Self:
        """Fill in path/location if they are not set yet; returns ``self``."""
        if self.path is None:
            self.path = path
        if self.location is None:
            self.location = location
        return self

    def __str__(self) -> str:
        head = self.code
        if self.path:
            head += f" at {self.path}"
        if self.location:
            head += f" ({self.location})"
        text = f"{head}: {self.message}"
        if self.hint:
            text += f" — {self.hint}"
        return text


class TemplateParseError(CrowleyError):
    error_class = ErrorClass.TEMPLATE_PARSE


class TemplateSchemaError(CrowleyError):
    error_class = ErrorClass.TEMPLATE_SCHEMA


class TemplateSemanticError(CrowleyError):
    error_class = ErrorClass.TEMPLATE_SEMANTIC


class ValidationError(CrowleyError):
    error_class = ErrorClass.VALIDATION


class ExecutionError(CrowleyError):
    error_class = ErrorClass.EXECUTION


class ExchangeError(CrowleyError):
    error_class = ErrorClass.EXCHANGE


class LimitError(CrowleyError):
    error_class = ErrorClass.LIMIT


class ControlError(CrowleyError):
    error_class = ErrorClass.CONTROL


class ConfigurationError(CrowleyError):
    error_class = ErrorClass.CONFIGURATION


_CLASS_BY_FAMILY: dict[ErrorClass, type[CrowleyError]] = {
    ErrorClass.TEMPLATE_PARSE: TemplateParseError,
    ErrorClass.TEMPLATE_SCHEMA: TemplateSchemaError,
    ErrorClass.TEMPLATE_SEMANTIC: TemplateSemanticError,
    ErrorClass.VALIDATION: ValidationError,
    ErrorClass.EXECUTION: ExecutionError,
    ErrorClass.EXCHANGE: ExchangeError,
    ErrorClass.LIMIT: LimitError,
    ErrorClass.CONTROL: ControlError,
    ErrorClass.CONFIGURATION: ConfigurationError,
}

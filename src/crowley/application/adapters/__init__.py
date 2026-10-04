"""BaseAdapter: the public base class for I/O plugins (docs/SPEC.md §13.1).

M1 needs only the declarative part (name, schemas, contributed functions). The runtime
methods (``open``/``send``/``close``) and the shared ``AdapterService`` pipeline arrive in M3.
"""

from collections.abc import Mapping
from typing import Any, ClassVar

from crowley.domain.adapters import AdapterSpec, TargetKind
from crowley.domain.common import SemVer
from crowley.domain.functions import FunctionSpec


class BaseAdapter:
    """Subclass to add a protocol: set the class attributes, list functions in ``functions()``."""

    name: ClassVar[str]
    version: ClassVar[str]
    schemes: ClassVar[tuple[str, ...]] = ()
    target_kind: ClassVar[TargetKind] = TargetKind.NETWORK
    config_schema: ClassVar[Mapping[str, Any]] = {"type": "object"}
    defaults_schema: ClassVar[Mapping[str, Any]] = {"type": "object"}
    request_schema: ClassVar[Mapping[str, Any]] = {"type": "object"}
    response_schema: ClassVar[Mapping[str, Any]] = {"type": "object"}
    builtin: ClassVar[bool] = False

    @property
    def spec(self) -> AdapterSpec:
        return AdapterSpec(
            name=self.name,
            version=SemVer.parse(self.version),
            schemes=self.schemes,
            target_kind=self.target_kind,
            config_schema=self.config_schema,
            defaults_schema=self.defaults_schema,
            request_schema=self.request_schema,
            response_schema=self.response_schema,
            builtin=self.builtin,
        )

    def functions(self) -> list[FunctionSpec]:
        """Functions this adapter contributes, all in its own namespace (e.g. ``http.get``)."""
        return []


__all__ = ["BaseAdapter"]

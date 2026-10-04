"""Use cases: LoadTemplate, ValidateTemplate (M1), and the runtime ones in M2+."""

from crowley.application.use_cases.load import LoadResult, LoadTemplate
from crowley.application.use_cases.validate import ValidateTemplate

__all__ = ["LoadResult", "LoadTemplate", "ValidateTemplate"]

"""Built-in ``http`` adapter (docs/SPEC.md §13.7)."""

from crowley.adapters.http.adapter import DEFAULT_USER_AGENT, HttpAdapter, HttpSession

__all__ = ["DEFAULT_USER_AGENT", "HttpAdapter", "HttpSession"]

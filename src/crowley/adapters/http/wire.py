"""Translating Crowley http requests to httpx and httpx responses back (SPEC §13.7)."""

import base64
import json
from collections.abc import Mapping
from email.utils import parsedate_to_datetime
from typing import Any

import httpx

from crowley.domain.adapters import media_type_essence
from crowley.domain.errors import ExchangeError

_TEXT_TYPES = ("text/", "application/xml", "application/javascript", "application/xhtml+xml")


def lower_headers(headers: Mapping[str, Any] | None) -> dict[str, Any]:
    """Header names are case-insensitive: normalise them to lower case."""
    return {str(k).lower(): v for k, v in (headers or {}).items()}


def query_params(
    query: Mapping[str, Any] | None,
) -> list[tuple[str, str | int | float | bool | None]]:
    """``{a: 1, b: [x, y], c: null}`` → ``[("a", "1"), ("b", "x"), ("b", "y")]``."""
    params: list[tuple[str, str | int | float | bool | None]] = []
    for key, value in (query or {}).items():
        values = value if isinstance(value, list) else [value]
        params.extend((str(key), _scalar(v)) for v in values if v is not None)
    return params


def _scalar(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, dict | list):
        return json.dumps(value, separators=(",", ":"))
    return str(value)


def build_request(
    client: httpx.AsyncClient,
    request: Mapping[str, Any],
    *,
    cookies: httpx.Cookies,
    timeout: float,
) -> httpx.Request:
    """An ``httpx.Request`` for a merged, validated Crowley request."""
    url = httpx.URL(str(request["url"]))
    params = query_params(request.get("query"))
    if params:
        url = url.copy_merge_params(params)
    headers = {k: v for k, v in lower_headers(request.get("headers")).items() if v is not None}
    auth = request.get("auth")
    if isinstance(auth, Mapping):
        if "bearer" in auth:
            headers["authorization"] = f"Bearer {auth['bearer']}"
        else:
            basic = auth["basic"]
            token = base64.b64encode(f"{basic['username']}:{basic['password']}".encode())
            headers["authorization"] = f"Basic {token.decode('ascii')}"
    kwargs: dict[str, Any] = {}
    body = request.get("body")
    if isinstance(body, Mapping):
        kind, value = next(iter(body.items()))
        if kind == "json":
            kwargs["json"] = value
        elif kind == "form":
            kwargs["data"] = {k: _scalar(v) for k, v in value.items() if v is not None}
        elif kind == "raw":
            kwargs["content"] = str(value).encode("utf-8")
            headers.setdefault("content-type", "text/plain; charset=utf-8")
        elif kind == "bytes":
            kwargs["content"] = base64.b64decode(value)
            headers.setdefault("content-type", "application/octet-stream")
        else:  # multipart
            kwargs["files"] = [_part(part) for part in value]
    built = client.build_request(
        str(request.get("method", "GET")),
        url,
        headers=headers,
        timeout=timeout,
        **kwargs,
    )
    if "cookie" not in headers:
        cookies.set_cookie_header(built)
    return built


def _part(part: Mapping[str, Any]) -> tuple[str, Any]:
    name = str(part["name"])
    if "content_base64" in part:
        content: bytes | str = base64.b64decode(part["content_base64"])
    else:
        content = _scalar(part.get("value", ""))
    filename = part.get("filename")
    if filename is None and "content_type" not in part:
        return name, (None, content)
    return name, (filename, content, part.get("content_type", "application/octet-stream"))


def response_value(
    response: httpx.Response,
    content: bytes,
    *,
    request: httpx.Request,
    redirects: list[str],
    response_type: str,
    encoding: str | None,
    elapsed_ms: float,
) -> dict[str, Any]:
    """The http response object templates see (SPEC §13.7)."""
    content_type = response.headers.get("content-type")
    media_type = media_type_essence(content_type)
    return {
        "status": response.status_code,
        "ok": response.status_code < 400,
        "url": str(response.url),
        "headers": _joined(response.headers),
        "media_type": media_type,
        "cookies": {cookie.name: cookie.value for cookie in response.cookies.jar},
        "body": _body(response, content, media_type, response_type, encoding),
        "elapsed_ms": round(elapsed_ms, 3),
        "redirects": redirects,
        "request": {
            "method": request.method,
            "url": str(request.url),
            "headers": _joined(request.headers),
        },
    }


def _joined(headers: httpx.Headers) -> dict[str, str]:
    joined: dict[str, str] = {}
    for key, value in headers.multi_items():
        name = key.lower()
        joined[name] = f"{joined[name]}, {value}" if name in joined else value
    return joined


def _body(
    response: httpx.Response,
    content: bytes,
    media_type: str | None,
    response_type: str,
    encoding: str | None,
) -> Any:
    if response.request.method == "HEAD":
        return ""
    if response_type == "bytes":
        return content
    text = content.decode(encoding or response.charset_encoding or "utf-8", errors="replace")
    if response_type == "text":
        return text
    is_json = media_type is not None and (
        media_type == "application/json" or media_type.endswith("+json")
    )
    if response_type == "json" or is_json:
        if not text.strip():
            return None
        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:
            if response_type == "json":
                raise ExchangeError(
                    "E602", f"response from {response.url} is not valid JSON: {exc}"
                ) from exc
            return text
    if media_type is None or media_type.startswith(_TEXT_TYPES) or media_type.endswith("+xml"):
        return text
    return content


def status_matches(status: int, expected: list[Any]) -> bool:
    """``expect_status`` entries are codes (``404``) or classes (``"2xx"``)."""
    for entry in expected:
        if isinstance(entry, int) and not isinstance(entry, bool) and entry == status:
            return True
        if (
            isinstance(entry, str)
            and len(entry) == 3
            and entry.endswith("xx")
            and entry[0] == str(status)[0]
        ):
            return True
    return False


def retry_after_seconds(value: str | None) -> float | None:
    """``Retry-After`` as seconds: an integer, or an HTTP date relative to now."""
    if not value:
        return None
    value = value.strip()
    if value.isdigit():
        return float(value)
    try:
        moment = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    from datetime import UTC, datetime

    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return max(0.0, (moment - datetime.now(UTC)).total_seconds())

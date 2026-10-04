"""The SPEC §2.3 example, end to end, against an in-memory directory API."""

from pathlib import Path
from typing import Any

import httpx
import pytest

from crowley import Crowley
from crowley.adapters.http import HttpAdapter
from crowley.domain.errors import ExchangeError
from crowley.infrastructure.network import StaticHostResolver

EXAMPLE = Path(__file__).parents[2] / "examples" / "company-directory" / "company-directory.yml"


class DirectoryApi:
    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.headers.get("authorization") != "Bearer t0ken":
            return httpx.Response(401, json={"error": "unauthorized"})
        path = request.url.path
        if path == "/api/companies/acme":
            return httpx.Response(
                200, json={"name": "Acme", "follower_count": 12, "website": "https://acme.test"}
            )
        if path == "/api/companies/acme/people":
            if request.url.params.get("cursor") == "c2":
                return httpx.Response(
                    200, json={"people": [{"id": 2, "full_name": "Bob"}], "next_cursor": None}
                )
            return httpx.Response(
                200,
                json={
                    "people": [{"id": 1, "full_name": "  Ada  ", "headline": "CTO"}],
                    "next_cursor": "c2",
                },
            )
        return httpx.Response(404, json={})


def crowley(api: DirectoryApi) -> Crowley:
    return Crowley(
        adapters=[HttpAdapter(transport=httpx.MockTransport(api))],
        resolver=StaticHostResolver({"directory.example.com": ["93.184.216.34"]}),
        secrets={"API_TOKEN": "t0ken"},
    )


def test_get_page_info() -> None:
    api = DirectoryApi()
    result = crowley(api).run_sync(str(EXAMPLE), "get_page_info", inputs={"company": "acme"})
    assert result.output == {"name": "Acme", "followers": 12, "website": "https://acme.test"}
    sent = api.requests[0]
    assert sent.headers["accept"] == "application/json"
    assert str(sent.url) == "https://directory.example.com/api/companies/acme"
    assert result.stats["requests"] == 1


def test_get_page_people_paginates_and_emits() -> None:
    api = DirectoryApi()
    cw = crowley(api)
    pages: list[Any] = []
    cw.add_notifier("page.before", lambda e: pages.append(e.page["cursor"]), observe=True)
    result = cw.run_sync(str(EXAMPLE), "get_page_people", inputs={"company": "acme"})
    assert result.output == [
        {"id": "1", "name": "Ada", "title": "CTO"},
        {"id": "2", "name": "Bob", "title": None},
    ]
    assert pages == [None, "c2"]
    assert [str(r.url) for r in api.requests] == [
        "https://directory.example.com/api/companies/acme/people",
        "https://directory.example.com/api/companies/acme/people?cursor=c2",
    ]


def test_secrets_never_leak_into_errors() -> None:
    api = DirectoryApi()
    cw = Crowley(
        adapters=[HttpAdapter(transport=httpx.MockTransport(api))],
        resolver=StaticHostResolver({"directory.example.com": ["93.184.216.34"]}),
        secrets={"API_TOKEN": "wrong-token"},
    )
    with pytest.raises(ExchangeError) as info:  # 401 from the API
        cw.run_sync(str(EXAMPLE), "get_page_info", inputs={"company": "acme"})
    assert info.value.code == "E601"
    assert "wrong-token" not in str(info.value)
    assert "wrong-token" not in repr(info.value.value)

"""Domain exchange model and the pipeline's guards."""

import pytest

from crowley.application.adapters.guards import RateLimiter, is_blocked_address
from crowley.domain.adapters import (
    ExchangeResult,
    RetryPolicy,
    Target,
    deep_merge,
    media_type_essence,
    media_type_rank,
)
from crowley.domain.common import Duration, Rate


@pytest.mark.parametrize(
    ("uri", "scheme", "host", "port"),
    [
        ("https://API.example.com/x?y=1", "https", "api.example.com", None),
        ("https://api.example.com:443/", "https", "api.example.com", None),
        ("http://api.example.com:8080/", "http", "api.example.com", 8080),
        ("ws://[::1]:9000/socket", "ws", "::1", 9000),
        ("custom://host/", "custom", "host", None),
    ],
)
def test_target_parse(uri: str, scheme: str, host: str, port: int | None) -> None:
    target = Target.parse(uri)
    assert (target.scheme, target.host, target.port) == (scheme, host, port)


@pytest.mark.parametrize("uri", ["/relative", "https://", "mailto:x", "http://h:99999/"])
def test_target_parse_rejects(uri: str) -> None:
    with pytest.raises(ValueError, match=r"URI|port"):
        Target.parse(uri)


def test_deep_merge() -> None:
    base = {"headers": {"a": "1", "b": "1"}, "timeout": 5, "list": [1]}
    merged = deep_merge(base, {"headers": {"b": "2"}, "list": [2]}, {"timeout": 9})
    assert merged == {"headers": {"a": "1", "b": "2"}, "timeout": 9, "list": [2]}
    assert base["headers"] == {"a": "1", "b": "1"}  # inputs untouched
    merged["list"].append(3)
    assert deep_merge({"x": {"y": 1}}, {"x": 5}) == {"x": 5}


def test_media_types() -> None:
    assert media_type_essence("Text/HTML; charset=utf-8") == "text/html"
    assert media_type_essence("") is None
    assert media_type_essence(None) is None
    assert media_type_rank("text/html", "text/html") == 3
    assert media_type_rank("*+json", "application/ld+json") == 2
    assert media_type_rank("*+json", "application/json") == 0
    assert media_type_rank("text/*", "text/csv") == 2
    assert media_type_rank("*", "image/png") == 1
    assert media_type_rank("application/json", "text/html") == 0


def test_retry_policy_and_result_meta() -> None:
    exp = RetryPolicy(times=4, delay=Duration(1.0), max_delay=Duration(3.0))
    assert [exp.delay_for(n) for n in (1, 2, 3)] == [1.0, 2.0, 3.0]
    assert RetryPolicy(times=1, backoff="fixed", delay=Duration(0.5)).delay_for(3) == 0.5
    result = ExchangeResult(response=None, meta={"bytes_in": "x", "hops": ["a", 1]})
    assert result.bytes_in == 0
    assert result.hops == ["a"]
    assert ExchangeResult(response=None, meta={"hops": "nope"}).hops == []


@pytest.mark.parametrize(
    ("address", "blocked"),
    [
        ("93.184.216.34", False),
        ("2606:2800:220:1:248:1893:25c8:1946", False),
        ("127.0.0.1", True),
        ("10.1.2.3", True),
        ("192.168.0.1", True),
        ("169.254.169.254", True),
        ("224.0.0.1", True),
        ("0.0.0.0", True),
        ("::1", True),
        ("fe80::1", True),
        ("::ffff:10.0.0.1", True),
        ("garbage", True),
    ],
)
def test_blocked_addresses(address: str, blocked: bool) -> None:
    assert is_blocked_address(address) is blocked


async def test_rate_limiter_token_bucket() -> None:
    now = [0.0]
    waits: list[float] = []

    async def sleep(seconds: float) -> None:
        waits.append(seconds)
        now[0] += seconds

    limiter = RateLimiter(Rate(2, 1.0), sleep=sleep, clock=lambda: now[0])
    for _ in range(4):
        await limiter.acquire("a.example")
    await limiter.acquire("b.example")  # separate bucket
    assert waits == [0.5, 0.5]
    await RateLimiter(None, sleep=sleep).acquire("x")
    assert len(waits) == 2


async def test_system_resolver_resolves_localhost() -> None:
    from crowley.infrastructure.network import SystemHostResolver

    addresses = await SystemHostResolver().resolve("localhost")
    assert {"127.0.0.1", "::1"} & set(addresses)

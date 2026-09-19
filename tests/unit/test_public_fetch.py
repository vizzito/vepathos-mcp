"""Downloads of model-supplied URLs never reach the server's own network."""

from __future__ import annotations

import httpx
import pytest

from vepathos_mcp.clients.public_fetch import (
    PublicFetchError,
    check_public_https_url,
    fetch_public_https,
    is_public_address,
)

PUBLIC_V4 = "93.184.216.34"


def resolver_for(*addresses: str):  # type: ignore[no-untyped-def]
    async def resolve(host: str) -> list[str]:
        return list(addresses)

    return resolve


@pytest.mark.parametrize(
    "address",
    [
        "127.0.0.1",
        "10.0.0.5",
        "172.16.3.4",
        "192.168.1.10",
        "169.254.169.254",  # cloud metadata
        "100.64.0.1",  # carrier-grade NAT
        "0.0.0.0",  # noqa: S104 - the address under test, nothing binds to it
        "224.0.0.1",
        "::1",
        "fd00::1",
        "fe80::1",
        "::ffff:10.0.0.1",
        "::ffff:127.0.0.1",
        "2002:0a00:0001::1",  # 6to4 wrapping 10.0.0.1
        "not-an-ip",
    ],
)
def test_internal_addresses_are_not_public(address: str) -> None:
    assert not is_public_address(address)


@pytest.mark.parametrize("address", [PUBLIC_V4, "2606:4700:4700::1111", "::ffff:93.184.216.34"])
def test_global_addresses_are_public(address: str) -> None:
    assert is_public_address(address)


@pytest.mark.parametrize(
    ("url", "reason"),
    [
        ("http://files.example.com/a.xlsx", "invalid_url"),
        ("ftp://files.example.com/a.xlsx", "invalid_url"),
        ("https://user:pass@files.example.com/a.xlsx", "invalid_url"),
        ("https:///a.xlsx", "invalid_url"),
        ("https://files.example.com:8443/a.xlsx", "blocked_host"),
        ("https://127.0.0.1/a.xlsx", "blocked_host"),
        ("https://169.254.169.254/latest/meta-data", "blocked_host"),
        ("https://[::1]/a.xlsx", "blocked_host"),
        ("https://[::ffff:10.0.0.1]/a.xlsx", "blocked_host"),
    ],
)
async def test_rejects_urls_that_are_not_public_https(url: str, reason: str) -> None:
    with pytest.raises(PublicFetchError) as exc:
        await check_public_https_url(url, resolver_for(PUBLIC_V4))
    assert exc.value.reason == reason


async def test_rejects_a_hostname_that_resolves_inside() -> None:
    with pytest.raises(PublicFetchError) as exc:
        await check_public_https_url("https://api-doc/internal", resolver_for("172.18.0.5"))
    assert exc.value.reason == "blocked_host"


async def test_rejects_a_hostname_with_any_internal_answer() -> None:
    with pytest.raises(PublicFetchError) as exc:
        await check_public_https_url("https://rebind.example.com/a", resolver_for(PUBLIC_V4, "127.0.0.1"))
    assert exc.value.reason == "blocked_host"


async def test_connects_to_the_checked_address_under_the_hostname() -> None:
    checked = await check_public_https_url(
        "https://files.example.com/f/a.xlsx?sig=1", resolver_for(PUBLIC_V4)
    )
    assert checked.host == "files.example.com"
    assert checked.target == f"https://{PUBLIC_V4}/f/a.xlsx?sig=1"

    v6 = await check_public_https_url("https://files.example.com/a", resolver_for("2606:4700:4700::1111"))
    assert v6.target == "https://[2606:4700:4700::1111]/a"


async def test_download_sends_host_and_sni_and_returns_the_body() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, content=b"id,address\n1,x", headers={"content-type": "text/csv"})

    body, content_type = await fetch_public_https(
        "https://files.example.com/a.csv",
        max_bytes=1024,
        resolver=resolver_for(PUBLIC_V4),
        transport=httpx.MockTransport(handler),
    )
    assert body == b"id,address\n1,x"
    assert content_type == "text/csv"
    assert seen[0].url.host == PUBLIC_V4
    assert seen[0].headers["host"] == "files.example.com"
    assert seen[0].extensions["sni_hostname"] == "files.example.com"


@pytest.mark.parametrize(
    ("response", "reason", "status"),
    [
        (httpx.Response(302, headers={"location": "https://127.0.0.1/"}), "redirect", 302),
        (httpx.Response(403), "http_status", 403),
        (httpx.Response(200, headers={"content-length": "4096"}, content=b"x" * 4096), "too_large", None),
    ],
)
async def test_download_failures(response: httpx.Response, reason: str, status: int | None) -> None:
    with pytest.raises(PublicFetchError) as exc:
        await fetch_public_https(
            "https://files.example.com/a.csv",
            max_bytes=1024,
            resolver=resolver_for(PUBLIC_V4),
            transport=httpx.MockTransport(lambda request: response),
        )
    assert exc.value.reason == reason
    assert exc.value.status_code == status


async def test_stops_reading_past_the_cap_without_a_content_length() -> None:
    async def chunks():  # type: ignore[no-untyped-def]
        for _ in range(10):
            yield b"x" * 512

    with pytest.raises(PublicFetchError) as exc:
        await fetch_public_https(
            "https://files.example.com/a.csv",
            max_bytes=1024,
            resolver=resolver_for(PUBLIC_V4),
            transport=httpx.MockTransport(lambda request: httpx.Response(200, content=chunks())),
        )
    assert exc.value.reason == "too_large"


async def test_network_errors_are_reported_as_network() -> None:
    def fail(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    with pytest.raises(PublicFetchError) as exc:
        await fetch_public_https(
            "https://files.example.com/a.csv",
            max_bytes=1024,
            resolver=resolver_for(PUBLIC_V4),
            transport=httpx.MockTransport(fail),
        )
    assert exc.value.reason == "network"


async def test_a_download_that_trickles_bytes_ends_at_the_total_deadline() -> None:
    import asyncio

    class Trickle(httpx.AsyncByteStream):
        async def __aiter__(self):  # type: ignore[no-untyped-def]
            while True:
                await asyncio.sleep(0.02)  # never idle long enough for the per-read timeout
                yield b"x"

    transport = httpx.MockTransport(lambda _request: httpx.Response(200, stream=Trickle()))
    with pytest.raises(PublicFetchError) as exc:
        await fetch_public_https(
            "https://files.example.com/a.csv",
            max_bytes=1024 * 1024,
            timeout_seconds=5,
            total_timeout_seconds=0.2,
            resolver=resolver_for(PUBLIC_V4),
            transport=transport,
        )
    assert exc.value.reason == "network"

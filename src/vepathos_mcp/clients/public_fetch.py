"""Download a file from a URL the model supplied, without reaching the server's own network.

`import_delivery_file` downloads `file.download_url`. ChatGPT fills it with its file host, but any
client can put any URL there, and this process runs inside the VM's Docker network, next to Core,
Postgres and the cloud metadata endpoint. So the download:

- accepts only https on the default port, without credentials in the URL;
- resolves the host itself and refuses it when any address is not public (loopback, private,
  link-local, carrier-grade NAT, documentation, multicast, IPv4-mapped private, ...);
- connects to the address it checked, sending the hostname as Host and TLS SNI, so a second DNS
  answer cannot swap in an internal address (DNS rebinding) and the certificate is still verified
  against the hostname;
- follows no redirect, stops reading past the size cap and gives up after the timeout.
"""

from __future__ import annotations

import asyncio
import ipaddress
import socket
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Literal
from urllib.parse import urlsplit

import httpx

FetchFailure = Literal["invalid_url", "blocked_host", "redirect", "http_status", "too_large", "network"]

Resolver = Callable[[str], Awaitable[list[str]]]


@dataclass
class PublicFetchError(Exception):
    reason: FetchFailure
    status_code: int | None = None


def is_public_address(address: str) -> bool:
    """True only for globally routable unicast addresses."""

    try:
        ip = ipaddress.ip_address(address.split("%", 1)[0])
    except ValueError:
        return False
    if isinstance(ip, ipaddress.IPv6Address):
        mapped = ip.ipv4_mapped or ip.sixtofour
        if mapped is not None:
            return is_public_address(str(mapped))
        if ip.teredo is not None:
            return False
    # is_global excludes private, loopback, link-local, CGNAT (100.64/10), documentation and reserved
    # ranges; multicast addresses can still be "global", so they are excluded on their own.
    return ip.is_global and not ip.is_multicast


async def resolve_host(host: str) -> list[str]:
    loop = asyncio.get_running_loop()
    infos = await loop.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    return list(dict.fromkeys(str(info[4][0]) for info in infos))


@dataclass(frozen=True)
class CheckedUrl:
    host: str
    address: str
    target: str  # the URL with the checked address in place of the host


async def check_public_https_url(url: str, resolver: Resolver = resolve_host) -> CheckedUrl:
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError:
        raise PublicFetchError("invalid_url") from None
    host = parts.hostname
    if parts.scheme != "https" or not host or parts.username is not None or parts.password is not None:
        raise PublicFetchError("invalid_url")
    if port not in (None, 443):
        raise PublicFetchError("blocked_host")

    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        literal = None
    if literal is not None:
        addresses = [str(literal)]
    else:
        try:
            addresses = await resolver(host)
        except (OSError, UnicodeError):
            raise PublicFetchError("network") from None
    if not addresses or not all(is_public_address(address) for address in addresses):
        raise PublicFetchError("blocked_host")

    address = addresses[0]
    netloc = f"[{address}]" if ":" in address else address
    target = parts._replace(netloc=netloc).geturl()
    return CheckedUrl(host=host, address=address, target=target)


async def fetch_public_https(
    url: str,
    *,
    max_bytes: int,
    timeout_seconds: float = 30.0,
    resolver: Resolver = resolve_host,
    transport: httpx.AsyncBaseTransport | None = None,
) -> tuple[bytes, str | None]:
    """Returns the body and its content type, or raises PublicFetchError."""

    checked = await check_public_https_url(url, resolver)
    try:
        async with (
            httpx.AsyncClient(follow_redirects=False, timeout=timeout_seconds, transport=transport) as client,
            client.stream(
                "GET",
                checked.target,
                headers={"Host": checked.host, "Accept": "*/*"},
                extensions={"sni_hostname": checked.host},
            ) as response,
        ):
            if response.is_redirect:
                raise PublicFetchError("redirect", response.status_code)
            if response.status_code >= 400:
                raise PublicFetchError("http_status", response.status_code)
            declared = response.headers.get("content-length")
            if declared and declared.isdigit() and int(declared) > max_bytes:
                raise PublicFetchError("too_large")
            body = bytearray()
            async for chunk in response.aiter_bytes():
                body.extend(chunk)
                if len(body) > max_bytes:
                    raise PublicFetchError("too_large")
            return bytes(body), response.headers.get("content-type")
    except PublicFetchError:
        raise
    except httpx.HTTPError:
        raise PublicFetchError("network") from None

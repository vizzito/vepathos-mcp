"""Download a file from a URL the model supplied, without reaching the server's own network.

`import_deliveries` downloads `file.download_url`. ChatGPT fills it with its file host, but any
client can put any URL there, and this process runs inside the VM's Docker network, next to Core,
Postgres and the cloud metadata endpoint. So the download:

- accepts only https on the default port, without credentials in the URL;
- rewrites public Google Drive share links to a direct download URL (no redirect hop);
- resolves the host itself and refuses it when any address is not public (loopback, private,
  link-local, carrier-grade NAT, documentation, multicast, IPv4-mapped private, ...);
- connects to the address it checked, sending the hostname as Host and TLS SNI, so a second DNS
  answer cannot swap in an internal address (DNS rebinding) and the certificate is still verified
  against the hostname;
- follows at most MAX_REDIRECTS hops, and puts every hop through the same checks as the first URL
  (https only, public addresses only, pinned address): Google's own export answers 307 to
  googleusercontent.com, so refusing every redirect refused every Sheets link, while a redirect
  to an internal address is still refused;
- stops reading past the size cap and gives up after the timeout.
"""

from __future__ import annotations

import asyncio
import ipaddress
import socket
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Literal
from urllib.parse import urljoin, urlsplit

import httpx

from vepathos_mcp.clients.google_drive import normalize_public_download_url

# Google's Sheets and Drive exports take one hop to googleusercontent.com; nothing legitimate needs many.
MAX_REDIRECTS = 3
FetchFailure = Literal[
    "invalid_url", "blocked_host", "redirect", "http_status", "not_a_file", "too_large", "network"
]

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


def _looks_like_html_interstitial(body: bytes, content_type: str | None) -> bool:
    """Drive sometimes returns an HTML virus-scan page instead of the file."""

    head = body[:200].lstrip().lower()
    if head.startswith(b"<!doctype html") or head.startswith(b"<html"):
        return True
    if content_type and "text/html" in content_type.lower() and b"<html" in body[:2048].lower():
        return True
    return False


async def _read_file(response: httpx.Response, max_bytes: int) -> tuple[bytes, str | None]:
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
    content_type = response.headers.get("content-type")
    raw = bytes(body)
    # A sign-in or confirm page answers 200 with HTML: the link is not a public file.
    if _looks_like_html_interstitial(raw, content_type):
        raise PublicFetchError("not_a_file")
    return raw, content_type


async def fetch_public_https(
    url: str,
    *,
    max_bytes: int,
    timeout_seconds: float = 30.0,
    total_timeout_seconds: float = 90.0,
    resolver: Resolver = resolve_host,
    transport: httpx.AsyncBaseTransport | None = None,
) -> tuple[bytes, str | None]:
    """Returns the body and its content type, or raises PublicFetchError.

    `timeout_seconds` is httpx's per-operation (inactivity) timeout; `total_timeout_seconds` bounds the
    whole download, so a server that trickles bytes cannot hold the call open."""

    url = normalize_public_download_url(url)
    try:
        async with (
            asyncio.timeout(total_timeout_seconds),
            httpx.AsyncClient(follow_redirects=False, timeout=timeout_seconds, transport=transport) as client,
        ):
            # Redirects are followed by hand so that each hop is checked and pinned like the first URL:
            # httpx following them itself would connect wherever a Location header says.
            status: int | None = None
            for _hop in range(MAX_REDIRECTS + 1):
                checked = await check_public_https_url(url, resolver)
                async with client.stream(
                    "GET",
                    checked.target,
                    headers={"Host": checked.host, "Accept": "*/*"},
                    extensions={"sni_hostname": checked.host},
                ) as response:
                    location = response.headers.get("location")
                    if response.is_redirect and location:
                        status = response.status_code
                        url = urljoin(url, location)
                        continue
                    if response.is_redirect:
                        raise PublicFetchError("redirect", response.status_code)
                    return await _read_file(response, max_bytes)
            raise PublicFetchError("redirect", status)
    except PublicFetchError:
        raise
    except (httpx.HTTPError, TimeoutError):
        raise PublicFetchError("network") from None

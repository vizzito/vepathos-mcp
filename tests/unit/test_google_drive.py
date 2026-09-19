"""Public Google Drive share links rewrite to a no-redirect download URL."""

from __future__ import annotations

import httpx
import pytest

from vepathos_mcp.clients.google_drive import (
    extract_google_drive_file_id,
    google_drive_direct_download_url,
    normalize_public_download_url,
)
from vepathos_mcp.clients.public_fetch import PublicFetchError, fetch_public_https

FILE_ID = "15bwqLzB_hUSAJ5iz2NbXiDAiAhaGi-dy"
DIRECT = f"https://drive.usercontent.google.com/download?id={FILE_ID}&export=download"
PUBLIC_V4 = "93.184.216.34"


@pytest.mark.parametrize(
    "url",
    [
        f"https://drive.google.com/file/d/{FILE_ID}/view?usp=sharing",
        f"https://drive.google.com/file/d/{FILE_ID}/view",
        f"https://drive.google.com/open?id={FILE_ID}",
        f"https://drive.google.com/uc?id={FILE_ID}&export=download",
        f"https://drive.google.com/uc?export=download&id={FILE_ID}",
        DIRECT,
    ],
)
def test_extracts_drive_file_id(url: str) -> None:
    assert extract_google_drive_file_id(url) == FILE_ID


@pytest.mark.parametrize(
    "url",
    [
        "https://files.example.com/a.xlsx",
        "https://dropbox.com/s/x/file.xlsx",
        "not a url",
    ],
)
def test_non_drive_urls_are_unchanged(url: str) -> None:
    assert extract_google_drive_file_id(url) is None
    assert google_drive_direct_download_url(url) is None
    assert normalize_public_download_url(url) == url


def test_share_view_rewrites_to_usercontent_download() -> None:
    share = f"https://drive.google.com/file/d/{FILE_ID}/view?usp=sharing"
    assert google_drive_direct_download_url(share) == DIRECT
    assert normalize_public_download_url(share) == DIRECT


def test_sheets_export_url() -> None:
    sheet = f"https://docs.google.com/spreadsheets/d/{FILE_ID}/edit#gid=0"
    assert (
        google_drive_direct_download_url(sheet)
        == f"https://docs.google.com/spreadsheets/d/{FILE_ID}/export?format=xlsx"
    )


async def test_fetch_rewrites_drive_share_before_request() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, content=b"id,address\n1,x", headers={"content-type": "text/csv"})

    async def resolve(host: str) -> list[str]:
        assert host == "drive.usercontent.google.com"
        return [PUBLIC_V4]

    body, _ = await fetch_public_https(
        f"https://drive.google.com/file/d/{FILE_ID}/view?usp=sharing",
        max_bytes=1024,
        resolver=resolve,
        transport=httpx.MockTransport(handler),
    )
    assert body.startswith(b"id,address")
    assert seen[0].headers["host"] == "drive.usercontent.google.com"
    assert f"id={FILE_ID}" in str(seen[0].url)
    assert "export=download" in str(seen[0].url)


async def test_html_interstitial_is_rejected() -> None:
    async def resolve(host: str) -> list[str]:
        return [PUBLIC_V4]

    with pytest.raises(PublicFetchError) as exc:
        await fetch_public_https(
            f"https://drive.google.com/file/d/{FILE_ID}/view",
            max_bytes=1024,
            resolver=resolve,
            transport=httpx.MockTransport(
                lambda _r: httpx.Response(
                    200,
                    content=b"<!DOCTYPE html><html>confirm</html>",
                    headers={"content-type": "text/html"},
                )
            ),
        )
    assert exc.value.reason == "not_a_file"

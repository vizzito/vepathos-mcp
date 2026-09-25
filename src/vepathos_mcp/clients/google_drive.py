"""Rewrite public Google Drive share links to a fetchable download URL.

Share pages (`/file/d/…/view`) are HTML, not the file, so a share link as the user copies it is
never downloadable on its own: the model can pass one to `import_deliveries` instead of shelling
out to gdown/curl only because this rewrites it first.

`drive.usercontent.google.com/download?id=…&export=download` answers the bytes for a publicly
shared Drive file with no hop. A Docs/Sheets/Slides link exports instead, and that export answers
307 to googleusercontent.com — one hop `public_fetch` follows and checks like any other URL. This
file used to say the fetch refused every redirect; it stopped being true when the fetcher learned
to follow them, and the stale sentence sent a later reader hunting a bug that was not here.
"""

from __future__ import annotations

import re
from urllib.parse import parse_qs, urlencode, urlsplit, urlunsplit

_DRIVE_HOSTS = frozenset(
    {
        "drive.google.com",
        "docs.google.com",
        "drive.usercontent.google.com",
    }
)

# /file/d/<id>/…  or docs /spreadsheets|document|presentation/d/<id>
_FILE_PATH_ID = re.compile(r"/(?:file|spreadsheets|document|presentation)/d/([a-zA-Z0-9_-]{10,})")
_DOCS_EXPORTABLE = re.compile(r"^/(spreadsheets|document|presentation)/d/([a-zA-Z0-9_-]{10,})")

_DIRECT = "https://drive.usercontent.google.com/download"


def extract_google_drive_file_id(url: str) -> str | None:
    try:
        parts = urlsplit(url.strip())
    except ValueError:
        return None
    host = (parts.hostname or "").lower()
    if host not in _DRIVE_HOSTS:
        return None

    path_match = _FILE_PATH_ID.search(parts.path or "")
    if path_match:
        return path_match.group(1)

    query = parse_qs(parts.query)
    for key in ("id", "file_id"):
        values = query.get(key) or []
        if values and re.fullmatch(r"[a-zA-Z0-9_-]{10,}", values[0]):
            return values[0]
    return None


def google_drive_direct_download_url(url: str) -> str | None:
    """If `url` is a public Drive/Docs share, return a no-redirect download URL."""

    file_id = extract_google_drive_file_id(url)
    if not file_id:
        return None

    try:
        parts = urlsplit(url.strip())
    except ValueError:
        return None
    host = (parts.hostname or "").lower()
    path = parts.path or ""

    # Already the direct usercontent download — keep confirm/resourcekey if present.
    if host == "drive.usercontent.google.com" and path.rstrip("/").endswith("/download"):
        query = parse_qs(parts.query, keep_blank_values=True)
        if "export" not in query:
            query["export"] = ["download"]
        if "id" not in query:
            query["id"] = [file_id]
        flat = [(k, v) for k, vs in query.items() for v in vs]
        return urlunsplit(("https", host, "/download", urlencode(flat), ""))

    # Google Docs/Sheets/Slides — export, not binary Drive file.
    docs = _DOCS_EXPORTABLE.match(path)
    if host == "docs.google.com" and docs:
        kind = docs.group(1)
        export_fmt = {"spreadsheets": "xlsx", "document": "docx", "presentation": "pptx"}[kind]
        return f"https://docs.google.com/{kind}/d/{file_id}/export?format={export_fmt}"

    return f"{_DIRECT}?{urlencode({'id': file_id, 'export': 'download'})}"


def normalize_public_download_url(url: str) -> str:
    """Rewrite known share hosts to a fetchable URL; otherwise return `url` unchanged."""

    return google_drive_direct_download_url(url) or url

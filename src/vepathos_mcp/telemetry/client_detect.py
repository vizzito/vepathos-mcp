"""Normalize the calling client into a small label set for metrics and Core analytics.

Preference order, from most to least reliable:
1. The OAuth `client_id` when it is a Client ID Metadata Document URL (host is verified by the
   authorization server when it fetches the document).
2. The client's self-reported `clientInfo.name`.
Unknown or unverifiable values map to `other`.
"""

from __future__ import annotations

from urllib.parse import urlparse

KNOWN_LABELS = ("claude-code", "claude", "cursor", "vscode", "codex", "chatgpt", "inspector", "other")

_HOST_LABELS: tuple[tuple[str, str], ...] = (
    ("claude.ai", "claude"),
    ("anthropic.com", "claude"),
    ("chatgpt.com", "chatgpt"),
    ("openai.com", "chatgpt"),
    ("cursor.com", "cursor"),
    ("cursor.sh", "cursor"),
    ("vscode.dev", "vscode"),
    ("code.visualstudio.com", "vscode"),
)

_NAME_LABELS: tuple[tuple[str, str], ...] = (
    ("claude-code", "claude-code"),
    ("claude code", "claude-code"),
    ("claude", "claude"),
    ("cursor", "cursor"),
    ("visual studio code", "vscode"),
    ("vscode", "vscode"),
    ("copilot", "vscode"),
    ("codex", "codex"),
    ("chatgpt", "chatgpt"),
    ("openai", "chatgpt"),
    ("inspector", "inspector"),
)


def label_from_client_id(client_id: str | None) -> str | None:
    if not client_id or not client_id.startswith("https://"):
        return None
    parsed = urlparse(client_id)
    host = (parsed.hostname or "").lower()
    path = parsed.path.lower()
    for suffix, label in _HOST_LABELS:
        if host == suffix or host.endswith("." + suffix):
            if label == "claude" and "claude-code" in path:
                return "claude-code"
            if label == "chatgpt" and "codex" in path:
                return "codex"
            return label
    return "other"


def label_from_client_name(name: str | None) -> str | None:
    if not name:
        return None
    lowered = name.lower()
    for needle, label in _NAME_LABELS:
        if needle in lowered:
            return label
    return "other"


def detect_client(client_id: str | None, client_name: str | None) -> str:
    return label_from_client_id(client_id) or label_from_client_name(client_name) or "other"

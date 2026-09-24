"""The repository is public: it must not say where production lives or who logs into it.

Finding F8 (docs/agent-test-plan.md): the production VM's address and its SSH user sat in two deploy
documents, nine times. Nothing stopped a third document from adding them again, so this does. The
address is still reachable — `dig +short api.vepathos.com A` — and the docs say exactly that instead
of printing it; what the repo no longer does is hand out the host, the login and the map between them.

Tests are excluded on purpose: they are full of addresses whose whole job is to be refused (metadata
endpoints, private ranges, a well-known public host for the SSRF guard).
"""

from __future__ import annotations

import ipaddress
import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
_IPV4 = re.compile(r"(?<![\d.])(\d{1,3}(?:\.\d{1,3}){3})(?![\d.])")
# SSH logins as a deploy guide writes them: `ssh user@host`, or a bare `user@` standing for the login
# ("`deploy@` api-prod"). Not a package pinned to a version (`actions/checkout@v4`,
# `inspector@latest`) and not an email: in both, the @ is followed straight by a name.
_SSH_LOGIN = re.compile(r"\bssh\s+[a-z_][\w-]*@[\w.-]+|(?<![\w/@.-])[a-z_][\w-]*@(?=[`\s]|$)", re.M)

# Public addresses a public document may name, and why.
ALLOWED_NETWORKS = {
    # Anthropic's published egress range: the authorization server must be reachable from it.
    ipaddress.ip_network("160.79.104.0/21"): "Anthropic egress, published by Anthropic",
}


def _tracked_files() -> list[Path]:
    git = shutil.which("git")
    if git is None or not (REPO / ".git").exists():
        pytest.skip("not a git checkout: nothing says which files the repo publishes")
    listed = subprocess.run(  # noqa: S603 — fixed arguments, no user input
        [git, "ls-files"], cwd=REPO, capture_output=True, text=True, check=True
    ).stdout.split()
    return [REPO / name for name in listed if not name.startswith("tests/")]


def _text(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return None  # binaries and unreadable files carry no prose


def _allowed(address: ipaddress.IPv4Address) -> bool:
    return not address.is_global or any(address in net for net in ALLOWED_NETWORKS)


def test_no_public_address_is_written_into_the_repo() -> None:
    found = []
    for path in _tracked_files():
        text = _text(path)
        if text is None:
            continue
        for match in _IPV4.finditer(text):
            try:
                address = ipaddress.IPv4Address(match.group(1))
            except ValueError:
                continue  # 999.1.1.1 is a typo or a version, not an address
            if not _allowed(address):
                line = text.count("\n", 0, match.start()) + 1
                found.append(f"{path.relative_to(REPO)}:{line}: {address}")
    assert not found, (
        "public IPv4 addresses in a public repo — write how to find the address "
        "(e.g. `dig +short api.vepathos.com A`) instead of the address:\n" + "\n".join(found)
    )


def test_no_ssh_login_is_written_into_the_repo() -> None:
    found = []
    for path in _tracked_files():
        if path.suffix not in {".md", ".txt", ".sh", ".yml", ".yaml", ".toml", ".example"}:
            continue
        text = _text(path)
        if text is None:
            continue
        for match in _SSH_LOGIN.finditer(text):
            line = text.count("\n", 0, match.start()) + 1
            found.append(f"{path.relative_to(REPO)}:{line}: {match.group(0).strip()}")
    assert not found, "SSH logins in a public repo — keep them in the operator's notes:\n" + "\n".join(found)


def test_the_check_would_have_caught_f8() -> None:
    # The two sentences F8 removed, verbatim in shape. If a refactor of the patterns stops seeing
    # them, the tests above pass for the wrong reason.
    assert not _allowed(ipaddress.IPv4Address("178.105.42.199"))
    assert _SSH_LOGIN.search("| VM | `deploy@` api-prod |")
    assert _allowed(ipaddress.IPv4Address("10.0.0.2"))  # the Docker bridge bind the docs explain
    assert _allowed(ipaddress.IPv4Address("160.79.104.1"))
    assert _SSH_LOGIN.search("ssh deploy@api-prod")
    assert not _SSH_LOGIN.search("write to soporte@vepathos.com")
    assert not _SSH_LOGIN.search("uses: actions/checkout@v4")
    assert not _SSH_LOGIN.search("npx @modelcontextprotocol/inspector@latest")

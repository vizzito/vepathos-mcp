"""The command line: help for whoever develops or operates the server. Serving stays the default."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from tests.conftest import API_KEY, DEV_TOKEN, SERVICE_KEY, make_settings
from vepathos_mcp import __version__
from vepathos_mcp.__main__ import build_parser, main
from vepathos_mcp.clients.vepathos_api import VepathosApiClient
from vepathos_mcp.doctor import run_doctor

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_no_command_still_means_serve() -> None:
    # The Dockerfile's ENTRYPOINT is the bare command, and --transport keeps working as before.
    assert build_parser([]).parse_args([]).command is None
    args = build_parser([]).parse_args(["--transport", "stdio"])
    assert args.command is None and args.transport == "stdio"


def test_help_lists_the_tools_by_task(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exit_info:
        main(["--help"])
    assert exit_info.value.code == 0
    out = capsys.readouterr().out
    assert "get_account" in out and "Saved vehicles and depots" in out and "vepathos-mcp doctor" in out


def test_tools_and_workflows_print_the_registry(capsys: pytest.CaptureFixture[str]) -> None:
    main(["tools"])
    out = capsys.readouterr().out
    assert "optimize_delivery_routes\n------------------------" in out
    assert "Published:   when MCP_CATALOG_WRITE_TOOLS_ENABLED=true" in out
    main(["workflows"])
    assert "list_fleet → manage_vehicle | manage_depot" in capsys.readouterr().out
    main(["tools", "--table"])
    assert "| `get_account` |" in capsys.readouterr().out


def test_check_passes_on_a_current_repository(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(REPO_ROOT)
    main(["tools", "--check"])


def test_writing_docs_needs_the_repository_root(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    with pytest.raises(SystemExit) as exit_info:
        main(["tools", "--check"])
    assert exit_info.value.code == 2


async def test_doctor_reports_the_configuration_and_never_a_secret(
    core_client_factory: Callable[..., VepathosApiClient],
) -> None:
    lines: list[str] = []
    core = core_client_factory()
    try:
        code = await run_doctor(make_settings(), core, lines.append, offline=False)
    finally:
        await core.aclose()
    out = "\n".join(lines)
    assert code == 0 and __version__ in out and "core check:   reachable" in out
    assert "catalog writes off" in out and "publishes 10 tools" in out
    for secret in (SERVICE_KEY, DEV_TOKEN, API_KEY, API_KEY.split(":")[1]):
        assert secret not in out


async def test_doctor_fails_when_core_does_not_answer(
    core_client_factory: Callable[..., VepathosApiClient],
) -> None:
    import httpx
    from devtools.fake_core.app import FakeCoreState, create_fake_core

    wrong_key = VepathosApiClient(
        "http://core.test",
        "not-the-key",
        transport=httpx.ASGITransport(app=create_fake_core(FakeCoreState(service_key=SERVICE_KEY))),
    )
    lines: list[str] = []
    try:
        assert await run_doctor(make_settings(), wrong_key, lines.append, offline=False) == 1
    finally:
        await wrong_key.aclose()
    assert "UNREACHABLE" in lines[-1]
    assert await run_doctor(make_settings(), None, lines.append, offline=True) == 0

"""`vepathos-mcp doctor`: what this configuration is and publishes, and whether Core answers.

For whoever operates the server: one command whose output can be pasted into a ticket. It prints no
secret — not the service key, not a credential, not a token — only whether each is set.
"""

from __future__ import annotations

from collections.abc import Callable

from vepathos_mcp import __version__
from vepathos_mcp.clients.vepathos_api import VepathosApiClient
from vepathos_mcp.config import Settings
from vepathos_mcp.reference import build_reference
from vepathos_mcp.tools.descriptions import server_instructions

EXIT_OK = 0
EXIT_CORE_UNREACHABLE = 1
EXIT_INVALID_CONFIGURATION = 2
# Claude Code cuts server instructions at this length.
INSTRUCTIONS_CUT = 2_048


def _on(value: bool) -> str:
    return "on" if value else "off"


async def run_doctor(
    settings: Settings,
    core: VepathosApiClient | None,
    write: Callable[[str], None],
    *,
    offline: bool,
) -> int:
    instructions = server_instructions(
        confirm_before_optimize=settings.confirm_before_optimize,
        import_tools=settings.import_tools_enabled,
        map_shares=settings.map_shares_enabled,
        catalog_writes=settings.catalog_write_tools_enabled,
    )
    names = build_reference(settings).names()
    write(f"vepathos-mcp {__version__} · environment {settings.environment} · transport {settings.transport}")
    write(f"auth modes:   {', '.join(mode.value for mode in settings.auth_modes)}")
    write(f"resource url: {settings.resource_url}")
    write(f"core:         {settings.core_base_url}")
    write(f"service key:  {'set' if settings.core_service_key.get_secret_value() else 'NOT SET'}")
    flags = {
        "confirm gate": settings.confirm_before_optimize,
        "imports": settings.import_tools_enabled,
        "map shares": settings.map_shares_enabled,
        "catalog writes": settings.catalog_write_tools_enabled,
    }
    write("flags:        " + " · ".join(f"{name} {_on(value)}" for name, value in flags.items()))
    write(f"publishes {len(names)} tools: {', '.join(names)}")
    note = (
        " (hosts that cut at 2,048 characters lose the rest; every rule also lives in a tool description)"
        if len(instructions) > INSTRUCTIONS_CUT
        else ""
    )
    write(f"instructions: {len(instructions):,} characters{note}")
    if offline:
        write("core check:   skipped (--offline)")
        return EXIT_OK

    owned = core is None
    client = core or VepathosApiClient(
        settings.core_base_url,
        settings.core_service_key.get_secret_value(),
        timeout_seconds=settings.core_timeout_seconds,
        submit_timeout_seconds=settings.core_submit_timeout_seconds,
    )
    try:
        reachable = await client.health()
    finally:
        if owned:
            await client.aclose()
    write(
        "core check:   reachable" if reachable else "core check:   UNREACHABLE (URL, service key or network)"
    )
    return EXIT_OK if reachable else EXIT_CORE_UNREACHABLE

"""Snapshot of tools/list — names, schemas, description hashes (T17).

Bump __version__ in the same commit when this snapshot must change.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

import pytest
from mcp.client import Client

from tests.conftest import FakeClock, make_settings
from vepathos_mcp import __version__
from vepathos_mcp.clients.vepathos_api import VepathosApiClient
from vepathos_mcp.server import build_server

# Frozen on 0.9.0 (one optimize_routes and one import_deliveries: the source of stops is an argument).
# Update deliberately with the version.
EXPECTED_TOOL_NAMES = (
    "optimize_routes",
    "get_optimization_result",
    "list_plans",
    "import_deliveries",
    "get_import_result",
    "update_import_mapping",
    "list_datasets",
    "geocode_addresses",
    "get_geocode_result",
    "get_account",
    "list_fleet",
    "manage_catalog",
    "list_automations",
    "create_automation",
)

# sha256 of sorted (name, description) pairs with gate off (prod default as of 16/09) and import tools on.
# Changed on 0.9.0: manage_vehicle and manage_depot became one manage_catalog.
EXPECTED_DESC_HASH_GATE_OFF = "268103d5327d981b23d448ecf0cf21794da1885fa328f3e84d473c21dbf97318"


CATALOG_WRITE_TOOLS = {"manage_catalog"}


def _desc_hash(tools: list[Any]) -> str:
    payload = sorted((t.name, t.description or "") for t in tools)
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()
    return hashlib.sha256(raw).hexdigest()


async def _list_tools(core_client_factory, clock: FakeClock, **overrides: str) -> tuple[list[Any], str]:
    settings = make_settings(MCP_TRANSPORT="stdio", MCP_CONFIRM_BEFORE_OPTIMIZE="false", **overrides)
    core: VepathosApiClient = core_client_factory()
    server = build_server(settings, core, sleep=clock.sleep, clock=clock)
    async with Client(server) as client:
        listed = await client.list_tools()
        instructions = client.instructions or ""
    await core.aclose()
    return listed.tools, instructions


@pytest.fixture
async def listed_tools(core_client_factory, clock: FakeClock):
    # The surface as ChatGPT staging will see it: import tools on.
    tools, _ = await _list_tools(
        core_client_factory,
        clock,
        MCP_IMPORT_TOOLS_ENABLED="true",
        MCP_CATALOG_WRITE_TOOLS_ENABLED="true",
    )
    return tools


async def test_import_tools_stay_hidden_until_enabled(core_client_factory, clock: FakeClock) -> None:
    tools, instructions = await _list_tools(core_client_factory, clock)
    names = {t.name for t in tools}
    hidden = {
        "import_deliveries",
        "get_import_result",
        "update_import_mapping",
        "list_datasets",
    }
    assert not names & hidden
    assert names == set(EXPECTED_TOOL_NAMES) - hidden - CATALOG_WRITE_TOOLS
    for name in hidden:
        assert name not in instructions
        assert all(name not in (t.description or "") for t in tools)


async def test_catalog_write_tools_stay_hidden_until_enabled(core_client_factory, clock: FakeClock) -> None:
    # Same rule as the import tools: deploying the code publishes nothing, and no text names them.
    tools, instructions = await _list_tools(core_client_factory, clock, MCP_IMPORT_TOOLS_ENABLED="true")
    assert not {t.name for t in tools} & CATALOG_WRITE_TOOLS
    for name in CATALOG_WRITE_TOOLS:
        assert name not in instructions
        assert all(name not in (t.description or "") for t in tools)

    _, enabled = await _list_tools(core_client_factory, clock, MCP_CATALOG_WRITE_TOOLS_ENABLED="true")
    assert "manage_catalog" in enabled and "master data" in enabled


async def test_plans_are_published_without_the_import_tools(core_client_factory, clock: FakeClock) -> None:
    # Plans exist for every account: a chat on a server without imports still finds and reruns its runs,
    # and another try is reachable only by running a plan_id.
    tools, instructions = await _list_tools(core_client_factory, clock)
    assert {"list_plans", "optimize_routes"} <= {t.name for t in tools}
    assert "list_plans" in instructions and "plan_id for a saved plan" in instructions
    assert "same stops or fewer" in instructions


async def test_excluded_stops_are_described_as_this_run_only(listed_tools) -> None:
    # Core keeps every stop in the plan; exclude_stop_ids shapes one run (16/09).
    optimize = next(t for t in listed_tools if t.name == "optimize_routes")
    exclude = optimize.input_schema["properties"]["exclude_stop_ids"]["description"]
    assert "this run only" in exclude and "plan keeps them" in exclude


async def test_no_published_text_offers_free_replans_or_variants(listed_tools) -> None:
    # 16/09: "free replans" / "variants" per dataset became one free retry per plan (24 h, same stops
    # or fewer). Descriptions and schemas must not keep the old promise.
    for tool in listed_tools:
        text = json.dumps(
            [tool.description, tool.input_schema, tool.output_schema], ensure_ascii=False
        ).lower()
        assert "replan" not in text, tool.name
        assert "variant" not in text, tool.name


async def test_tools_list_names_match_snapshot(listed_tools) -> None:
    names = tuple(t.name for t in listed_tools)
    assert names == EXPECTED_TOOL_NAMES


async def test_tools_list_description_hash_tracked(listed_tools) -> None:
    digest = _desc_hash(listed_tools)
    # First run after a description change: fail with the new hash so the commit bumps both.
    if digest != EXPECTED_DESC_HASH_GATE_OFF:
        pytest.fail(
            f"tools/list description hash changed to {digest!r}. "
            f"Bump __version__ (now {__version__}) and EXPECTED_DESC_HASH_GATE_OFF in the same commit."
        )


async def test_version_is_bumped_with_tool_surface() -> None:
    assert tuple(int(p) for p in __version__.split(".")[:2]) >= (0, 6)


def test_package_version_matches_the_server_version() -> None:
    # /health and serverInfo report __version__; the package metadata said 0.3.0 while the server said
    # 0.4.0 (2026-09-16). One number, bumped together.
    import tomllib
    from pathlib import Path

    pyproject = tomllib.loads((Path(__file__).resolve().parents[2] / "pyproject.toml").read_text())
    assert pyproject["project"]["version"] == __version__

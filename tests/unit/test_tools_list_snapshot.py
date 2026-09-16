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

# Frozen on 0.4.0 (import/dataset tools + preflight fields). Update deliberately with the version.
EXPECTED_TOOL_NAMES = (
    "optimize_delivery_routes",
    "get_optimization_result",
    "import_delivery_file",
    "import_delivery_text",
    "get_import_result",
    "update_import_mapping",
    "optimize_dataset",
    "list_datasets",
    "geocode_addresses",
    "get_geocode_result",
    "get_account",
    "list_fleet",
)

# sha256 of sorted (name, description) pairs with gate off (prod default as of 16/09) and import tools on.
EXPECTED_DESC_HASH_GATE_OFF = "881c652ce2bed73d166f8dae7afdd33417f3f785e4ddd03d3b900899120fc0c5"


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
    # The 0.4.0 surface as ChatGPT staging will see it: import tools on.
    tools, _ = await _list_tools(core_client_factory, clock, MCP_IMPORT_TOOLS_ENABLED="true")
    return tools


async def test_import_tools_stay_hidden_until_enabled(core_client_factory, clock: FakeClock) -> None:
    tools, instructions = await _list_tools(core_client_factory, clock)
    names = {t.name for t in tools}
    hidden = {
        "import_delivery_file",
        "import_delivery_text",
        "get_import_result",
        "update_import_mapping",
        "optimize_dataset",
        "list_datasets",
    }
    assert not names & hidden
    assert names == set(EXPECTED_TOOL_NAMES) - hidden
    for name in hidden:
        assert name not in instructions
        assert all(name not in (t.description or "") for t in tools)


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
    assert tuple(int(p) for p in __version__.split(".")[:2]) >= (0, 4)

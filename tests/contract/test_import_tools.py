"""Import / dataset tools against fake Core."""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from typing import Any

import pytest
from mcp.client import Client

from tests.conftest import FakeClock, make_settings
from vepathos_mcp.clients.vepathos_api import VepathosApiClient
from vepathos_mcp.server import build_server


@pytest.fixture
async def mcp_client(
    core_client_factory: Callable[..., VepathosApiClient], clock: FakeClock
) -> AsyncIterator[Callable[..., Any]]:
    clients: list[VepathosApiClient] = []

    async def connect(**settings_overrides: Any) -> Client:
        settings = make_settings(
            MCP_TRANSPORT="stdio",
            MCP_CONFIRM_BEFORE_OPTIMIZE="false",
            **settings_overrides,
        )
        core = core_client_factory()
        clients.append(core)
        server = build_server(settings, core, sleep=clock.sleep, clock=clock)
        return Client(server)

    yield connect
    for core in clients:
        await core.aclose()


async def call(client: Client, tool: str, arguments: dict[str, Any]) -> tuple[bool, dict[str, Any]]:
    result = await client.call_tool(tool, arguments)
    assert isinstance(result.structured_content, dict)
    return result.is_error, result.structured_content


async def test_import_file_missing_attachment_is_invalid_input(mcp_client: Callable[..., Any]) -> None:
    async with await mcp_client() as client:
        is_error, payload = await call(client, "import_delivery_file", {})
    assert is_error
    assert payload["error"]["code"] == "INVALID_INPUT"


async def test_import_text_then_optimize_dataset(mcp_client: Callable[..., Any]) -> None:
    async with await mcp_client() as client:
        is_error, created = await call(
            client,
            "import_delivery_text",
            {"text": "id,address\n1,Calle Falsa 123\n2,Av Siempre Viva 742"},
        )
        assert not is_error, created
        assert created["import_id"] and created["dataset_id"]
        is_error, status = await call(client, "get_import_result", {"import_id": created["import_id"]})
        assert not is_error and status["status"] == "completed"
        assert "stops" in status["summary"]
        assert "rows" not in status  # never dump all rows
        is_error, listed = await call(client, "list_datasets", {})
        assert not is_error
        assert any(d["dataset_id"] == created["dataset_id"] for d in listed["datasets"])
        is_error, opt = await call(
            client,
            "optimize_dataset",
            {
                "dataset_id": created["dataset_id"],
                "depot": {"latitude": -34.6, "longitude": -58.4},
                "vehicles": [{"vehicle_id": "van", "count": 1, "max_stops": 20}],
            },
        )
        assert not is_error, opt
        assert opt["optimization_id"].startswith("mcp_")
        assert opt["submitted_stops"] == 5

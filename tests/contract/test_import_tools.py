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
            **{
                "MCP_TRANSPORT": "stdio",
                "MCP_CONFIRM_BEFORE_OPTIMIZE": "false",
                "MCP_IMPORT_TOOLS_ENABLED": "true",
                **settings_overrides,
            }
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


@pytest.mark.parametrize(
    "download_url",
    [
        "https://169.254.169.254/latest/meta-data/",
        "https://127.0.0.1/internal",
        "http://files.example.com/a.xlsx",
    ],
)
async def test_import_file_refuses_an_attachment_url_inside_the_network(
    mcp_client: Callable[..., Any], download_url: str
) -> None:
    async with await mcp_client() as client:
        is_error, payload = await call(
            client, "import_delivery_file", {"file": {"download_url": download_url, "file_name": "a.xlsx"}}
        )
    assert is_error
    assert payload["error"]["code"] == "INVALID_INPUT"
    assert payload["error"]["details"]["reason"] in {"blocked_host", "invalid_url"}


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


DEPOT = {"latitude": -34.6, "longitude": -58.4}
VEHICLES = [{"vehicle_id": "van", "count": 1, "max_stops": 20}]


async def _import(client: Client) -> str:
    is_error, created = await call(client, "import_delivery_text", {"text": "id,address\n1,Calle Falsa 123"})
    assert not is_error, created
    return str(created["dataset_id"])


async def test_dataset_first_run_is_charged_and_variants_are_free_replans(
    mcp_client: Callable[..., Any], clock: FakeClock
) -> None:
    async with await mcp_client() as client:
        dataset_id = await _import(client)
        _, listed = await call(client, "list_datasets", {})
        row = next(d for d in listed["datasets"] if d["dataset_id"] == dataset_id)
        assert row["next_optimize_charged"] is True and row["free_replans_remaining"] == 0

        args = {"dataset_id": dataset_id, "depot": DEPOT, "vehicles": VEHICLES}
        is_error, first = await call(client, "optimize_dataset", args)
        assert not is_error, first
        assert first["quota_charged"] is True and first["free_replans_remaining"] == 5

        clock.now += 10  # the fake plan runs one optimization at a time
        variant = {**args, "vehicles": [{"vehicle_id": "van", "count": 2, "max_stops": 20}]}
        is_error, second = await call(client, "optimize_dataset", variant)
        assert not is_error, second
        assert second["quota_charged"] is False and second["free_replans_remaining"] == 4


async def test_free_replan_still_has_to_fit_the_plan(mcp_client: Callable[..., Any], core_state: Any) -> None:
    async with await mcp_client() as client:
        dataset_id = await _import(client)
        args = {"dataset_id": dataset_id, "depot": DEPOT, "vehicles": VEHICLES}
        is_error, first = await call(client, "optimize_dataset", args)
        assert not is_error, first
        core_state.plan.max_stops_per_request = 3  # the dataset has 5 stops
        variant = {**args, "exclude_stop_ids": ["S5"]}
        is_error, rejected = await call(client, "optimize_dataset", variant)
    assert is_error
    assert rejected["error"]["code"] == "PLAN_UPGRADE_REQUIRED"


async def test_dataset_preflight_states_the_real_stops_and_charge(
    mcp_client: Callable[..., Any],
) -> None:
    async with await mcp_client(MCP_CONFIRM_BEFORE_OPTIMIZE="true") as client:
        dataset_id = await _import(client)
        args = {"dataset_id": dataset_id, "depot": DEPOT, "vehicles": VEHICLES}
        is_error, preview = await call(client, "optimize_dataset", {**args, "exclude_stop_ids": ["S1"]})
        assert not is_error, preview
        preflight = preview["preflight"]
        assert preflight["stops"] == 4 and preflight["charges_stops"] == 4
        assert preflight["plan"]["stops_remaining_after"] == preflight["plan"]["stops_remaining"] - 4
        assert "charges its stops" in preflight["confirm_with"]

        is_error, ran = await call(client, "optimize_dataset", {**args, "confirmed": True})
        assert not is_error and ran["quota_charged"] is True

        is_error, replan = await call(client, "optimize_dataset", {**args, "service_time_minutes": 5})
        assert not is_error, replan
        assert replan["preflight"]["stops"] == 5 and replan["preflight"]["charges_stops"] == 0
        assert "free replan" in replan["preflight"]["confirm_with"]


async def test_dataset_preflight_warns_before_a_run_core_would_reject(
    mcp_client: Callable[..., Any], core_state: Any
) -> None:
    async with await mcp_client(MCP_CONFIRM_BEFORE_OPTIMIZE="true") as client:
        dataset_id = await _import(client)
        stops = core_state.datasets[dataset_id]["stops"]
        stops[0]["weight_kg"] = 2.0
        stops[1]["time_window"] = {"start": "09:00", "end": "12:00"}
        vehicles = [{"vehicle_id": "van", "count": 1, "max_weight_kg": 500}]
        is_error, preview = await call(
            client,
            "optimize_dataset",
            {"dataset_id": dataset_id, "depot": DEPOT, "vehicles": vehicles, "use_weight": True},
        )
    assert not is_error, preview
    preflight = preview["preflight"]
    codes = {w["code"]: w for w in preflight["warnings"]}
    assert codes["stops_without_weight"]["count"] == 4
    assert "route_start_time_required" in codes
    assert preflight["constraints_enforced"] == ["weight_capacity", "time_windows"]


async def test_a_new_chat_can_repeat_the_last_run_of_a_dataset(
    mcp_client: Callable[..., Any], clock: FakeClock
) -> None:
    async with await mcp_client() as client:
        dataset_id = await _import(client)
        _, listed = await call(client, "list_datasets", {})
        row = next(d for d in listed["datasets"] if d["dataset_id"] == dataset_id)
        assert row.get("last_run") is None  # absent until the dataset is optimized

        args = {
            "dataset_id": dataset_id,
            "depot": DEPOT,
            "vehicles": [{"vehicle_id": "van", "count": 2, "min_stops": 1, "max_stops": 20}],
            "route_start_time": "08:05",
            "time_zone": "Europe/Stockholm",
        }
        is_error, run = await call(client, "optimize_dataset", args)
        assert not is_error, run

    # A different MCP session on the same account sees how the dataset was last optimized.
    async with await mcp_client() as other_chat:
        _, listed = await call(other_chat, "list_datasets", {})
        row = next(d for d in listed["datasets"] if d["dataset_id"] == dataset_id)
        last = row["last_run"]
        assert last["optimization_id"] == run["optimization_id"]
        assert last["depot"] == {"lat": DEPOT["latitude"], "lng": DEPOT["longitude"]}
        assert last["vehicles"] == [{"id": "van", "count": 2, "min_stops": 1, "max_stops": 20}]
        assert last["schedule"]["route_start_time"] == "08:05"
        assert last["dataset_id"] == dataset_id and last["excluded_stops"] == 0
        assert row["next_optimize_charged"] is False and row["free_replans_remaining"] == 5

        clock.now += 10
        is_error, result = await call(
            other_chat, "get_optimization_result", {"optimization_id": run["optimization_id"]}
        )
    assert not is_error, result
    assert result["request"]["depot"] == last["depot"]
    assert result["request"]["schedule"]["time_zone"] == "Europe/Stockholm"


async def test_free_replans_follow_the_plan(
    mcp_client: Callable[..., Any], core_state: Any, clock: FakeClock
) -> None:
    core_state.plan.free_replans = 1  # Free and Starter
    async with await mcp_client() as client:
        dataset_id = await _import(client)
        args = {"dataset_id": dataset_id, "depot": DEPOT, "vehicles": VEHICLES}
        _, first = await call(client, "optimize_dataset", args)
        assert first["quota_charged"] is True and first["free_replans_remaining"] == 1

        clock.now += 10
        _, second = await call(client, "optimize_dataset", {**args, "exclude_stop_ids": ["S5"]})
        assert second["quota_charged"] is False and second["free_replans_remaining"] == 0

        _, listed = await call(client, "list_datasets", {})
        row = next(d for d in listed["datasets"] if d["dataset_id"] == dataset_id)
        assert row["next_optimize_charged"] is True

        clock.now += 10
        _, third = await call(client, "optimize_dataset", {**args, "exclude_stop_ids": ["S4"]})
        assert third["quota_charged"] is True

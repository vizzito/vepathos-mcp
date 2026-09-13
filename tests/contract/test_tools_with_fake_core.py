"""End-to-end tool behaviour through the MCP SDK client against the fake Core channel."""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from typing import Any

import pytest
from devtools.fake_core.app import FakeCoreState, FakePlan
from mcp.client import Client

from tests.conftest import FakeClock, make_settings, sample_arguments
from vepathos_mcp.clients.vepathos_api import VepathosApiClient
from vepathos_mcp.server import build_server


@pytest.fixture
async def mcp_client(
    core_client_factory: Callable[..., VepathosApiClient], clock: FakeClock
) -> AsyncIterator[Callable[..., Any]]:
    clients: list[VepathosApiClient] = []

    async def connect(**settings_overrides: Any) -> Client:
        # stdio transport: the credential comes from the environment (no HTTP auth in-process).
        settings = make_settings(MCP_TRANSPORT="stdio", **settings_overrides)
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


async def test_tools_list_publishes_annotations_and_strict_schemas(mcp_client: Callable[..., Any]) -> None:
    async with await mcp_client() as client:
        listed = await client.list_tools()
    tools = {t.name: t for t in listed.tools}
    assert set(tools) == {
        "optimize_delivery_routes",
        "get_optimization_result",
        "geocode_addresses",
        "get_geocode_result",
    }

    optimize = tools["optimize_delivery_routes"]
    assert optimize.annotations is not None
    assert optimize.annotations.read_only_hint is False and optimize.annotations.destructive_hint is False
    assert optimize.annotations.idempotent_hint is True and optimize.annotations.title
    assert optimize.input_schema["additionalProperties"] is False
    assert "$defs" not in str(optimize.input_schema)
    assert optimize.output_schema is not None and "optimization_id" in optimize.output_schema["properties"]
    assert "vehicle routing problem" in (optimize.description or "").lower()

    result = tools["get_optimization_result"]
    assert result.annotations is not None and result.annotations.read_only_hint is True
    assert len(optimize.name) <= 64 and len(result.name) <= 64


async def test_async_flow_queued_then_completed(mcp_client: Callable[..., Any], clock: FakeClock) -> None:
    async with await mcp_client() as client:
        is_error, created = await call(client, "optimize_delivery_routes", sample_arguments(stops=12))
        assert not is_error, created
        assert created["status"] == "queued" and created["submitted_stops"] == 12
        assert created["poll_after_seconds"] == 10
        job_id = created["optimization_id"]

        clock.now += 10
        is_error, summary = await call(client, "get_optimization_result", {"optimization_id": job_id})
        assert not is_error
        assert summary["status"] == "completed"
        assert summary["summary"]["stops_submitted"] == 12 and summary["summary"]["vehicles_used"] == 3
        assert [r["route_id"] for r in summary["routes"]] == ["r1", "r2", "r3"]

        is_error, stops = await call(
            client,
            "get_optimization_result",
            {"optimization_id": job_id, "detail": "stops", "route_id": "r2", "limit": 2},
        )
        assert not is_error
        assert [s["sequence"] for s in stops["stops"]] == [1, 2]
        assert stops["page"]["next_offset"] == 2
        assert "latitude" not in str(stops)  # coordinates are never echoed


async def test_inline_wait_returns_result_for_fast_jobs(mcp_client: Callable[..., Any]) -> None:
    async with await mcp_client(MCP_OPTIMIZE_INLINE_WAIT_SECONDS=8) as client:
        is_error, created = await call(client, "optimize_delivery_routes", sample_arguments(stops=6))
    assert not is_error
    assert created["status"] == "completed"
    assert created["result"]["summary"]["stops_assigned"] == 6


async def test_long_poll_waits_for_completion(mcp_client: Callable[..., Any]) -> None:
    async with await mcp_client(MCP_RESULT_LONGPOLL_SECONDS=20) as client:
        _, created = await call(client, "optimize_delivery_routes", sample_arguments(stops=4))
        is_error, result = await call(
            client, "get_optimization_result", {"optimization_id": created["optimization_id"]}
        )
    assert not is_error and result["status"] == "completed"


async def test_identical_arguments_return_the_same_optimization(
    mcp_client: Callable[..., Any], core_state: FakeCoreState
) -> None:
    async with await mcp_client() as client:
        _, first = await call(client, "optimize_delivery_routes", sample_arguments(stops=5))
        _, second = await call(client, "optimize_delivery_routes", sample_arguments(stops=5))
    assert first["optimization_id"] == second["optimization_id"]
    assert second["idempotent_replay"] is True
    assert len(core_state.jobs) == 1


async def test_plan_upgrade_required_is_structured(
    mcp_client: Callable[..., Any], core_state: FakeCoreState
) -> None:
    core_state.trial_used.add(
        "acct_" + __import__("hashlib").sha256(("vpt_" + "a" * 24).encode()).hexdigest()[:12]
    )
    async with await mcp_client() as client:
        is_error, payload = await call(client, "optimize_delivery_routes", sample_arguments(stops=400))
    assert is_error
    error = payload["error"]
    assert error["code"] == "PLAN_UPGRADE_REQUIRED"
    assert (
        error["message"]
        == "Your current Vepathos plan allows up to 150 stops per optimization. This request contains 400 stops."
    )
    assert error["details"]["upgrade_url"].endswith("source=mcp")
    assert error["retryable"] is False


async def test_trial_applied_is_reported(mcp_client: Callable[..., Any]) -> None:
    args = sample_arguments(stops=80)
    args["schedule"]["route_start_time"] = "08:00"
    args["stops"][0]["time_window"] = {"start": "09:00", "end": "11:00"}
    async with await mcp_client() as client:
        is_error, created = await call(client, "optimize_delivery_routes", args)
    assert not is_error, created
    assert created["full_trial_applied"]["max_stops"] == 2000
    assert created["full_trial_applied"]["quota_charged"] is False


async def test_quota_and_concurrency_errors(
    mcp_client: Callable[..., Any], core_state: FakeCoreState
) -> None:
    core_state.plan = FakePlan(max_stops_per_request=None, monthly_stops=20, max_concurrent=1)
    async with await mcp_client() as client:
        is_error, payload = await call(client, "optimize_delivery_routes", sample_arguments(stops=30))
        assert is_error and payload["error"]["code"] == "QUOTA_EXCEEDED"

        _, first = await call(client, "optimize_delivery_routes", sample_arguments(stops=3))
        args = sample_arguments(stops=4)
        is_error, payload = await call(client, "optimize_delivery_routes", args)
    assert is_error and payload["error"]["code"] == "CONCURRENT_OPTIMIZATION_LIMIT"
    assert payload["error"]["retryable"] is True
    assert payload["error"]["details"]["active_optimization_ids"] == [first["optimization_id"]]


async def test_failed_optimization_and_unknown_id(mcp_client: Callable[..., Any], clock: FakeClock) -> None:
    args = sample_arguments(stops=3)
    args["stops"][0]["stop_id"] = "FAIL-1"
    async with await mcp_client() as client:
        _, created = await call(client, "optimize_delivery_routes", args)
        clock.now += 10
        is_error, payload = await call(
            client, "get_optimization_result", {"optimization_id": created["optimization_id"]}
        )
        assert is_error and payload["error"]["code"] == "OPTIMIZATION_FAILED"

        is_error, payload = await call(
            client, "get_optimization_result", {"optimization_id": "mcp_" + "0" * 32}
        )
    assert is_error and payload["error"]["code"] == "OPTIMIZATION_NOT_FOUND"


async def test_geocode_addresses_returns_pins_from_smart_import_contract(
    mcp_client: Callable[..., Any],
) -> None:
    async with await mcp_client() as client:
        is_error, created = await call(
            client,
            "geocode_addresses",
            {
                "addresses": [
                    {"stop_id": "A1", "address": "Av. Corrientes 1000", "city": "CABA", "country": "AR"}
                ],
                "city": "Buenos Aires",
                "country": "AR",
            },
        )
        assert is_error is False
        assert created["geocode_id"].startswith("mcpg_")
        is_error, payload = await call(
            client, "get_geocode_result", {"geocode_id": created["geocode_id"]}
        )
    assert is_error is False
    assert payload["status"] == "completed"
    assert payload["stops"][0]["stop_id"] == "A1"
    assert payload["stops"][0]["latitude"] is not None


async def test_validation_errors_are_structured(mcp_client: Callable[..., Any]) -> None:
    args = sample_arguments(stops=2, return_to_depot=True)
    async with await mcp_client() as client:
        is_error, payload = await call(client, "optimize_delivery_routes", args)
    assert is_error
    assert payload["error"]["code"] == "INVALID_INPUT"
    assert payload["error"]["details"]["issues"][0]["path"] == "return_to_depot"

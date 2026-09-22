"""End-to-end tool behaviour through the MCP SDK client against the fake Core channel."""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from typing import Any

import pytest
from devtools.fake_core.app import FakeCoreState, FakePlan
from mcp.client import Client

from tests.conftest import FakeClock, make_settings, sample_arguments, tool_arguments
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
    if tool == "optimize_routes":
        arguments = tool_arguments(arguments)
    result = await client.call_tool(tool, arguments)
    assert isinstance(result.structured_content, dict)
    return result.is_error, result.structured_content


async def test_tools_list_publishes_annotations_and_strict_schemas(mcp_client: Callable[..., Any]) -> None:
    async with await mcp_client() as client:
        listed = await client.list_tools()
    tools = {t.name: t for t in listed.tools}
    # Import/dataset tools stay unpublished unless MCP_IMPORT_TOOLS_ENABLED (tests/contract/test_import_tools.py).
    assert set(tools) == {
        "optimize_routes",
        "get_optimization_result",
        "list_plans",
        "geocode_addresses",
        "get_geocode_result",
        "get_account",
        "list_fleet",
        "list_automations",
        "create_automation",
    }
    plans = tools["list_plans"]
    assert plans.annotations is not None and plans.annotations.read_only_hint is True
    assert plans.input_schema["additionalProperties"] is False
    account = tools["get_account"]
    assert account.annotations is not None and account.annotations.read_only_hint is True
    assert account.input_schema["additionalProperties"] is False
    assert not account.input_schema.get("required")

    optimize = tools["optimize_routes"]
    assert optimize.annotations is not None
    # A run can replace the oldest plan in a full library (plan_replaced), so it is not called harmless.
    assert optimize.annotations.read_only_hint is False and optimize.annotations.destructive_hint is True
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
        is_error, created = await call(client, "optimize_routes", sample_arguments(stops=12))
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
        is_error, created = await call(client, "optimize_routes", sample_arguments(stops=6))
    assert not is_error
    assert created["status"] == "completed"
    assert created["result"]["summary"]["stops_assigned"] == 6


async def test_long_poll_waits_for_completion(mcp_client: Callable[..., Any]) -> None:
    async with await mcp_client(MCP_RESULT_LONGPOLL_SECONDS=20) as client:
        _, created = await call(client, "optimize_routes", sample_arguments(stops=4))
        is_error, result = await call(
            client, "get_optimization_result", {"optimization_id": created["optimization_id"]}
        )
    assert not is_error and result["status"] == "completed"


async def test_identical_arguments_return_the_same_optimization(
    mcp_client: Callable[..., Any], core_state: FakeCoreState
) -> None:
    async with await mcp_client() as client:
        _, first = await call(client, "optimize_routes", sample_arguments(stops=5))
        _, second = await call(client, "optimize_routes", sample_arguments(stops=5))
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
        is_error, payload = await call(client, "optimize_routes", sample_arguments(stops=400))
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
        is_error, created = await call(client, "optimize_routes", args)
    assert not is_error, created
    assert created["full_trial_applied"]["max_stops"] == 2000
    assert created["full_trial_applied"]["quota_charged"] is False


async def test_quota_and_concurrency_errors(
    mcp_client: Callable[..., Any], core_state: FakeCoreState
) -> None:
    core_state.plan = FakePlan(max_stops_per_request=None, monthly_stops=20, max_concurrent=1)
    async with await mcp_client() as client:
        is_error, payload = await call(client, "optimize_routes", sample_arguments(stops=30))
        assert is_error and payload["error"]["code"] == "QUOTA_EXCEEDED"

        _, first = await call(client, "optimize_routes", sample_arguments(stops=3))
        args = sample_arguments(stops=4)
        is_error, payload = await call(client, "optimize_routes", args)
    assert is_error and payload["error"]["code"] == "CONCURRENT_OPTIMIZATION_LIMIT"
    assert payload["error"]["retryable"] is True
    assert payload["error"]["details"]["active_optimization_ids"] == [first["optimization_id"]]


async def test_failed_optimization_and_unknown_id(mcp_client: Callable[..., Any], clock: FakeClock) -> None:
    args = sample_arguments(stops=3)
    args["stops"][0]["stop_id"] = "FAIL-1"
    async with await mcp_client() as client:
        _, created = await call(client, "optimize_routes", args)
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
        is_error, payload = await call(client, "get_geocode_result", {"geocode_id": created["geocode_id"]})
    assert is_error is False
    assert payload["status"] == "completed"
    assert payload["stops"][0]["stop_id"] == "A1"
    assert payload["stops"][0]["latitude"] is not None
    assert payload.get("needs_confirmation") is False


async def test_validation_errors_are_structured(mcp_client: Callable[..., Any]) -> None:
    args = sample_arguments(stops=2, return_to_depot=True)
    async with await mcp_client() as client:
        is_error, payload = await call(client, "optimize_routes", args)
    assert is_error
    assert payload["error"]["code"] == "INVALID_INPUT"
    assert payload["error"]["details"]["issues"][0]["path"] == "return_to_depot"


async def test_get_account_reports_the_connected_account_and_its_limits(
    mcp_client: Callable[..., Any], core_state: FakeCoreState
) -> None:
    core_state.company_name = "Stormtech SRL"
    async with await mcp_client() as client:
        is_error, payload = await call(client, "get_account", {})
    assert not is_error, payload
    assert payload["account_label"] == "Stormtech SRL"
    assert payload["plan"]["name"] == "Free" and payload["plan"]["max_stops_per_request"] == 150
    assert payload["usage"]["stops_limit"] == 2000 and payload["usage"]["stops_remaining"] == 2000


async def test_get_account_masks_the_email_when_there_is_no_company(
    mcp_client: Callable[..., Any],
) -> None:
    async with await mcp_client() as client:
        is_error, payload = await call(client, "get_account", {})
    assert not is_error, payload
    assert payload["account_label"].startswith("a***@") and "@example.test" in payload["account_label"]


async def test_get_account_rejects_arguments(mcp_client: Callable[..., Any]) -> None:
    async with await mcp_client() as client:
        is_error, payload = await call(client, "get_account", {"account_id": "acct_other"})
    assert is_error and payload["error"]["code"] == "INVALID_INPUT"


async def test_plan_error_names_the_connected_account(
    mcp_client: Callable[..., Any], core_state: FakeCoreState
) -> None:
    core_state.company_name = "Stormtech SRL"
    core_state.trial_used.add(
        "acct_" + __import__("hashlib").sha256(("vpt_" + "a" * 24).encode()).hexdigest()[:12]
    )
    async with await mcp_client() as client:
        is_error, payload = await call(client, "optimize_routes", sample_arguments(stops=400))
    assert is_error
    error = payload["error"]
    assert error["details"]["connected_account"] == "Stormtech SRL"
    assert "Stormtech SRL" in error["suggestion"] and "get_account" in error["suggestion"]


async def test_list_fleet_returns_the_account_catalog(
    mcp_client: Callable[..., Any], core_state: FakeCoreState
) -> None:
    core_state.fleets = [
        {
            "fleet_id": "7",
            "name": "Reparto AM",
            "total_units": 3,
            "vehicles": [
                {"vehicle_id": "v1", "name": "Sprinter", "count": 2, "max_weight_kg": 1200.0},
                {"vehicle_id": "v2", "name": "KIA", "count": 1, "max_volume_m3": 6.0},
            ],
        }
    ]
    async with await mcp_client() as client:
        is_error, payload = await call(client, "list_fleet", {})
    assert not is_error, payload
    assert payload["fleets"][0]["name"] == "Reparto AM"
    assert [v["vehicle_id"] for v in payload["fleets"][0]["vehicles"]] == ["v1", "v2"]
    assert payload["fleets"][0]["vehicles"][0]["max_weight_kg"] == 1200.0
    assert "empty" not in payload


async def test_list_fleet_flags_an_account_with_no_fleet(mcp_client: Callable[..., Any]) -> None:
    async with await mcp_client() as client:
        is_error, payload = await call(client, "list_fleet", {})
    assert not is_error, payload
    assert payload["empty"] is True


async def test_list_fleet_rejects_arguments(mcp_client: Callable[..., Any]) -> None:
    async with await mcp_client() as client:
        is_error, payload = await call(client, "list_fleet", {"fleet_id": "7"})
    assert is_error and payload["error"]["code"] == "INVALID_INPUT"


async def test_unconfirmed_optimize_returns_a_preflight_and_charges_nothing(
    mcp_client: Callable[..., Any], core_state: FakeCoreState
) -> None:
    args = sample_arguments(stops=12, confirmed=False)
    async with await mcp_client() as client:
        is_error, payload = await call(client, "optimize_routes", args)
    assert not is_error
    # No job reached Core, so no stop was spent deciding whether to spend stops.
    assert not core_state.jobs
    assert "optimization_id" not in payload
    preflight = payload["preflight"]
    assert preflight["stops"] == 12 and preflight["charges_stops"] == 12
    assert preflight["vehicle_units"] == 3
    assert preflight["constraints_enforced"] == []
    assert preflight["plan"]["stops_remaining_after"] == preflight["plan"]["stops_remaining"] - 12
    assert "confirmed=true" in preflight["confirm_with"]


async def test_preflight_names_the_constraints_and_totals_it_would_enforce(
    mcp_client: Callable[..., Any],
) -> None:
    args = sample_arguments(stops=2, confirmed=False)
    args["vehicles"] = [{"vehicle_id": "van", "count": 1, "max_weight_kg": 900}]
    args["stops"][0]["weight_kg"] = 1.5
    args["stops"][1]["weight_kg"] = 2.25
    args["stops"][0]["time_window"] = {"start": "09:00", "end": "12:00"}
    args["schedule"]["route_start_time"] = "08:00"
    async with await mcp_client() as client:
        _, payload = await call(client, "optimize_routes", args)
    preflight = payload["preflight"]
    assert preflight["constraints_enforced"] == ["weight_capacity", "time_windows"]
    assert preflight["total_weight_kg"] == 3.75
    # exclude_none: a quantity no stop declares is absent, not zero.
    assert "total_volume_m3" not in preflight
    assert preflight["stops_with_time_window"] == 1
    # The fake Core's plan carries no premium features, so the request would be rejected.
    assert preflight["plan"]["fits"] is False
    assert preflight["plan"]["missing_features"] == ["weight_capacity", "time_windows"]


async def test_preflight_flags_a_request_over_the_plan_maximum(mcp_client: Callable[..., Any]) -> None:
    async with await mcp_client() as client:
        _, payload = await call(client, "optimize_routes", sample_arguments(stops=400, confirmed=False))
    plan = payload["preflight"]["plan"]
    assert plan["fits"] is False and plan["max_stops_per_request"] == 150


async def test_variants_of_one_day_share_a_stops_identity(mcp_client: Callable[..., Any]) -> None:
    first = sample_arguments(stops=5, confirmed=False)
    second = sample_arguments(stops=5, confirmed=False)
    second["vehicles"] = [{"vehicle_id": "van", "count": 2, "max_stops": 3}]
    async with await mcp_client() as client:
        _, a = await call(client, "optimize_routes", first)
        _, b = await call(client, "optimize_routes", second)
    # Same delivery day, different routing: the pair Core would charge twice.
    assert a["preflight"]["stops_identity"] == b["preflight"]["stops_identity"]
    assert a["preflight"]["vehicle_units"] == 3 and b["preflight"]["vehicle_units"] == 2


async def test_the_gate_can_be_turned_off_for_unattended_callers(
    mcp_client: Callable[..., Any], core_state: FakeCoreState
) -> None:
    args = sample_arguments(stops=4)
    del args["confirmed"]
    async with await mcp_client(MCP_CONFIRM_BEFORE_OPTIMIZE="false") as client:
        is_error, payload = await call(client, "optimize_routes", args)
    assert not is_error and payload["optimization_id"].startswith("mcp_")
    assert len(core_state.jobs) == 1


async def test_a_client_with_a_cached_schema_still_optimizes_without_the_gate(
    mcp_client: Callable[..., Any], core_state: FakeCoreState
) -> None:
    # ChatGPT caches tool schemas and kept sending confirmed=false after the gate was turned off
    # (2026-09-16). Rejecting the field would have broken optimization for every such client.
    args = sample_arguments(stops=4, confirmed=False)
    async with await mcp_client(MCP_CONFIRM_BEFORE_OPTIMIZE="false") as client:
        is_error, payload = await call(client, "optimize_routes", args)
    assert not is_error and payload["optimization_id"].startswith("mcp_")
    assert "preflight" not in payload
    assert len(core_state.jobs) == 1


async def test_the_published_contract_follows_the_gate(mcp_client: Callable[..., Any]) -> None:
    async with await mcp_client() as client:
        gated = {t.name: t for t in (await client.list_tools()).tools}["optimize_routes"]
    async with await mcp_client(MCP_CONFIRM_BEFORE_OPTIMIZE="false") as client:
        direct = {t.name: t for t in (await client.list_tools()).tools}["optimize_routes"]

    assert "confirmed" in gated.input_schema["properties"]
    assert "confirmed=true" in (gated.description or "")
    # Without the gate the field changes nothing, so advertising it only invites a second call.
    assert "confirmed" not in direct.input_schema["properties"]
    assert "confirmed" not in (direct.description or "")
    assert direct.input_schema["additionalProperties"] is False


async def test_server_instructions_follow_the_gate(
    core_client_factory: Callable[..., VepathosApiClient], clock: FakeClock
) -> None:
    for gate, describes_two_calls in (("true", True), ("false", False)):
        core = core_client_factory()
        server = build_server(
            make_settings(MCP_TRANSPORT="stdio", MCP_CONFIRM_BEFORE_OPTIMIZE=gate),
            core,
            sleep=clock.sleep,
            clock=clock,
        )
        assert ("confirmed=true" in (server.instructions or "")) is describes_two_calls
        await core.aclose()


async def test_an_inline_run_is_saved_as_a_plan_the_account_can_open(
    mcp_client: Callable[..., Any], clock: FakeClock
) -> None:
    args = sample_arguments(stops=4, plan_name="Lunes zona 1", depot_name="Galpón")
    async with await mcp_client() as client:
        is_error, created = await call(client, "optimize_routes", args)
        assert not is_error, created
        assert created["plan_id"].startswith("cmf") and created["plan_name"] == "Lunes zona 1"
        assert created["account_url"].endswith(f"plan={created['plan_id']}")
        assert created["quota_charged"] is True and "free_retry" not in created
        assert "plan_replaced" not in created and "plan_temporary" not in created

        is_error, pending = await call(
            client, "get_optimization_result", {"optimization_id": created["optimization_id"]}
        )
        assert not is_error and pending["status"] != "completed"
        assert pending["plan_id"] == created["plan_id"] and pending["account_url"] == created["account_url"]

        clock.now += 10
        is_error, result = await call(
            client, "get_optimization_result", {"optimization_id": created["optimization_id"]}
        )
        assert not is_error, result
        # Kept with the plan: no expiry, and the run is addressable in the account.
        assert result["plan_id"] == created["plan_id"] and result["history_id"].startswith("hist_")
        assert "history=" in result["account_url"] and "expires_at" not in result

        is_error, listed = await call(client, "list_plans", {})
        assert not is_error, listed
        row = next(p for p in listed["plans"] if p["plan_id"] == created["plan_id"])
        assert row["name"] == "Lunes zona 1" and row["stops"] == 4 and row["created_by"] == "agent"
        assert row["depot"] == {"name": "Galpón", "latitude": -34.6037, "longitude": -58.3816}
        assert row["next_optimize_charged"] is False and row["free_retries_remaining"] == 1
        assert "last_agent_run" not in row
        assert listed["library"] == {
            "plans_in_library": 1,
            "max_plans": 3,
            "kept_plans": 0,
            "max_kept_plans": 2,
        }

        is_error, one = await call(client, "list_plans", {"plan_id": created["plan_id"]})
    assert not is_error, one
    assert one["plan"]["last_agent_run"]["vehicles"] == [{"id": "van", "count": 3}]
    assert one["plan"]["with_time_window"] == 0 and one["plan"]["free_retry_window_ends_at"]


async def test_a_full_library_replaces_the_oldest_plan_and_says_which(
    mcp_client: Callable[..., Any], core_state: FakeCoreState, clock: FakeClock
) -> None:
    async with await mcp_client() as client:
        created = []
        for index in range(4):
            args = sample_arguments(stops=2 + index, plan_name=f"Día {index + 1}")
            is_error, run = await call(client, "optimize_routes", args)
            assert not is_error, run
            created.append(run)
            clock.now += 10
    first, fourth = created[0], created[3]
    assert "plan_replaced" not in created[2]
    assert fourth["plan_replaced"] == {"plan_id": first["plan_id"], "name": "Día 1"}

    async with await mcp_client() as client:
        is_error, gone = await call(client, "list_plans", {"plan_id": first["plan_id"]})
    assert is_error and gone["error"]["code"] == "PLAN_NOT_FOUND"
    assert "list_plans" in gone["error"]["suggestion"]


async def test_a_plan_is_temporary_when_every_library_slot_is_protected(
    mcp_client: Callable[..., Any], core_state: FakeCoreState, clock: FakeClock
) -> None:
    core_state.plan.max_saved_plans = 1
    async with await mcp_client() as client:
        _, kept = await call(client, "optimize_routes", sample_arguments(stops=2))
        core_state.plans[kept["plan_id"]]["kept"] = True
        clock.now += 10
        is_error, run = await call(client, "optimize_routes", sample_arguments(stops=3))
        assert not is_error, run
        assert run["plan_temporary"] is True and "plan_replaced" not in run
        _, listed = await call(client, "list_plans", {"query": "optimization"})
    temporary = next(p for p in listed["plans"] if p["plan_id"] == run["plan_id"])
    assert temporary["temporary"] is True


async def test_a_server_without_imports_reruns_a_plan_as_another_try(
    mcp_client: Callable[..., Any], clock: FakeClock
) -> None:
    # The free retry is only reachable through optimize_routes, so it is published on every server.
    async with await mcp_client(MCP_CONFIRM_BEFORE_OPTIMIZE="false") as client:
        tools = {t.name for t in (await client.list_tools()).tools}
        assert "optimize_routes" in tools and "import_deliveries" not in tools

        is_error, first = await call(client, "optimize_routes", sample_arguments(stops=5))
        assert not is_error, first
        clock.now += 10

        rerun = {
            "plan_id": first["plan_id"],
            "depot": {"latitude": -34.6037, "longitude": -58.3816},
            "vehicles": [{"vehicle_id": "van", "count": 2}],
            "exclude_stop_ids": ["ORD-00004"],
        }
        is_error, retry = await call(client, "optimize_routes", rerun)
        assert not is_error, retry
        assert retry["free_retry"] is True and retry["quota_charged"] is False
        assert retry["submitted_stops"] == 4 and retry["plan_id"] == first["plan_id"]

        clock.now += 10
        _, plan = await call(client, "list_plans", {"plan_id": first["plan_id"]})
    # The excluded stop shaped one run only: the plan keeps all five.
    assert plan["plan"]["stops"] == 5


async def test_automations_are_read_and_prepared_but_never_switched_on(
    mcp_client: Callable[..., Any], core_state: FakeCoreState
) -> None:
    """The whole point of the pair: an agent may set a rule up, and cannot start it."""

    core_state.stores = [
        {"integration_account_id": "acc-1", "kind": "mercadolibre", "name": "Mi tienda", "last_sync_at": None}
    ]
    async with await mcp_client() as client:
        # Nothing yet: the account's stores are still worth answering, they are how a rule gets fed.
        _, empty = await call(client, "list_automations", {})
        assert empty["empty"] is True
        assert empty["stores"][0]["integration_account_id"] == "acc-1"

        _, plan = await call(client, "optimize_routes", sample_arguments(stops=2))
        is_error, created = await call(
            client,
            "create_automation",
            {
                "name": "Reparto de la mañana",
                "template_plan_id": plan["plan_id"],
                "integration_account_id": "acc-1",
                "looks_at": "08:00",
                "days": [1, 2, 3, 4, 5],
                "min_orders": 5,
                "mode": "auto",
                "operation_id": "chat-draft-0001",
            },
        )
        assert not is_error, created
        # Switched off, and what a run would still lack is said now rather than discovered by the
        # scheduler: a plan an agent made carries an ad-hoc depot, which a run refuses.
        assert created["enabled"] is False
        assert created["automation"]["status"] == "draft"
        assert created["missing"] == ["depot"]
        assert created["account_url"].endswith(created["automation"]["automation_id"])
        # Its own plan is not an id an agent may hold: optimizing it by hand would spend the rule's stops.
        assert "plan_id" not in created["automation"]

        # The same attempt twice is one rule, not two.
        _, again = await call(
            client,
            "create_automation",
            {
                "name": "Reparto de la mañana",
                "template_plan_id": plan["plan_id"],
                "integration_account_id": "acc-1",
                "looks_at": "08:00",
                "operation_id": "chat-draft-0001",
            },
        )
        assert again["automation"]["automation_id"] == created["automation"]["automation_id"]

        _, listed = await call(client, "list_automations", {})
        assert len(listed["automations"]) == 1
        rule = listed["automations"][0]
        # One time of day is reported as one time, not as a window that starts and ends together.
        assert rule["looks_at"] == "08:00" and "window_from" not in rule
        assert rule["enabled"] is False


async def test_an_automation_needs_to_be_told_where_its_orders_come_from(
    mcp_client: Callable[..., Any], core_state: FakeCoreState
) -> None:
    async with await mcp_client() as client:
        _, plan = await call(client, "optimize_routes", sample_arguments(stops=2))
        base = {"name": "Sin origen", "template_plan_id": plan["plan_id"], "operation_id": "chat-draft-0002"}
        is_error, refused = await call(client, "create_automation", base)
        assert is_error and refused["error"]["code"] == "INVALID_INPUT"

        # A window that ends before it starts is not a window.
        is_error, backwards = await call(
            client,
            "create_automation",
            {**base, "use_plan_stops": True, "window_from": "14:00", "window_to": "10:00"},
        )
        assert is_error and backwards["error"]["code"] == "INVALID_INPUT"

        # A plan that is not in the account cannot be the template.
        is_error, gone = await call(
            client,
            "create_automation",
            {**base, "use_plan_stops": True, "template_plan_id": "plan_nope"},
        )
        assert is_error and gone["error"]["code"] == "PLAN_NOT_FOUND"


async def test_an_account_without_automations_is_told_so_before_anything_is_written(
    mcp_client: Callable[..., Any], core_state: FakeCoreState
) -> None:
    core_state.max_enabled_automations = 0
    async with await mcp_client() as client:
        _, plan = await call(client, "optimize_routes", sample_arguments(stops=2))
        is_error, refused = await call(
            client,
            "create_automation",
            {
                "name": "No incluida",
                "template_plan_id": plan["plan_id"],
                "use_plan_stops": True,
                "operation_id": "chat-draft-0003",
            },
        )
    assert is_error and refused["error"]["code"] == "AUTOMATION_NOT_INCLUDED"
    assert refused["error"]["retryable"] is False
    assert not core_state.automations, "nothing is written for an account that could never switch it on"


CATALOG_WRITES = {"MCP_CATALOG_WRITE_TOOLS_ENABLED": "true"}
SPRINTER = {
    "resource": "vehicle",
    "action": "create",
    "name": "Sprinter",
    "max_weight_kg": 1500,
    "max_volume_m3": 14,
}


async def test_a_saved_vehicle_shows_in_the_fleet_and_asking_again_writes_nothing(
    mcp_client: Callable[..., Any], core_state: FakeCoreState
) -> None:
    async with await mcp_client(**CATALOG_WRITES) as client:
        is_error, created = await call(client, "manage_catalog", SPRINTER)
        assert not is_error, created
        assert created["outcome"] == "created" and created["vehicle"]["max_weight_kg"] == 1500
        assert created["account_url"].endswith("/vehicles")

        _, fleet = await call(client, "list_fleet", {})
        assert [v["name"] for v in fleet["vehicles"]] == ["Sprinter"]

        # A retry, or a model asking twice with another spelling, converges on the same row.
        again = {**SPRINTER, "name": "sprinter"}
        is_error, replay = await call(client, "manage_catalog", again)
        assert not is_error and replay["outcome"] == "already_existed"
        assert replay["vehicle"]["vehicle_id"] == created["vehicle"]["vehicle_id"]
    assert len(core_state.vehicles) == 1


async def test_a_name_in_use_with_other_values_is_the_users_call(
    mcp_client: Callable[..., Any], core_state: FakeCoreState
) -> None:
    async with await mcp_client(**CATALOG_WRITES) as client:
        await call(client, "manage_catalog", SPRINTER)
        other = {"resource": "vehicle", "action": "create", "name": "Sprinter", "max_weight_kg": 900}
        is_error, payload = await call(client, "manage_catalog", other)
    assert is_error and payload["error"]["code"] == "NAME_TAKEN"
    assert payload["error"]["details"]["existing"]["max_weight_kg"] == 1500
    assert len(core_state.vehicles) == 1


async def test_a_saved_vehicle_is_updated_by_the_id_list_fleet_gave(
    mcp_client: Callable[..., Any], core_state: FakeCoreState
) -> None:
    async with await mcp_client(**CATALOG_WRITES) as client:
        _, created = await call(client, "manage_catalog", SPRINTER)
        vehicle_id = created["vehicle"]["vehicle_id"]
        update = {"resource": "vehicle", "action": "update", "resource_id": vehicle_id, "max_volume_m3": 12}
        is_error, updated = await call(client, "manage_catalog", update)
        assert not is_error, updated
        assert updated["outcome"] == "updated" and updated["vehicle"]["max_volume_m3"] == 12
        assert updated["vehicle"]["max_weight_kg"] == 1500

        missing = {"resource": "vehicle", "action": "update", "resource_id": "999", "name": "x"}
        is_error, payload = await call(client, "manage_catalog", missing)
        assert is_error and payload["error"]["code"] == "VEHICLE_NOT_FOUND"


async def test_the_plans_catalog_cap_is_said_before_anything_is_written(
    mcp_client: Callable[..., Any], core_state: FakeCoreState
) -> None:
    core_state.max_vehicles = 0
    async with await mcp_client(**CATALOG_WRITES) as client:
        is_error, payload = await call(client, "manage_catalog", SPRINTER)
    assert is_error and payload["error"]["code"] == "PLAN_UPGRADE_REQUIRED"
    assert payload["error"]["details"]["reason"] == "CATALOG_VEHICLE_LIMIT"
    assert core_state.vehicles == []


async def test_use_25_vehicles_cannot_become_25_saved_vehicles(
    mcp_client: Callable[..., Any], core_state: FakeCoreState
) -> None:
    async with await mcp_client(**CATALOG_WRITES) as client:
        for arguments in (
            {**SPRINTER, "count": 25},
            {"resource": "vehicle", "action": "create", "name": "S", "available": False},
        ):
            is_error, payload = await call(client, "manage_catalog", arguments)
            assert is_error and payload["error"]["code"] == "INVALID_INPUT"
    assert core_state.vehicles == []


async def test_a_saved_depot_is_read_back_with_coordinates_an_optimization_takes(
    mcp_client: Callable[..., Any], core_state: FakeCoreState
) -> None:
    barracas = {
        "resource": "depot",
        "action": "create",
        "name": "Barracas",
        "latitude": -34.6441,
        "longitude": -58.3816,
    }
    async with await mcp_client(**CATALOG_WRITES) as client:
        is_error, created = await call(client, "manage_catalog", barracas)
        assert not is_error, created
        assert created["outcome"] == "created" and created["account_url"].endswith("/milestones")

        _, fleet = await call(client, "list_fleet", {})
        assert fleet["depots"] == [created["depot"]]
        assert fleet["depots"][0]["latitude"] == -34.6441

        # The same place under the same name is the same depot; another place is not.
        nearby = {**barracas, "latitude": -34.64412}
        _, replay = await call(client, "manage_catalog", nearby)
        assert replay["outcome"] == "already_existed"
        elsewhere = {**barracas, "latitude": -34.9}
        is_error, payload = await call(client, "manage_catalog", elsewhere)
        assert is_error and payload["error"]["code"] == "NAME_TAKEN"

        rename = {
            "resource": "depot",
            "action": "update",
            "resource_id": created["depot"]["depot_id"],
            "name": "Barracas Sur",
        }
        _, renamed = await call(client, "manage_catalog", rename)
        assert renamed["outcome"] == "updated" and renamed["depot"]["name"] == "Barracas Sur"
    assert len(core_state.depots) == 1


async def test_depots_are_read_on_every_server_but_written_only_behind_the_flag(
    mcp_client: Callable[..., Any], core_state: FakeCoreState
) -> None:
    core_state.depots = [{"depot_id": "3", "name": "Barracas", "latitude": -34.6441, "longitude": -58.3816}]
    async with await mcp_client() as client:
        names = {t.name for t in (await client.list_tools()).tools}
        _, fleet = await call(client, "list_fleet", {})
    assert "manage_catalog" not in names
    assert fleet["depots"][0]["name"] == "Barracas"


async def test_tools_that_overwrite_something_saved_say_so(mcp_client: Callable[..., Any]) -> None:
    async with await mcp_client(**CATALOG_WRITES) as client:
        tools = {t.name: t for t in (await client.list_tools()).tools}
    for name in ("optimize_routes", "manage_catalog"):
        annotations = tools[name].annotations
        assert annotations is not None
        assert annotations.destructive_hint is True and annotations.read_only_hint is False, name
    for name in ("list_fleet", "get_account", "create_automation", "geocode_addresses"):
        annotations = tools[name].annotations
        assert annotations is not None and annotations.destructive_hint is False, name


async def test_only_the_tool_that_fetches_a_callers_url_is_open_world(mcp_client: Callable[..., Any]) -> None:
    async with await mcp_client(
        MCP_IMPORT_TOOLS_ENABLED="true", MCP_MAP_SHARES_ENABLED="true", **CATALOG_WRITES
    ) as client:
        tools = {t.name: t for t in (await client.list_tools()).tools}
    open_world = {
        name for name, tool in tools.items() if tool.annotations and tool.annotations.open_world_hint
    }
    assert open_world == {"import_deliveries"}


async def test_the_route_duration_is_published_as_the_target_it_is(mcp_client: Callable[..., Any]) -> None:
    # Local smoke, 2026-09-21: the field said "maximum", the agent promised no route over 60 minutes, and 7
    # of 18 ran 67-70. Core sends it as rebalance_by_time: a soft target. The text must not promise more.
    async with await mcp_client() as client:
        tools = {t.name: t for t in (await client.list_tools()).tools}
    for text in (tools["optimize_routes"].input_schema["properties"]["max_route_minutes"]["description"],):
        assert "not a hard limit" in text.replace("not \n", "not ") and "target" in text
        assert "Maximum" not in text

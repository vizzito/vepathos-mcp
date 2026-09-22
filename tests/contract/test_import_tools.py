"""Import / dataset tools against fake Core."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Callable
from typing import Any

import pytest
from mcp.client import Client

from tests.conftest import FakeClock, make_settings, tool_arguments
from vepathos_mcp.clients.public_fetch import PublicFetchError
from vepathos_mcp.clients.vepathos_api import VepathosApiClient
from vepathos_mcp.server import build_server
from vepathos_mcp.tools import import_tools


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
    if tool == "optimize_routes":
        arguments = tool_arguments(arguments)
    result = await client.call_tool(tool, arguments)
    assert isinstance(result.structured_content, dict)
    return result.is_error, result.structured_content


async def test_import_file_missing_attachment_is_invalid_input(mcp_client: Callable[..., Any]) -> None:
    async with await mcp_client() as client:
        is_error, payload = await call(client, "import_deliveries", {})
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
            client, "import_deliveries", {"file": {"download_url": download_url, "file_name": "a.xlsx"}}
        )
    assert is_error
    assert payload["error"]["code"] == "INVALID_INPUT"
    assert payload["error"]["details"]["reason"] in {"blocked_host", "invalid_url"}


@pytest.mark.parametrize(
    ("failure", "message_part"),
    [
        (PublicFetchError("http_status", 403), "HTTP 403"),
        (PublicFetchError("too_large"), "too large"),
        (PublicFetchError("network"), "Could not download"),
    ],
)
async def test_import_file_asks_to_attach_again_when_the_download_fails(
    mcp_client: Callable[..., Any],
    monkeypatch: pytest.MonkeyPatch,
    failure: PublicFetchError,
    message_part: str,
) -> None:
    # An expired ChatGPT download URL answers 403; a file over 8 MiB is too large.
    async def fail(url: str, *, max_bytes: int) -> tuple[bytes, str | None]:
        raise failure

    monkeypatch.setattr(import_tools, "fetch_public_https", fail)
    async with await mcp_client() as client:
        is_error, payload = await call(
            client,
            "import_deliveries",
            {"file": {"download_url": "https://files.example.com/a.xlsx", "file_name": "a.xlsx"}},
        )
    assert is_error
    assert payload["error"]["code"] == "INVALID_INPUT"
    assert message_part in payload["error"]["message"]
    assert "attach" in (payload["error"].get("suggestion") or "").lower()


async def test_import_text_then_optimize_dataset(mcp_client: Callable[..., Any]) -> None:
    async with await mcp_client() as client:
        is_error, created = await call(
            client,
            "import_deliveries",
            {"text": "id,address\n1,Calle Falsa 123\n2,Av Siempre Viva 742"},
        )
        assert not is_error, created
        assert created["import_id"] and created["dataset_id"] and created["plan_id"]
        assert created["account_url"].endswith(f"plan={created['plan_id']}")
        is_error, status = await call(client, "get_import_result", {"import_id": created["import_id"]})
        assert not is_error and status["status"] == "completed"
        assert "stops" in status["summary"]
        assert "rows" not in status  # never dump all rows
        assert status["plan_id"] == created["plan_id"]
        assert status["account_url"].endswith(f"plan={created['plan_id']}")
        assert status["next_optimize_charged"] is True and status["charged_because"] == "no_billed_run"
        assert "free_replans_remaining" not in status
        is_error, listed = await call(client, "list_datasets", {})
        assert not is_error
        row = next(d for d in listed["datasets"] if d["dataset_id"] == created["dataset_id"])
        assert row["plan_id"] == created["plan_id"]
        is_error, opt = await call(
            client,
            "optimize_routes",
            {
                "plan_id": created["plan_id"],
                "depot": {"latitude": -34.6, "longitude": -58.4},
                "vehicles": [{"vehicle_id": "van", "count": 1, "max_stops": 20}],
            },
        )
        assert not is_error, opt
        assert opt["optimization_id"].startswith("mcp_")
        assert opt["submitted_stops"] == 5 and opt["plan_id"] == created["plan_id"]


DEPOT = {"latitude": -34.6, "longitude": -58.4}
VEHICLES = [{"vehicle_id": "van", "count": 1, "max_stops": 20}]


async def _import(client: Client, **extra: Any) -> dict[str, Any]:
    is_error, created = await call(
        client, "import_deliveries", {"text": "id,address\n1,Calle Falsa 123", **extra}
    )
    assert not is_error, created
    return created


async def test_optimize_plan_by_dataset_id_runs_in_the_plan_the_import_loaded(
    mcp_client: Callable[..., Any],
) -> None:
    async with await mcp_client() as client:
        created = await _import(client)
        is_error, run = await call(
            client,
            "optimize_routes",
            {"dataset_id": created["dataset_id"], "depot": DEPOT, "vehicles": VEHICLES},
        )
    assert not is_error, run
    assert run["plan_id"] == created["plan_id"] and run["submitted_stops"] == 5


@pytest.mark.parametrize(
    "sources",
    [{}, {"plan_id": "cmf0000000000000000000001", "dataset_id": "mcp_ds_000000000000"}],
    ids=["neither", "both"],
)
async def test_optimize_plan_takes_exactly_one_source(
    mcp_client: Callable[..., Any], core_state: Any, sources: dict[str, str]
) -> None:
    async with await mcp_client() as client:
        is_error, payload = await call(
            client, "optimize_routes", {**sources, "depot": DEPOT, "vehicles": VEHICLES}
        )
    assert is_error and payload["error"]["code"] == "INVALID_INPUT"
    assert not core_state.jobs


async def test_a_plan_reruns_free_once_within_24_hours_with_the_same_stops_or_fewer(
    mcp_client: Callable[..., Any], clock: FakeClock
) -> None:
    async with await mcp_client() as client:
        plan_id = (await _import(client))["plan_id"]
        args = {"plan_id": plan_id, "depot": DEPOT, "vehicles": VEHICLES}
        is_error, first = await call(client, "optimize_routes", args)
        assert not is_error, first
        assert first["quota_charged"] is True and first["free_retries_remaining"] == 1
        assert "free_retry" not in first

        clock.now += 10  # the billed run completes; the plan runs one optimization at a time
        variant = {
            **args,
            "vehicles": [{"vehicle_id": "van", "count": 2, "max_stops": 20}],
            "exclude_stop_ids": ["S5"],
        }
        is_error, second = await call(client, "optimize_routes", variant)
        assert not is_error, second
        assert second["quota_charged"] is False and second["free_retry"] is True
        assert second["free_retries_remaining"] == 0

        clock.now += 10
        _, plan = await call(client, "list_plans", {"plan_id": plan_id})
        assert plan["plan"]["next_optimize_charged"] is True
        assert plan["plan"]["charged_because"] == "allowance_used"
        # exclude_stop_ids shapes one run: the plan keeps every stop.
        assert plan["plan"]["stops"] == 5

        is_error, third = await call(client, "optimize_routes", {**args, "exclude_stop_ids": ["S4"]})
    assert not is_error, third
    assert third["quota_charged"] is True and "free_retry" not in third


async def test_added_stops_or_a_closed_window_charge_the_rerun(
    mcp_client: Callable[..., Any], core_state: Any, clock: FakeClock
) -> None:
    async with await mcp_client(MCP_CONFIRM_BEFORE_OPTIMIZE="true") as client:
        plan_id = (await _import(client))["plan_id"]
        args = {"plan_id": plan_id, "depot": DEPOT, "vehicles": VEHICLES}
        is_error, first = await call(client, "optimize_routes", {**args, "confirmed": True})
        assert not is_error, first
        clock.now += 10

        core_state.plans[plan_id]["stops"].append({"id": "S9", "lat": -34.61, "lng": -58.41})
        is_error, preview = await call(client, "optimize_routes", args)
        assert not is_error, preview
        preflight = preview["preflight"]
        assert preflight["stops"] == 6 and preflight["charges_stops"] == 6
        assert preflight["charged_because"] == "stops_changed" and preflight["plan_id"] == plan_id
        assert "stops were added or moved" in preflight["confirm_with"]

        core_state.plans[plan_id]["stops"].pop()
        clock.now += 24 * 3600
        _, preview = await call(client, "optimize_routes", args)
    assert preview["preflight"]["charges_stops"] == 5
    assert preview["preflight"]["charged_because"] == "window_closed"


async def test_free_retry_still_has_to_fit_the_plan(
    mcp_client: Callable[..., Any], core_state: Any, clock: FakeClock
) -> None:
    async with await mcp_client() as client:
        plan_id = (await _import(client))["plan_id"]
        args = {"plan_id": plan_id, "depot": DEPOT, "vehicles": VEHICLES}
        is_error, first = await call(client, "optimize_routes", args)
        assert not is_error, first
        clock.now += 10
        core_state.plan.max_stops_per_request = 3  # the plan has 5 stops
        is_error, rejected = await call(client, "optimize_routes", {**args, "exclude_stop_ids": ["S5"]})
    assert is_error
    assert rejected["error"]["code"] == "PLAN_UPGRADE_REQUIRED"


async def test_plan_preflight_states_the_real_stops_and_charge(
    mcp_client: Callable[..., Any], clock: FakeClock
) -> None:
    async with await mcp_client(MCP_CONFIRM_BEFORE_OPTIMIZE="true") as client:
        plan_id = (await _import(client))["plan_id"]
        args = {"plan_id": plan_id, "depot": DEPOT, "vehicles": VEHICLES}
        is_error, preview = await call(client, "optimize_routes", {**args, "exclude_stop_ids": ["S1"]})
        assert not is_error, preview
        preflight = preview["preflight"]
        assert preflight["stops"] == 4 and preflight["charges_stops"] == 4
        assert preflight["plan"]["stops_remaining_after"] == preflight["plan"]["stops_remaining"] - 4
        assert preflight["stops_identity"] == f"plan:{plan_id}"
        assert "charges its stops" in preflight["confirm_with"]
        assert "within 24 h with the same stops or fewer is another try" in preflight["confirm_with"]

        is_error, ran = await call(client, "optimize_routes", {**args, "confirmed": True})
        assert not is_error and ran["quota_charged"] is True

        clock.now += 10
        is_error, retry = await call(client, "optimize_routes", {**args, "service_time_minutes": 5})
        assert not is_error, retry
        assert retry["preflight"]["stops"] == 5 and retry["preflight"]["charges_stops"] == 0
        assert "another try" in retry["preflight"]["confirm_with"].lower()
        assert retry["preflight"]["free_retry_window_ends_at"]


async def test_dataset_preflight_warns_before_a_run_core_would_reject(
    mcp_client: Callable[..., Any], core_state: Any
) -> None:
    async with await mcp_client(MCP_CONFIRM_BEFORE_OPTIMIZE="true") as client:
        dataset_id = (await _import(client))["dataset_id"]
        stops = core_state.datasets[dataset_id]["stops"]
        stops[0]["weight_kg"] = 2.0
        stops[1]["time_window"] = {"start": "09:00", "end": "12:00"}
        vehicles = [{"vehicle_id": "van", "count": 1, "max_weight_kg": 500}]
        is_error, preview = await call(
            client,
            "optimize_routes",
            {"dataset_id": dataset_id, "depot": DEPOT, "vehicles": vehicles, "use_weight": True},
        )
    assert not is_error, preview
    preflight = preview["preflight"]
    codes = {w["code"]: w for w in preflight["warnings"]}
    assert codes["stops_without_weight"]["count"] == 4
    assert "route_start_time_required" in codes
    assert preflight["constraints_enforced"] == ["weight_capacity", "time_windows"]


async def test_plan_preflight_warns_when_the_plan_cannot_run_now(
    mcp_client: Callable[..., Any], core_state: Any
) -> None:
    async with await mcp_client(MCP_CONFIRM_BEFORE_OPTIMIZE="true") as client:
        plan_id = (await _import(client))["plan_id"]
        is_error, ran = await call(
            client,
            "optimize_routes",
            {"plan_id": plan_id, "depot": DEPOT, "vehicles": VEHICLES, "confirmed": True},
        )
        assert not is_error, ran
        # Still running: Core would answer PLAN_BUSY.
        is_error, preview = await call(
            client,
            "optimize_routes",
            {"plan_id": plan_id, "depot": DEPOT, "vehicles": VEHICLES, "date": "2026-09-18"},
        )
    assert not is_error, preview
    assert "plan_optimizing" in {w["code"] for w in preview["preflight"]["warnings"]}


async def test_dataset_flags_keep_capacities_and_windows_for_reference(
    mcp_client: Callable[..., Any], core_state: Any
) -> None:
    async with await mcp_client(MCP_CONFIRM_BEFORE_OPTIMIZE="true") as client:
        plan_id = (await _import(client))["plan_id"]
        core_state.plans[plan_id]["stops"][1]["time_window"] = {"start": "09:00", "end": "12:00"}
        vehicles = [{"vehicle_id": "van", "count": 1, "max_weight_kg": 500, "min_stops": 5, "max_stops": 5}]
        is_error, preview = await call(
            client,
            "optimize_routes",
            {
                "plan_id": plan_id,
                "depot": DEPOT,
                "vehicles": vehicles,
                "use_weight": False,
                "use_time_windows": False,
            },
        )
    assert not is_error, preview
    preflight = preview["preflight"]
    codes = {w["code"] for w in preflight["warnings"] or []}
    assert preflight["constraints_enforced"] == []
    assert "stops_without_weight" not in codes and "route_start_time_required" not in codes
    assert "stop_band_margin" in codes


async def test_a_new_chat_can_repeat_the_last_run_of_a_plan(
    mcp_client: Callable[..., Any], clock: FakeClock
) -> None:
    async with await mcp_client() as client:
        created = await _import(client)
        _, plan = await call(client, "list_plans", {"plan_id": created["plan_id"]})
        assert "last_agent_run" not in plan["plan"]  # absent until an agent runs the plan

        args = {
            "dataset_id": created["dataset_id"],
            "depot": DEPOT,
            "depot_name": "Galpón",
            "vehicles": [{"vehicle_id": "van", "count": 2, "min_stops": 1, "max_stops": 20}],
            "route_start_time": "08:05",
            "time_zone": "Europe/Stockholm",
        }
        is_error, run = await call(client, "optimize_routes", args)
        assert not is_error, run

    # A different MCP session on the same account sees how the plan was last optimized.
    async with await mcp_client() as other_chat:
        _, listed = await call(other_chat, "list_datasets", {})
        row = next(d for d in listed["datasets"] if d["dataset_id"] == created["dataset_id"])
        assert row["last_run"]["optimization_id"] == run["optimization_id"]
        assert (
            row["last_run"]["dataset_id"] == created["dataset_id"] and row["last_run"]["excluded_stops"] == 0
        )

        clock.now += 10
        _, plan = await call(other_chat, "list_plans", {"plan_id": created["plan_id"]})
        last = plan["plan"]["last_agent_run"]
        assert last["depot"] == {"lat": DEPOT["latitude"], "lng": DEPOT["longitude"]}
        assert last["vehicles"] == [{"id": "van", "count": 2, "min_stops": 1, "max_stops": 20}]
        assert last["schedule"]["route_start_time"] == "08:05"
        assert plan["plan"]["depot"]["name"] == "Galpón"
        assert plan["plan"]["next_optimize_charged"] is False and plan["plan"]["free_retries_remaining"] == 1

        is_error, result = await call(
            other_chat, "get_optimization_result", {"optimization_id": run["optimization_id"]}
        )
    assert not is_error, result
    assert result["request"]["depot"] == last["depot"]
    assert result["request"]["schedule"]["time_zone"] == "Europe/Stockholm"
    assert result["plan_id"] == created["plan_id"] and "expires_at" not in result


async def test_import_into_a_named_plan_replaces_its_stops(
    mcp_client: Callable[..., Any], core_state: Any
) -> None:
    async with await mcp_client() as client:
        plan_id = (await _import(client))["plan_id"]
        again = await _import(client, plan_id=plan_id)
        assert again["plan_id"] == plan_id
        assert len([p for p in core_state.plans.values() if not p["deleted"]]) == 1

        is_error, missing = await call(
            client, "import_deliveries", {"text": "id,address\n1,x", "plan_id": "cmf_does_not_exist"}
        )
    assert is_error and missing["error"]["code"] == "PLAN_NOT_FOUND"


async def test_the_same_arguments_run_again_once_the_plans_stops_changed(
    mcp_client: Callable[..., Any], core_state: Any, clock: FakeClock
) -> None:
    # Core deduplicates the request as sent, before it expands the plan's stops: without the plan's
    # revision in the key, a plan re-imported with new stops would replay yesterday's routes.
    async with await mcp_client() as client:
        plan_id = (await _import(client))["plan_id"]
        args = {"plan_id": plan_id, "depot": DEPOT, "vehicles": VEHICLES}
        _, first = await call(client, "optimize_routes", args)
        clock.now += 10
        await _import(client, plan_id=plan_id, filename="martes.csv")
        is_error, second = await call(client, "optimize_routes", args)
    assert not is_error, second
    assert second["optimization_id"] != first["optimization_id"]
    assert second["idempotent_replay"] is False


async def test_a_plan_that_is_optimizing_answers_plan_busy(
    mcp_client: Callable[..., Any], core_state: Any
) -> None:
    core_state.plan.max_concurrent = 5
    async with await mcp_client() as client:
        plan_id = (await _import(client))["plan_id"]
        args = {"plan_id": plan_id, "depot": DEPOT, "vehicles": VEHICLES}
        is_error, first = await call(client, "optimize_routes", args)
        assert not is_error, first
        is_error, busy = await call(client, "optimize_routes", {**args, "service_time_minutes": 3})
    assert is_error, busy
    assert busy["error"]["code"] == "PLAN_BUSY" and busy["error"]["retryable"] is True
    assert busy["error"]["retry_after_seconds"] == 30


async def test_an_import_into_a_full_library_says_which_plan_it_replaced(
    mcp_client: Callable[..., Any],
) -> None:
    async with await mcp_client() as client:
        first = await _import(client, filename="lunes.csv")
        for name in ("martes.csv", "miercoles.csv"):
            await _import(client, filename=name)
        fourth = await _import(client, filename="jueves.csv")
        is_error, status = await call(client, "get_import_result", {"import_id": fourth["import_id"]})
        _, listed = await call(client, "list_datasets", {})
    assert not is_error, status
    assert status["plan_replaced"] == {"plan_id": first["plan_id"], "name": "lunes"}
    rows = {row["dataset_id"]: row for row in listed["datasets"]}
    assert rows[fourth["dataset_id"]]["plan_replaced"] == status["plan_replaced"]
    # Only when true: the other imports did not rotate the library.
    assert (
        "plan_replaced" not in rows[first["dataset_id"]] and "plan_temporary" not in rows[first["dataset_id"]]
    )


async def test_plan_preflight_shows_the_plans_totals_when_nothing_is_excluded(
    mcp_client: Callable[..., Any], core_state: Any
) -> None:
    async with await mcp_client(MCP_CONFIRM_BEFORE_OPTIMIZE="true") as client:
        plan_id = (await _import(client))["plan_id"]
        for stop in core_state.plans[plan_id]["stops"]:
            stop["weight_kg"] = 2.5
        vehicles = [{"vehicle_id": "van", "count": 1, "max_stops": 20, "max_weight_kg": 500}]
        args = {"plan_id": plan_id, "depot": DEPOT, "vehicles": vehicles}
        _, whole = await call(client, "optimize_routes", args)
        _, partial = await call(client, "optimize_routes", {**args, "exclude_stop_ids": ["S1"]})
        _, plan = await call(client, "list_plans", {"plan_id": plan_id})
    assert whole["preflight"]["total_weight_kg"] == 12.5
    assert "total_volume_m3" not in whole["preflight"]
    # A total over every stored stop would overstate a run that leaves stops out.
    assert "total_weight_kg" not in partial["preflight"]
    assert plan["plan"]["total_weight_kg"] == 12.5


async def test_a_depot_address_runs_and_reports_what_it_matched(
    mcp_client: Callable[..., Any], caplog: pytest.LogCaptureFixture
) -> None:
    address = "Av. Saenz 1200, Pompeya, Buenos Aires, Argentina"
    async with await mcp_client() as client:
        created = await _import(client)
        with caplog.at_level("INFO"):
            is_error, run = await call(
                client,
                "optimize_routes",
                {"plan_id": created["plan_id"], "depot": {"address": address}, "vehicles": VEHICLES},
            )
    assert not is_error, run
    resolved = run["depot_resolved"]
    assert resolved["matched_address"] == address
    assert -90 <= resolved["latitude"] <= 90 and -180 <= resolved["longitude"] <= 180
    # docs/privacy-mcp.md: application logs carry neither addresses nor coordinates.
    logged = [
        getattr(record, "fields", {}) for record in caplog.records if record.getMessage() == "depot_geocoded"
    ]
    assert logged == [{"band": "valid"}]


async def test_an_uncertain_depot_match_is_handed_back_before_anything_is_charged(
    mcp_client: Callable[..., Any],
) -> None:
    async with await mcp_client() as client:
        created = await _import(client)
        is_error, payload = await call(
            client,
            "optimize_routes",
            {
                "plan_id": created["plan_id"],
                "depot": {"address": "Saenz (unsure), Buenos Aires"},
                "vehicles": VEHICLES,
            },
        )
        assert is_error and payload["error"]["code"] == "INVALID_INPUT"
        resolved = payload["error"]["details"]["depot_resolved"]
        assert resolved["matched_address"] and "latitude" in resolved

        # The user confirmed the match: its coordinates run without geocoding again.
        is_error, run = await call(
            client,
            "optimize_routes",
            {
                "plan_id": created["plan_id"],
                "depot": {"latitude": resolved["latitude"], "longitude": resolved["longitude"]},
                "vehicles": VEHICLES,
            },
        )
    assert not is_error, run
    assert run.get("depot_resolved") is None


async def test_the_depot_preflight_shows_the_matched_address(mcp_client: Callable[..., Any]) -> None:
    async with await mcp_client(MCP_CONFIRM_BEFORE_OPTIMIZE="true") as client:
        created = await _import(client)
        is_error, payload = await call(
            client,
            "optimize_routes",
            {
                "plan_id": created["plan_id"],
                "depot": {"address": "Av. Saenz 1200, Buenos Aires"},
                "vehicles": VEHICLES,
            },
        )
    assert not is_error, payload
    assert payload["preflight"]["depot_resolved"]["matched_address"] == "Av. Saenz 1200, Buenos Aires"


@pytest.mark.parametrize(
    ("depot", "expected"),
    [
        (
            {"address": "Av. Saenz 1200, Pompeya, Buenos Aires, Argentina"},
            {"city": "Buenos Aires", "country": "Argentina"},
        ),
        ({"address": "Av. Saenz 1200, Buenos Aires"}, {"city": "Buenos Aires"}),
        ({"address": "Av. Saenz 1200"}, {"city": "Av. Saenz 1200"}),
        (
            {"address": "Av. Saenz 1200, Argentina", "city": "Lanus", "country": "AR"},
            {"city": "Lanus", "country": "AR"},
        ),
    ],
    ids=["street-area-city-country", "street-city", "street-only", "explicit"],
)
def test_depot_region_does_not_send_the_country_as_the_city(
    depot: dict[str, str], expected: dict[str, str]
) -> None:
    assert import_tools.depot_region(import_tools.DatasetDepot(**depot)) == expected


async def test_only_tools_that_deduplicate_say_they_are_idempotent(mcp_client: Callable[..., Any]) -> None:
    async with await mcp_client() as client:
        tools = {tool.name: tool for tool in (await client.list_tools()).tools}
    # An import starts a new import and a new plan on every call; a run is keyed by its arguments.
    assert tools["import_deliveries"].annotations.idempotent_hint is False
    assert tools["import_deliveries"].annotations.idempotent_hint is False
    assert tools["optimize_routes"].annotations.idempotent_hint is True
    assert tools["update_import_mapping"].annotations.idempotent_hint is True


async def test_a_link_that_is_not_shared_publicly_says_so(
    mcp_client: Callable[..., Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    async def not_a_file(url: str, *, max_bytes: int) -> tuple[bytes, str | None]:
        raise PublicFetchError("not_a_file")

    monkeypatch.setattr(import_tools, "fetch_public_https", not_a_file)
    async with await mcp_client() as client:
        is_error, payload = await call(
            client,
            "import_deliveries",
            {
                "file": {
                    "download_url": "https://drive.google.com/file/d/abcdefghij12/view",
                    "file_name": "a.xlsx",
                }
            },
        )
    assert is_error and payload["error"]["code"] == "INVALID_INPUT"
    assert "Anyone with the link" in payload["error"]["suggestion"]


async def test_a_mapping_can_declare_the_unit_and_format_of_a_column(
    mcp_client: Callable[..., Any], core_state: Any
) -> None:
    async with await mcp_client() as client:
        created = await _import(client)
        is_error, payload = await call(
            client,
            "update_import_mapping",
            {
                "import_id": created["import_id"],
                "mapping": {
                    "Peso": {"field": "weight_kg", "unit": "lb"},
                    "Entrega": {"field": "time_window_start", "format": "MM/DD/YYYY hh:mm AM/PM"},
                    "Cliente": "customer_name",
                    "Notas": None,
                },
            },
        )
        assert not is_error, payload
        sent = core_state.imports[created["import_id"]]["mapping"]
        # Unset keys are left out, so Smart Import infers what was not declared.
        assert sent["Peso"] == {"field": "weight_kg", "unit": "lb"}
        assert sent["Entrega"] == {"field": "time_window_start", "format": "MM/DD/YYYY hh:mm AM/PM"}
        assert sent["Cliente"] == "customer_name" and sent["Notas"] is None

        is_error, payload = await call(
            client,
            "update_import_mapping",
            {"import_id": created["import_id"], "mapping": {"Peso": {"unit": "lb"}}},
        )
    assert is_error and payload["error"]["code"] == "INVALID_INPUT"


async def test_the_rows_of_an_import_never_reach_the_model(mcp_client: Callable[..., Any]) -> None:
    # 0.9.0: the model works from ids and counts. Core's summary carries the first rows of the file
    # (addresses, phones); the MCP keeps them and says only what each column holds.
    async with await mcp_client() as client:
        created = await _import(client)
        is_error, result = await call(client, "get_import_result", {"import_id": created["import_id"]})
    assert not is_error, result
    summary = result["summary"]
    assert "sample_rows" not in summary
    assert summary["column_kinds"] == {"id": "number", "direccion": "text", "entrega": "date", "tel": "phone"}
    shown = json.dumps(result, ensure_ascii=False)
    for value in ("Calle Falsa 123", "Av. Siempreviva 742", "4567-8901", "21/09/2026"):
        assert value not in shown


@pytest.mark.parametrize(
    ("arguments", "message_part"),
    [
        (
            {"text": "id,address\n1,Calle Falsa 123", "url": "https://files.example.com/a.csv"},
            "send one of file, url or text",
        ),
        ({}, "No deliveries arrived"),
    ],
    ids=["two-sources", "none"],
)
async def test_an_import_takes_one_source(
    mcp_client: Callable[..., Any], core_state: Any, arguments: dict[str, Any], message_part: str
) -> None:
    async with await mcp_client() as client:
        is_error, payload = await call(client, "import_deliveries", arguments)
    assert is_error and payload["error"]["code"] == "INVALID_INPUT"
    assert message_part in json.dumps(payload["error"])
    assert not core_state.imports


async def test_a_saved_depot_is_passed_by_its_id(mcp_client: Callable[..., Any], core_state: Any) -> None:
    # The model carries an id, not coordinates: the server reads the depot from the account's catalog.
    core_state.depots.append({"depot_id": "7", "name": "Barracas", "latitude": -34.64, "longitude": -58.38})
    async with await mcp_client() as client:
        created = await _import(client)
        is_error, run = await call(
            client,
            "optimize_routes",
            {"plan_id": created["plan_id"], "depot": {"depot_id": "7"}, "vehicles": VEHICLES},
        )
        assert not is_error, run
        job = core_state.jobs[run["optimization_id"]]
        assert job.body["depot"] == {"lat": -34.64, "lng": -58.38}
        # The saved depot's name goes with it, so the plan shows where the routes start.
        assert core_state.plans[created["plan_id"]]["depot"]["name"] == "Barracas"

        is_error, payload = await call(
            client,
            "optimize_routes",
            {"plan_id": created["plan_id"], "depot": {"depot_id": "99"}, "vehicles": VEHICLES},
        )
    assert is_error and payload["error"]["code"] == "DEPOT_NOT_FOUND"
    assert "list_fleet" in payload["error"]["suggestion"]


async def test_stops_in_the_chat_run_through_the_same_tool_as_a_saved_plan(
    mcp_client: Callable[..., Any], core_state: Any
) -> None:
    stops = [{"stop_id": f"S{i}", "latitude": -34.6 - i * 0.001, "longitude": -58.4} for i in range(1, 4)]
    async with await mcp_client() as client:
        is_error, run = await call(
            client,
            "optimize_routes",
            {
                "stops": stops,
                "depot": {"address": "Av. Saenz 1200, Buenos Aires"},
                "vehicles": VEHICLES,
                "route_start_time": "08:30",
                "time_zone": "America/Argentina/Buenos_Aires",
                "plan_name": "Martes",
            },
        )
    assert not is_error, run
    assert run["depot_resolved"]["matched_address"] == "Av. Saenz 1200, Buenos Aires"
    job = core_state.jobs[run["optimization_id"]]
    assert job.body["schedule"]["route_start_time"] == "08:30" and run["plan_name"] == "Martes"
    assert [s["id"] for s in job.body["stops"]] == ["S1", "S2", "S3"]


@pytest.mark.parametrize(
    ("arguments", "code_part"),
    [
        (
            {
                "plan_id": "cmf0000000000000000000001",
                "stops": [{"stop_id": "a", "latitude": 1, "longitude": 1}],
            },
            "exactly one",
        ),
        (
            {"stops": [{"stop_id": "a", "latitude": 1, "longitude": 1}], "exclude_stop_ids": ["a"]},
            "exclude_stop_ids",
        ),
        ({"plan_id": "cmf0000000000000000000001", "plan_name": "Nuevo"}, "plan_name"),
        ({"plan_id": "cmf0000000000000000000001", "depot": {"latitude": -34.6}}, "latitude and longitude"),
    ],
    ids=["plan-and-stops", "exclude-with-stops", "plan-name-on-a-plan", "half-a-depot"],
)
async def test_optimize_routes_refuses_what_it_cannot_mean(
    mcp_client: Callable[..., Any], core_state: Any, arguments: dict[str, Any], code_part: str
) -> None:
    args = {"depot": DEPOT, "vehicles": VEHICLES, **arguments}
    async with await mcp_client() as client:
        is_error, payload = await call(client, "optimize_routes", args)
    assert is_error and payload["error"]["code"] == "INVALID_INPUT"
    assert code_part in json.dumps(payload["error"])
    assert not core_state.jobs

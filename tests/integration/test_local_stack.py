"""Integration tests against a real local Vepathos stack (api-doc MCP channel + optimizer + worker).

Skipped unless VEPATHOS_INTEGRATION=1. Never point these at production.
See docs/testing.md for the stack setup and required environment variables.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import AsyncIterator
from typing import Any

import pytest
from devtools.generate_dataset import generate

from vepathos_mcp.clients.vepathos_api import CallContext, VepathosApiClient
from vepathos_mcp.errors.codes import DomainError, ErrorCode
from vepathos_mcp.schemas.inputs import parse_optimize_input
from vepathos_mcp.schemas.mapping import resolve_schedule_date, to_core_request

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(os.environ.get("VEPATHOS_INTEGRATION") != "1", reason="VEPATHOS_INTEGRATION != 1"),
]

BASE_URL = os.environ.get("VEPATHOS_API_BASE_URL", "http://localhost:3000")
if "vepathos.com" in BASE_URL and os.environ.get("VEPATHOS_INTEGRATION_ALLOW_REMOTE") != "1":
    pytest.skip("refusing to run integration tests against a vepathos.com host", allow_module_level=True)


def credential(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        pytest.skip(f"{name} not set")
    return f"Bearer {value}"


@pytest.fixture
async def core() -> AsyncIterator[VepathosApiClient]:
    client = VepathosApiClient(BASE_URL, os.environ.get("VEPATHOS_MCP_SERVICE_KEY", ""), timeout_seconds=60)
    try:
        yield client
    finally:
        await client.aclose()


def body_for(**kwargs: Any) -> dict[str, Any]:
    inp = parse_optimize_input(
        generate(
            stops=kwargs.get("stops", 10),
            vehicles=kwargs.get("vehicles", 2),
            depot=(-37.3217, -59.1332),
            radius_km=5,
            weight=kwargs.get("weight", False),
            volume=kwargs.get("volume", False),
            time_windows=kwargs.get("time_windows", False),
            seed=kwargs.get("seed", 1),
        )
    )
    return to_core_request(inp, resolve_schedule_date(inp))


async def wait_completed(core: VepathosApiClient, call: CallContext, job_id: str, attempts: int = 180) -> str:
    import anyio

    for _ in range(attempts):
        status = await core.get_job(call, job_id)
        if status.is_terminal:
            return status.status
        await anyio.sleep(2)
    return "timeout"


async def test_health(core: VepathosApiClient) -> None:
    assert await core.health()


@pytest.mark.parametrize("stops", [10, 150])
async def test_basic_optimization_completes(core: VepathosApiClient, stops: int) -> None:
    call = CallContext(authorization=credential("VEPATHOS_GROWTH_CREDENTIAL"))
    created = await core.create_job(call, body_for(stops=stops, vehicles=4), f"it-{uuid.uuid4().hex}")
    assert await wait_completed(core, call, created.job_id) == "completed"
    result = await core.get_result(call, created.job_id, view="summary", offset=0, limit=25)
    assert result.summary and result.summary["stops_submitted"] == stops


async def test_idempotent_retry_returns_same_job(core: VepathosApiClient) -> None:
    call = CallContext(authorization=credential("VEPATHOS_GROWTH_CREDENTIAL"))
    key = f"it-{uuid.uuid4().hex}"
    body = body_for(stops=12, seed=42)
    first = await core.create_job(call, body, key)
    second = await core.create_job(call, body, key)
    assert first.job_id == second.job_id and second.idempotent_replay


async def test_free_plan_rejects_large_basic_request(core: VepathosApiClient) -> None:
    call = CallContext(authorization=credential("VEPATHOS_FREE_CREDENTIAL"))
    with pytest.raises(DomainError) as info:
        await core.create_job(call, body_for(stops=400), f"it-{uuid.uuid4().hex}")
    assert info.value.code is ErrorCode.PLAN_UPGRADE_REQUIRED


async def test_unauthorized_credential(core: VepathosApiClient) -> None:
    call = CallContext(authorization="Bearer vpt_" + "0" * 24 + ":vpt_sk_test_" + "0" * 48)
    with pytest.raises(DomainError) as info:
        await core.create_job(call, body_for(stops=3), f"it-{uuid.uuid4().hex}")
    assert info.value.code in {ErrorCode.INVALID_CREDENTIALS, ErrorCode.AUTHENTICATION_REQUIRED}


async def test_catalog_master_data_round_trip_against_routehub(core: VepathosApiClient) -> None:
    """The catalog writes against the REAL RouteHub: nothing a unit test or the fake Core can vouch for.

    What only this proves: RouteHub accepts the integer units Core writes (a fractional capacity goes
    down to GRAMS / LITER), the created row is reconciled to the caller's company and so reads back,
    a PATCH changes only what is sent, and a create converges by name instead of duplicating.
    Spends no stops and no geocoding. Names are unique per run, so reruns never collide; RouteHub has
    no delete on this channel, so each run leaves one vehicle and one depot in the DEV account.
    """

    call = CallContext(authorization=credential("VEPATHOS_GROWTH_CREDENTIAL"))
    tag = uuid.uuid4().hex[:8]
    vehicle = {"name": f"IT Sprinter {tag}", "max_weight_kg": 1500, "max_volume_m3": 12.5}

    created = await core.create_vehicle(call, vehicle, idempotency_key=f"it-vehicle-{tag}")
    assert created.outcome == "created"
    assert created.vehicle.max_weight_kg == 1500 and created.vehicle.max_volume_m3 == 12.5

    # The same request again, with another spelling of the name: the row that exists, nothing written.
    again = await core.create_vehicle(
        call, {**vehicle, "name": vehicle["name"].upper()}, idempotency_key=f"it-vehicle-{tag}-b"
    )
    assert again.outcome == "already_existed" and again.vehicle.vehicle_id == created.vehicle.vehicle_id

    with pytest.raises(DomainError) as taken:
        await core.create_vehicle(
            call, {**vehicle, "max_weight_kg": 900}, idempotency_key=f"it-vehicle-{tag}-c"
        )
    assert taken.value.code is ErrorCode.NAME_TAKEN

    updated = await core.update_vehicle(call, created.vehicle.vehicle_id, {"max_volume_m3": 14})
    assert updated.outcome == "updated"
    assert updated.vehicle.max_volume_m3 == 14 and updated.vehicle.max_weight_kg == 1500
    assert updated.vehicle.name == vehicle["name"]

    with pytest.raises(DomainError) as missing:
        await core.update_vehicle(call, "999999999", {"name": "x"})
    assert missing.value.code is ErrorCode.VEHICLE_NOT_FOUND

    depot = {"name": f"IT Depot {tag}", "latitude": -37.3217, "longitude": -59.1332}
    saved = await core.create_depot(call, depot, idempotency_key=f"it-depot-{tag}")
    assert saved.outcome == "created"
    assert (
        abs(saved.depot.latitude - depot["latitude"]) < 1e-6
        and abs(saved.depot.longitude - depot["longitude"]) < 1e-6
    )
    nearby = await core.create_depot(
        call, {**depot, "latitude": -37.32172}, idempotency_key=f"it-depot-{tag}-b"
    )
    assert nearby.outcome == "already_existed"
    moved = await core.update_depot(call, saved.depot.depot_id, {"latitude": -37.33, "longitude": -59.14})
    assert abs(moved.depot.latitude + 37.33) < 1e-6 and moved.depot.name == depot["name"]

    # Both read back through the catalog the agent sees.
    catalog = await core.get_catalog(call)
    assert created.vehicle.vehicle_id in {v.vehicle_id for v in catalog.vehicles}
    assert saved.depot.depot_id in {d.depot_id for d in catalog.depots}

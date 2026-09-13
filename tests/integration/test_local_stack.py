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

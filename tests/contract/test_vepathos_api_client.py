"""Contract tests: VepathosApiClient ↔ docs/core-channel-contract.md (HTTP mocked with respx)."""

from __future__ import annotations

import json

import httpx
import pytest
import respx

from tests.conftest import API_KEY, SERVICE_KEY
from vepathos_mcp.clients.breaker import CircuitBreaker
from vepathos_mcp.clients.vepathos_api import CallContext, VepathosApiClient
from vepathos_mcp.errors.codes import DomainError, ErrorCode

BASE = "https://api.vepathos.test"
CALL = CallContext(
    authorization=f"Bearer {API_KEY}",
    client_label="claude",
    traceparent="00-" + "a" * 32 + "-" + "b" * 16 + "-01",
)

CREATED = {
    "job_id": "mcp_" + "1" * 32,
    "status": "queued",
    "idempotent_replay": False,
    "submitted_stops": 2,
    "vehicles_available": 1,
    "schedule_date": "2026-09-14",
    "expires_at": "2026-09-14T18:20:00Z",
    "billing": {"mode": "plan", "quota_charged": True, "stops_remaining_this_period": 1998},
}


async def no_sleep(_: float) -> None:
    return None


def client(**kwargs: object) -> VepathosApiClient:
    return VepathosApiClient(BASE, SERVICE_KEY, sleep=no_sleep, **kwargs)  # type: ignore[arg-type]


@respx.mock
async def test_create_job_sends_trust_boundary_headers_and_body() -> None:
    route = respx.post(f"{BASE}/api/mcp/v1/optimization/jobs").mock(
        return_value=httpx.Response(202, json=CREATED)
    )
    api = client()
    body = {"depot": {"lat": 1.0, "lng": 2.0}, "vehicles": [{"id": "v", "count": 1}], "stops": []}
    created = await api.create_job(CALL, body, "mcp-fp-abcdef0123")
    await api.aclose()

    request = route.calls.last.request
    assert request.headers["X-Vepathos-MCP-Service-Key"] == SERVICE_KEY
    assert request.headers["Authorization"] == f"Bearer {API_KEY}"
    assert request.headers["Idempotency-Key"] == "mcp-fp-abcdef0123"
    assert request.headers["X-Vepathos-MCP-Client"] == "claude"
    assert request.headers["traceparent"] == CALL.traceparent
    assert json.loads(request.content) == body
    # The account is never sent by the adapter: Core derives it from the credential.
    assert "account" not in request.content.decode() and "user_id" not in str(request.headers).lower()
    assert (
        created.job_id == CREATED["job_id"]
        and created.billing
        and created.billing.stops_remaining_this_period == 1998
    )


@respx.mock
async def test_create_job_retries_with_same_idempotency_key_on_503() -> None:
    route = respx.post(f"{BASE}/api/mcp/v1/optimization/jobs").mock(
        side_effect=[
            httpx.Response(503, headers={"Retry-After": "1"}),
            httpx.ConnectError("reset"),
            httpx.Response(202, json=CREATED),
        ]
    )
    api = client()
    created = await api.create_job(CALL, {"stops": []}, "mcp-fp-retry-key")
    await api.aclose()
    assert created.status == "queued"
    keys = {call.request.headers["Idempotency-Key"] for call in route.calls}
    assert route.call_count == 3 and keys == {"mcp-fp-retry-key"}


@respx.mock
async def test_client_errors_are_not_retried_and_are_mapped() -> None:
    route = respx.post(f"{BASE}/api/mcp/v1/optimization/jobs").mock(
        return_value=httpx.Response(
            403,
            json={
                "error": {
                    "code": "PLAN_UPGRADE_REQUIRED",
                    "message": "Plan too small.",
                    "details": {
                        "reason": "STOP_LIMIT_EXCEEDED",
                        "requested": {"stops": 400},
                        "current_limit": {"stops_per_request": 150},
                    },
                }
            },
        )
    )
    api = client()
    with pytest.raises(DomainError) as info:
        await api.create_job(CALL, {"stops": []}, "mcp-fp-4xx-key")
    await api.aclose()
    assert route.call_count == 1
    assert info.value.code is ErrorCode.PLAN_UPGRADE_REQUIRED
    assert "up to 150 stops" in info.value.message


@respx.mock
async def test_post_without_idempotency_key_is_refused_locally() -> None:
    api = client()
    with pytest.raises(ValueError):
        await api._request("POST", "/api/mcp/v1/optimization/jobs", CALL, operation="submit", json_body={})
    await api.aclose()


@respx.mock
async def test_read_timeout_maps_to_timeout_after_retries() -> None:
    route = respx.get(f"{BASE}/api/mcp/v1/optimization/jobs/mcp_x").mock(
        side_effect=httpx.ReadTimeout("slow")
    )
    api = client(max_attempts=2)
    with pytest.raises(DomainError) as info:
        await api.get_job(CALL, "mcp_x")
    await api.aclose()
    assert route.call_count == 2 and info.value.code is ErrorCode.TIMEOUT and info.value.retryable


@respx.mock
async def test_circuit_breaker_fails_fast() -> None:
    route = respx.get(f"{BASE}/api/mcp/v1/optimization/jobs/mcp_x").mock(return_value=httpx.Response(502))
    api = client(max_attempts=1, breaker=CircuitBreaker(failure_threshold=2, reset_after_seconds=60))
    for _ in range(2):
        with pytest.raises(DomainError):
            await api.get_job(CALL, "mcp_x")
    with pytest.raises(DomainError) as info:
        await api.get_job(CALL, "mcp_x")
    await api.aclose()
    assert route.call_count == 2  # third call never left the process
    assert info.value.code is ErrorCode.BACKEND_UNAVAILABLE and info.value.retry_after_seconds


@respx.mock
async def test_result_query_parameters_and_path_quoting() -> None:
    route = respx.get(f"{BASE}/api/mcp/v1/optimization/jobs/mcp_%2F..%2Fx/result").mock(
        return_value=httpx.Response(200, json={"job_id": "mcp_/../x", "status": "running"})
    )
    api = client()
    result = await api.get_result(CALL, "mcp_/../x", view="stops", offset=200, limit=100, route_id="r2")
    await api.aclose()
    params = dict(route.calls.last.request.url.params)
    assert params == {"view": "stops", "offset": "200", "limit": "100", "route_id": "r2"}
    assert result.status == "running"


@respx.mock
async def test_unexpected_success_payload_is_internal_error() -> None:
    respx.get(f"{BASE}/api/mcp/v1/optimization/jobs/mcp_x").mock(
        return_value=httpx.Response(200, json={"weird": True})
    )
    api = client()
    with pytest.raises(DomainError) as info:
        await api.get_job(CALL, "mcp_x")
    await api.aclose()
    assert info.value.code is ErrorCode.INTERNAL_ERROR


@respx.mock
async def test_health_probe_uses_service_key_only() -> None:
    route = respx.get(f"{BASE}/api/mcp/v1/health").mock(
        return_value=httpx.Response(200, json={"status": "ok"})
    )
    api = client()
    assert await api.health()
    await api.aclose()
    request = route.calls.last.request
    assert request.headers["X-Vepathos-MCP-Service-Key"] == SERVICE_KEY
    assert "authorization" not in request.headers


async def test_get_account_reports_a_core_without_the_route() -> None:
    """Core does not serve /account yet: say so plainly instead of 'unexpected response'."""

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, text="Not Found")

    client = VepathosApiClient(
        "http://core.test", "svc", transport=httpx.MockTransport(handler), max_attempts=1
    )
    try:
        with pytest.raises(DomainError) as excinfo:
            await client.get_account(CallContext(authorization="Bearer t"))
    finally:
        await client.aclose()
    assert "does not report account information yet" in excinfo.value.message
    assert excinfo.value.retryable is False


PLAN = {
    "plan_id": "cmf0000000000000000000001",
    "name": "zona-1",
    "created_by": "agent",
    "temporary": False,
    "kept": False,
    "favorite": False,
    "revision": 7,
    "stops": 10931,
    "stops_not_optimizable": 0,
    "total_weight_kg": 21862.5,
    "total_volume_m3": None,
    "with_weight": 10931,
    "with_volume": 0,
    "with_time_window": 0,
    "depot": {"name": "Depot", "lat": -34.6, "lng": -58.4},
    "optimizing": False,
    "last_run_history_id": "cmfhist",
    "last_run_at": "2026-09-16T12:00:00.000Z",
    "account_url": "https://vepathos.com/dashboard?tab=plans&plan=cmf0000000000000000000001",
    "created_at": "2026-09-16T11:00:00.000Z",
    "updated_at": "2026-09-16T12:00:00.000Z",
    "next_optimize_charged": False,
    "free_retries_allowed": 1,
    "free_retries_remaining": 1,
    "free_retry_window_ends_at": "2026-09-17T12:00:00.000Z",
    "last_agent_run": {"depot": {"lat": -34.6, "lng": -58.4}, "vehicles": [{"id": "van", "count": 2}]},
}


@respx.mock
async def test_list_plans_sends_limit_and_query_and_parses_the_library() -> None:
    route = respx.get(f"{BASE}/api/mcp/v1/plans").mock(
        return_value=httpx.Response(
            200,
            json={
                "plans": [{k: v for k, v in PLAN.items() if k != "last_agent_run"}],
                "library": {"plans_in_library": 3, "max_plans": 3, "kept_plans": 2, "max_kept_plans": 2},
            },
        )
    )
    api = client()
    listed = await api.list_plans(CALL, limit=5, query="zona")
    await api.aclose()
    assert dict(route.calls.last.request.url.params) == {"limit": "5", "query": "zona"}
    assert route.calls.last.request.headers["X-Vepathos-MCP-Service-Key"] == SERVICE_KEY
    assert listed.plans[0].plan_id == PLAN["plan_id"] and listed.plans[0].next_optimize_charged is False
    assert listed.library is not None and listed.library.max_kept_plans == 2


@respx.mock
async def test_get_plan_quotes_the_id_and_reads_the_last_agent_run() -> None:
    route = respx.get(f"{BASE}/api/mcp/v1/plans/cmf%2F..%2Fx").mock(
        return_value=httpx.Response(200, json=PLAN)
    )
    api = client()
    plan = await api.get_plan(CALL, "cmf/../x")
    await api.aclose()
    assert route.called
    assert plan.depot is not None and plan.depot.lat == -34.6
    assert plan.revision == 7 and plan.total_weight_kg == 21862.5 and plan.total_volume_m3 is None
    assert plan.free_retry_window_ends_at == "2026-09-17T12:00:00.000Z"
    assert plan.last_agent_run is not None and plan.last_agent_run["vehicles"][0]["id"] == "van"


@respx.mock
async def test_create_job_passes_the_plan_source_and_parses_where_the_run_lives() -> None:
    route = respx.post(f"{BASE}/api/mcp/v1/optimization/jobs").mock(
        return_value=httpx.Response(
            202,
            json={
                **CREATED,
                "plan_id": "cmfnew",
                "plan_name": "Optimization 2026-09-16",
                "account_url": "https://vepathos.com/dashboard?tab=plans&plan=cmfnew",
                "plan_replaced": {"id": "cmfold", "displayName": "Lunes"},
                "plan_temporary": True,
                "billing": {
                    "mode": "plan_free_retry",
                    "quota_charged": False,
                    "stops_remaining_this_period": 950000,
                    "free_retries_remaining": 0,
                },
            },
        )
    )
    body = {
        "plan_id": "cmf0000000000000000000001",
        "exclude_stop_ids": ["S1"],
        "depot_name": "Galpón",
        "depot": {"lat": 1.0, "lng": 2.0},
        "vehicles": [{"id": "v", "count": 1}],
    }
    api = client()
    created = await api.create_job(CALL, body, "mcp-fp-plan-key")
    await api.aclose()
    assert json.loads(route.calls.last.request.content) == body
    assert created.plan_id == "cmfnew" and created.plan_name == "Optimization 2026-09-16"
    assert created.plan_replaced is not None
    assert (created.plan_replaced.id, created.plan_replaced.display_name) == ("cmfold", "Lunes")
    assert created.plan_temporary is True
    assert created.billing is not None and created.billing.mode == "plan_free_retry"
    assert created.billing.free_retries_remaining == 0


@respx.mock
async def test_a_busy_plan_is_not_retried_by_the_client_but_tells_when_to_retry() -> None:
    route = respx.post(f"{BASE}/api/mcp/v1/optimization/jobs").mock(
        return_value=httpx.Response(
            409,
            headers={"Retry-After": "30"},
            json={
                "error": {
                    "code": "PLAN_BUSY",
                    "message": "This plan is already optimizing.",
                    "retryable": True,
                }
            },
        )
    )
    api = client()
    with pytest.raises(DomainError) as info:
        await api.create_job(CALL, {"plan_id": "cmf0000000000000000000001"}, "mcp-fp-busy-key")
    await api.aclose()
    # Retrying inside the call would only find the same run still going: the agent waits instead.
    assert route.call_count == 1
    assert info.value.code is ErrorCode.PLAN_BUSY and info.value.retryable
    assert info.value.retry_after_seconds == 30


@respx.mock
async def test_settled_plan_results_never_expire_and_name_their_history_run() -> None:
    respx.get(f"{BASE}/api/mcp/v1/optimization/jobs/mcp_x/result").mock(
        return_value=httpx.Response(
            200,
            json={
                "job_id": "mcp_x",
                "status": "completed",
                "plan_id": "cmfplan",
                "history_id": "cmfhist",
                "account_url": "https://vepathos.com/dashboard?tab=plans&plan=cmfplan&history=cmfhist",
                "expires_at": None,
                "summary": {"stops_submitted": 2},
            },
        )
    )
    api = client()
    result = await api.get_result(CALL, "mcp_x", view="summary", offset=0, limit=None)
    await api.aclose()
    assert result.expires_at is None
    assert (result.plan_id, result.history_id) == ("cmfplan", "cmfhist")


@respx.mock
async def test_a_settled_plan_job_status_names_its_history_run() -> None:
    respx.get(f"{BASE}/api/mcp/v1/optimization/jobs/mcp_x").mock(
        return_value=httpx.Response(
            200,
            json={
                "job_id": "mcp_x",
                "status": "completed",
                "plan_id": "cmfplan",
                "history_id": "cmfhist",
                "completed_at": "2026-09-16T12:00:00.000Z",
                "account_url": "https://vepathos.com/dashboard?tab=plans&plan=cmfplan&history=cmfhist",
                "progress": None,
                "submitted_stops": 2,
                "expires_at": None,
                "billing": {"mode": "plan", "quota_charged": True, "stops_remaining_this_period": None},
            },
        )
    )
    api = client()
    status = await api.get_job(CALL, "mcp_x")
    await api.aclose()
    assert status.is_terminal and status.expires_at is None
    assert status.history_id == "cmfhist" and status.completed_at == "2026-09-16T12:00:00.000Z"
    assert status.account_url is not None and status.account_url.endswith("history=cmfhist")

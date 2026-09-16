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

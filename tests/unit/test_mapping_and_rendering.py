from __future__ import annotations

import json
from datetime import UTC, datetime

from tests.conftest import sample_arguments
from vepathos_mcp.errors.codes import DomainError, ErrorCode
from vepathos_mcp.schemas.inputs import parse_optimize_input
from vepathos_mcp.schemas.mapping import request_fingerprint, resolve_schedule_date, to_core_request
from vepathos_mcp.schemas.outputs import OptimizationResult, Page, ResultSummary, RouteMetrics
from vepathos_mcp.tools.rendering import error_result, estimate_tokens, result_text, success_result


def test_core_request_shape_matches_contract() -> None:
    args = sample_arguments(stops=2)
    args["vehicles"] = [{"vehicle_id": "van", "count": 2, "max_weight_kg": 900}]
    args["stops"][0].update(weight_kg=1.5, time_window={"start": "09:00", "end": "10:00"})
    args["stops"][1].update(weight_kg=2.0)
    args["schedule"]["route_start_time"] = "08:00"
    inp = parse_optimize_input(args)
    body = to_core_request(inp, "2026-09-14")
    assert body["depot"] == {"lat": -34.6037, "lng": -58.3816}
    assert body["vehicles"] == [{"id": "van", "count": 2, "max_weight_kg": 900.0}]
    assert body["stops"][0] == {
        "id": "ORD-00000",
        "lat": -34.6,
        "lng": -58.38,
        "weight_kg": 1.5,
        "time_window": {"start": "09:00", "end": "10:00"},
    }
    assert body["schedule"] == {
        "date": "2026-09-14",
        "route_start_time": "08:00",
        "time_zone": "America/Argentina/Buenos_Aires",
    }
    assert "stop_id" not in json.dumps(body)


def test_schedule_date_defaults_to_today_in_time_zone() -> None:
    args = sample_arguments(stops=1)
    args["schedule"] = {"time_zone": "Pacific/Auckland"}
    inp = parse_optimize_input(args)
    moment = datetime(2026, 9, 13, 15, 0, tzinfo=UTC)  # already Sept 14 in Auckland
    assert resolve_schedule_date(inp, now=moment) == "2026-09-14"


def test_fingerprint_is_deterministic_and_sensitive() -> None:
    inp = parse_optimize_input(sample_arguments(stops=4))
    a = request_fingerprint(to_core_request(inp, "2026-09-14"))
    b = request_fingerprint(to_core_request(parse_optimize_input(sample_arguments(stops=4)), "2026-09-14"))
    c = request_fingerprint(to_core_request(inp, "2026-09-15"))
    assert a == b != c
    assert a.startswith("mcp-fp-") and 8 <= len(a) <= 128


def test_success_result_is_compact_json_with_structured_content() -> None:
    output = OptimizationResult(optimization_id="mcp_x1234567", status="queued", poll_after_seconds=10)
    result = success_result(output)
    assert not result.is_error
    assert result.structured_content == {
        "optimization_id": "mcp_x1234567",
        "status": "queued",
        "poll_after_seconds": 10,
    }
    assert (
        result_text(result) == '{"optimization_id":"mcp_x1234567","status":"queued","poll_after_seconds":10}'
    )


def test_error_result_carries_structured_error() -> None:
    err = DomainError(
        ErrorCode.QUOTA_EXCEEDED, "No stops left.", suggestion="Upgrade.", details={"stops_remaining": 0}
    )
    result = error_result(err)
    assert result.is_error
    assert result.structured_content == {
        "error": {
            "code": "QUOTA_EXCEEDED",
            "message": "No stops left.",
            "retryable": False,
            "suggestion": "Upgrade.",
            "details": {"stops_remaining": 0},
        }
    }


def test_summary_for_large_plan_stays_within_token_budget() -> None:
    """A 3,000-stop / 35-route summary with the default 25-route page must stay small."""

    routes = [
        RouteMetrics(
            route_id=f"route-{i:03d}",
            vehicle_id="van-large",
            stops=86,
            distance_km=123.45,
            duration_minutes=487.5,
            weight_kg=812.25,
            volume_m3=6.125,
        )
        for i in range(25)
    ]
    output = OptimizationResult(
        optimization_id="mcp_" + "f" * 32,
        status="completed",
        detail="summary",
        expires_at="2026-09-14T18:20:00Z",
        summary=ResultSummary(
            stops_submitted=3000,
            stops_assigned=2998,
            stops_unassigned=2,
            vehicles_available=35,
            vehicles_used=35,
            total_distance_km=4321.9,
            total_duration_minutes=17062.5,
            charged_stops=2998,
        ),
        routes=routes,
        page=Page(offset=0, limit=25, total=35, next_offset=25),
    )
    assert estimate_tokens(result_text(success_result(output))) <= 2500

from __future__ import annotations

from vepathos_mcp.errors.codes import ErrorCode
from vepathos_mcp.errors.mapping import from_core_error


def envelope(code: str, message: str = "", **details: object) -> dict[str, object]:
    return {"error": {"code": code, "message": message, "retryable": False, "details": details}}


def test_plan_upgrade_stop_limit_message_uses_core_numbers() -> None:
    err = from_core_error(
        403,
        envelope(
            "PLAN_UPGRADE_REQUIRED",
            "Plan too small.",
            reason="STOP_LIMIT_EXCEEDED",
            requested={"stops": 2430},
            current_limit={"stops_per_request": 500},
            eligible_plans=[{"id": "growth", "name": "Growth"}],
            upgrade_url="https://api.vepathos.com/dashboard/billing?upgrade=growth&source=mcp",
        ),
    )
    assert err.code is ErrorCode.PLAN_UPGRADE_REQUIRED
    assert err.message == (
        "Your current Vepathos plan allows up to 500 stops per optimization. This request contains 2,430 stops."
    )
    assert err.suggestion == "Reduce the number of stops or upgrade the Vepathos plan."
    assert err.details["upgrade_url"].startswith("https://")
    assert err.details["eligible_plans"] == [{"id": "growth", "name": "Growth"}]
    assert err.retryable is False


def test_plan_upgrade_feature_and_trial_hint() -> None:
    err = from_core_error(
        403,
        envelope(
            "PLAN_UPGRADE_REQUIRED",
            reason="FEATURE_NOT_AVAILABLE",
            requested={"stops": 2500, "features": ["time_windows"]},
            full_trial={"available": True, "max_stops": 2000},
        ),
    )
    assert err.message == "Your current Vepathos plan does not include delivery time windows."
    assert "up to 2,000 stops" in err.suggestion  # type: ignore[operator]
    assert err.details["full_trial"] == {"available": True, "max_stops": 2000}


def test_upgrade_url_must_be_http_url() -> None:
    err = from_core_error(403, envelope("PLAN_UPGRADE_REQUIRED", upgrade_url="javascript:alert(1)"))
    assert "upgrade_url" not in err.details


def test_quota_and_concurrency() -> None:
    quota = from_core_error(
        429,
        envelope("QUOTA_EXCEEDED", requested=300, stops_remaining=120, period_ends_at="2026-10-01T00:00:00Z"),
    )
    assert quota.code is ErrorCode.QUOTA_EXCEEDED
    assert "300 stops but 120 remain" in quota.message
    busy = from_core_error(
        429, envelope("CONCURRENT_OPTIMIZATION_LIMIT", limit=1, active_job_ids=["mcp_a"]), 7
    )
    assert busy.retryable and busy.retry_after_seconds == 7
    assert busy.details == {"active_optimization_ids": ["mcp_a"]}


def test_operator_errors_do_not_leak() -> None:
    err = from_core_error(401, envelope("SERVICE_UNAUTHORIZED", "service key abc123 invalid"))
    assert err.code is ErrorCode.INTERNAL_ERROR
    assert "abc123" not in str(err.to_payload())


def test_unknown_errors_by_status() -> None:
    assert from_core_error(502, None).code is ErrorCode.BACKEND_UNAVAILABLE
    assert from_core_error(401, {"detail": "nope"}).code is ErrorCode.AUTHENTICATION_REQUIRED
    assert from_core_error(418, "teapot").code is ErrorCode.INTERNAL_ERROR


def test_messages_are_clipped() -> None:
    err = from_core_error(400, envelope("INVALID_INPUT", "x" * 5000))
    assert len(err.message) <= 400

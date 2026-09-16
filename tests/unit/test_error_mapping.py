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
    assert err.suggestion is not None
    assert err.suggestion.startswith("Reduce the number of stops or upgrade the Vepathos plan.")
    # Plan limits belong to an account, so the agent must be able to question the connection itself.
    assert "get_account" in err.suggestion
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


def test_authentication_error_explains_accounts_and_how_to_switch() -> None:
    err = from_core_error(401, envelope("AUTHENTICATION_REQUIRED", "nope"))
    assert err.code is ErrorCode.AUTHENTICATION_REQUIRED
    assert err.suggestion is not None
    # Reconnecting is only half the fix: a stale grant keeps sending the user back to the old account.
    assert "Connected apps" in err.suggestion and "revoked" in err.suggestion
    assert "creates a free one" in err.suggestion
    assert "get_account" in err.suggestion


def test_quota_error_also_questions_the_connected_account() -> None:
    err = from_core_error(
        403, envelope("QUOTA_EXCEEDED", "", stops_remaining=10, requested=400, period_ends_at="2026-10-01")
    )
    assert err.code is ErrorCode.QUOTA_EXCEEDED
    assert err.suggestion is not None and "get_account" in err.suggestion


def test_api_key_callers_are_not_told_to_reconnect_a_connector() -> None:
    from vepathos_mcp.clients.vepathos_api import CallContext
    from vepathos_mcp.tools.runtime import RequestIdentity, explain_auth_failure

    def identity(kind: str) -> RequestIdentity:
        return RequestIdentity(
            call=CallContext(authorization="Bearer x"),
            subject="s",
            client_label="other",
            protocol_version=None,
            credential_kind=kind,
        )

    key_error = explain_auth_failure(identity("api_key"), from_core_error(401, envelope("INVALID_CREDENTIALS")))
    assert key_error.suggestion is not None
    # A developer credential has no connector, no consent screen and no grant to revoke.
    assert "reconnect" not in key_error.suggestion.lower().replace("no connector to reconnect", "")
    assert "mcp:optimize" in key_error.suggestion
    assert "client_id:client_secret" in key_error.suggestion
    assert "test secret does not authenticate against production" in key_error.suggestion
    assert "revoked and reissued" in key_error.suggestion

    oauth_error = explain_auth_failure(identity("oauth"), from_core_error(401, envelope("INVALID_CREDENTIALS")))
    assert oauth_error.suggestion is not None and "Connected apps" in oauth_error.suggestion


def test_only_authorization_errors_are_rewritten_for_the_credential() -> None:
    from vepathos_mcp.clients.vepathos_api import CallContext
    from vepathos_mcp.tools.runtime import RequestIdentity, explain_auth_failure

    identity = RequestIdentity(
        call=CallContext(authorization="Bearer x"),
        subject="s",
        client_label="other",
        protocol_version=None,
        credential_kind="api_key",
    )
    quota = from_core_error(403, envelope("QUOTA_EXCEEDED", "", stops_remaining=1, requested=9))
    assert explain_auth_failure(identity, quota).suggestion == quota.suggestion

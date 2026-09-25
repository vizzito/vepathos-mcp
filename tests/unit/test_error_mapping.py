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

    key_error = explain_auth_failure(
        identity("api_key"), from_core_error(401, envelope("INVALID_CREDENTIALS"))
    )
    assert key_error.suggestion is not None
    # A developer credential has no connector, no consent screen and no grant to revoke.
    assert "reconnect" not in key_error.suggestion.lower().replace("no connector to reconnect", "")
    assert "mcp:optimize" in key_error.suggestion
    assert "client_id:client_secret" in key_error.suggestion
    assert "test secret does not authenticate against production" in key_error.suggestion
    assert "revoked and reissued" in key_error.suggestion

    oauth_error = explain_auth_failure(
        identity("oauth"), from_core_error(401, envelope("INVALID_CREDENTIALS"))
    )
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


def test_plan_not_found_points_at_list_plans() -> None:
    err = from_core_error(404, envelope("PLAN_NOT_FOUND", "No plan with this id exists for this account."))
    assert err.code is ErrorCode.PLAN_NOT_FOUND
    assert err.retryable is False
    assert err.suggestion is not None and "list_plans" in err.suggestion


def test_plan_busy_is_retryable_after_the_running_optimization() -> None:
    err = from_core_error(409, envelope("PLAN_BUSY", "This plan is already optimizing."), 30)
    assert err.code is ErrorCode.PLAN_BUSY
    assert err.retryable is True and err.retry_after_seconds == 30
    assert err.suggestion is not None and "get_optimization_result" in err.suggestion
    # Without Retry-After the agent still gets a wait.
    assert from_core_error(409, envelope("PLAN_BUSY")).retry_after_seconds == 30


def test_import_and_dataset_not_found_are_not_unexpected_responses() -> None:
    dataset = from_core_error(404, envelope("DATASET_NOT_FOUND"))
    assert dataset.code is ErrorCode.DATASET_NOT_FOUND
    assert dataset.suggestion is not None and "plan_id" in dataset.suggestion
    assert from_core_error(404, envelope("IMPORT_NOT_FOUND")).code is ErrorCode.IMPORT_NOT_FOUND


def test_quota_rejection_keeps_why_the_free_retry_did_not_apply() -> None:
    free_retry = {"next_optimize_charged": True, "charged_because": "stops_changed"}
    err = from_core_error(
        429, envelope("QUOTA_EXCEEDED", requested=10, stops_remaining=2, free_retry=free_retry)
    )
    assert err.details["free_retry"] == free_retry
    assert err.suggestion is not None and "same stops or fewer" in err.suggestion


def test_a_taken_name_hands_back_the_one_that_has_it() -> None:
    existing = {"vehicle_id": "7", "name": "Sprinter", "max_weight_kg": 1200}
    err = from_core_error(409, envelope("NAME_TAKEN", "Taken.", existing=existing, matches=1))
    assert err.code is ErrorCode.NAME_TAKEN and err.retryable is False
    assert err.details == {"existing": existing}
    assert err.suggestion is not None and "action=update" in err.suggestion
    assert from_core_error(409, envelope("NAME_TAKEN")).details == {}


def test_an_unknown_saved_vehicle_or_depot_points_back_to_list_fleet() -> None:
    for code, expected in (
        ("VEHICLE_NOT_FOUND", ErrorCode.VEHICLE_NOT_FOUND),
        ("DEPOT_NOT_FOUND", ErrorCode.DEPOT_NOT_FOUND),
    ):
        err = from_core_error(404, envelope(code))
        assert err.code is expected and err.retryable is False
        assert err.suggestion is not None and "list_fleet" in err.suggestion


def test_a_catalog_cap_says_the_plan_can_still_run_without_saving() -> None:
    err = from_core_error(
        403,
        envelope(
            "PLAN_UPGRADE_REQUIRED",
            "Your plan allows up to 3 vehicles.",
            reason="CATALOG_VEHICLE_LIMIT",
            upgrade_url="https://api.vepathos.com/dashboard/billing?source=mcp",
        ),
    )
    assert err.code is ErrorCode.PLAN_UPGRADE_REQUIRED
    assert err.suggestion is not None and "without saving it" in err.suggestion
    assert err.details["reason"] == "CATALOG_VEHICLE_LIMIT" and err.details["upgrade_url"]
    depot = from_core_error(403, envelope("PLAN_UPGRADE_REQUIRED", "cap", reason="CATALOG_DEPOT_LIMIT"))
    assert depot.suggestion is not None and "saved-depot" in depot.suggestion


def test_backend_unavailable_keeps_cores_own_reason() -> None:
    """Three blind retries came from throwing this sentence away: the agent was told to resend."""

    err = from_core_error(503, envelope("BACKEND_UNAVAILABLE", "Import is not configured."))
    assert err.code is ErrorCode.BACKEND_UNAVAILABLE
    assert err.message == "Import is not configured."
    assert err.retry_after_seconds == 30


def test_backend_unavailable_without_a_reason_still_says_something() -> None:
    assert from_core_error(503, envelope("BACKEND_UNAVAILABLE")).message == "Vepathos is temporarily unavailable."


def test_plain_5xx_never_repeats_a_body_that_is_not_ours() -> None:
    """A 502 from a proxy carries HTML, not our envelope: that text must not reach the agent."""

    err = from_core_error(502, "<html><body>nginx</body></html>")
    assert err.code is ErrorCode.BACKEND_UNAVAILABLE
    assert err.message == "Vepathos is temporarily unavailable."

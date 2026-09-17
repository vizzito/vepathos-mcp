"""get_account: the label the agent shows, and the masking it applies before showing it."""

from __future__ import annotations

from vepathos_mcp.clients.core_models import CoreAccount
from vepathos_mcp.tools.account import account_label, mask_email, to_account_info


def core_account(**account: object) -> CoreAccount:
    return CoreAccount.model_validate(
        {
            "account": account,
            "plan": {
                "id": "free",
                "name": "Free",
                "max_stops_per_request": 150,
                "features": ["time_windows"],
                "max_active_optimizations": 1,
            },
            "usage": {
                "stops_limit": 2000,
                "stops_used": 236,
                "stops_remaining": 1764,
                "period_end": "2026-10-01T00:00:00Z",
            },
            "full_trial_available": False,
        }
    )


def test_mask_email_keeps_the_domain_recognisable() -> None:
    assert mask_email("martin@stormtech.com") == "m***@stormtech.com"
    assert mask_email("m@x.io") == "m***@x.io"
    assert mask_email("not-an-email") == "***"


def test_company_name_is_preferred_over_the_email() -> None:
    assert (
        account_label(core_account(company_name="Vepathos SA", email="martin@stormtech.com")) == "Vepathos SA"
    )


def test_email_is_masked_and_account_id_is_the_last_resort() -> None:
    assert account_label(core_account(email="martin@stormtech.com")) == "m***@stormtech.com"
    assert account_label(core_account(account_id="acct_123")) == "acct_123"
    assert account_label(core_account()) is None


def test_output_never_carries_the_full_email() -> None:
    info = to_account_info(core_account(account_id="acct_123", email="martin@stormtech.com"))
    assert info.account_label == "m***@stormtech.com"
    assert "martin@stormtech.com" not in info.model_dump_json()
    assert info.plan is not None and info.plan.max_stops_per_request == 150
    assert info.usage is not None and info.usage.stops_remaining == 1764
    assert info.full_trial_available is False


def test_unlimited_plan_reports_no_stop_ceiling() -> None:
    account = core_account(account_id="acct_1")
    account.plan.unlimited_stops_per_request = True
    account.plan.max_stops_per_request = None
    info = to_account_info(account)
    assert info.plan is not None and info.plan.max_stops_per_request is None


def test_features_the_tools_cannot_request_are_not_advertised() -> None:
    account = CoreAccount.model_validate(
        {
            "account": {"email": "ops@stormtech.com"},
            "plan": {"name": "Scale", "features": ["time_windows", "minimize_duration"]},
        }
    )
    info = to_account_info(account)
    assert info.plan is not None
    # Offering duration routing produces a promise optimize_delivery_routes cannot keep.
    assert info.plan.features == ["time_windows"]

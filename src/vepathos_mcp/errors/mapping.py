"""Translate Vepathos Core channel errors into agent-friendly domain errors.

Messages are built from structured `details` returned by Core. Plan names and limits are never
hardcoded here: whatever Core reports is what the agent sees.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from vepathos_mcp.errors.codes import DomainError, ErrorCode

MAX_MESSAGE_CHARS = 400

# Authorization failures are usually a connection problem, not a Vepathos outage: the grant was
# revoked, the token expired, or the client is connected to a different account than the user means.
AUTH_MESSAGE = "The Vepathos connection is not authorized, so no account could be read for this request."
# The fix differs by how the caller authenticated, and advice for the other path wastes the user's
# time: an API-key caller has no connector to reconnect, and an OAuth user has no key to reissue.
AUTH_SUGGESTION = (
    "Ask the user to reconnect the Vepathos connector and sign in. Tell them that signing in with a "
    "different Vepathos user connects a different account, with its own plan and stop quota, and "
    "that connecting without an account creates a free one. To switch accounts, the grant must be "
    "revoked first in Connected apps in the Vepathos dashboard, because the old one is remembered. "
    "Once connected, call get_account to confirm which account it is."
)
API_KEY_AUTH_SUGGESTION = (
    "This client authenticates with a Vepathos developer credential, so there is no connector to "
    "reconnect: the credential itself was refused. Ask the user to check, in the Vepathos dashboard "
    "of the account they mean, that it is sent as client_id:client_secret, that it was created with "
    "the option for AI agents (scope mcp:optimize) rather than as a REST key, and that it belongs "
    "to the environment being called — a test secret does not authenticate against production. "
    "Never ask the user to paste the secret; if they already did, it must be revoked and reissued."
)

# Plans and quotas belong to an account, so "upgrade the plan" is the wrong advice when the client
# is simply connected to the wrong one.
WRONG_ACCOUNT_HINT = (
    "These limits are the connected Vepathos account's: if that plan is not the one the user "
    "expects, call get_account to check which account the connector is on."
)

FEATURE_LABELS: dict[str, str] = {
    "weight_capacity": "weight capacity constraints",
    "volume_capacity": "volume capacity constraints",
    "time_windows": "delivery time windows",
    "minimize_duration": "duration-based routing",
}


def _clip(text: str) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= MAX_MESSAGE_CHARS else text[: MAX_MESSAGE_CHARS - 1] + "…"


def _int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return None


def _fmt(n: int) -> str:
    return f"{n:,}"


def _as_dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _as_list(value: Any) -> list[Any]:
    return list(value) if isinstance(value, list) else []


def _plan_upgrade(message: str, details: dict[str, Any]) -> DomainError:
    reason = str(details.get("reason") or "")
    requested = _as_dict(details.get("requested"))
    current = _as_dict(details.get("current_limit"))
    trial = _as_dict(details.get("full_trial"))
    upgrade_url = details.get("upgrade_url")
    contact_url = details.get("contact_url")

    if reason == "STOP_LIMIT_EXCEEDED":
        limit = _int(current.get("stops_per_request"))
        asked = _int(requested.get("stops"))
        if limit is not None and asked is not None:
            message = (
                f"Your current Vepathos plan allows up to {_fmt(limit)} stops per optimization. "
                f"This request contains {_fmt(asked)} stops."
            )
        suggestion = "Reduce the number of stops or upgrade the Vepathos plan."
    elif reason == "FEATURE_NOT_AVAILABLE":
        features = _as_list(requested.get("features"))
        labels = [FEATURE_LABELS.get(str(f), str(f)) for f in features]
        if labels:
            message = f"Your current Vepathos plan does not include {', '.join(labels)}."
        suggestion = "Remove the unsupported constraints or upgrade the Vepathos plan."
    elif reason == "VEHICLE_LIMIT_EXCEEDED":
        suggestion = "Reduce the number of vehicles or upgrade the Vepathos plan."
    elif reason == "ROUTE_STOP_LIMIT_EXCEEDED":
        suggestion = "Lower max_stops per vehicle or upgrade the Vepathos plan."
    elif reason == "CATALOG_VEHICLE_LIMIT":
        suggestion = (
            "The plan's saved-vehicle limit is reached. Plan with this vehicle in vehicles[] without "
            "saving it, remove one in the dashboard, or upgrade the Vepathos plan."
        )
    elif reason == "CATALOG_DEPOT_LIMIT":
        suggestion = (
            "The plan's saved-depot limit is reached. Pass the depot coordinates to the optimize call "
            "without saving it, remove one in the dashboard, or upgrade the Vepathos plan."
        )
    else:
        suggestion = "Adjust the request or upgrade the Vepathos plan."

    suggestion += f" {WRONG_ACCOUNT_HINT}"
    trial_max = _int(trial.get("max_stops")) if trial.get("available") is True else None
    if trial_max is not None:
        suggestion += (
            f" This account still has a one-time full-feature optimization for requests of up to "
            f"{_fmt(trial_max)} stops."
        )

    kept: dict[str, Any] = {"reason": reason} if reason else {}
    for key in ("requested", "current_limit", "required_capability", "eligible_plans"):
        if details.get(key) is not None:
            kept[key] = details[key]
    if isinstance(upgrade_url, str) and upgrade_url.startswith("https://"):
        kept["upgrade_url"] = upgrade_url
    elif isinstance(contact_url, str) and contact_url.startswith("https://"):
        kept["contact_url"] = contact_url
    elif isinstance(upgrade_url, str) and upgrade_url.startswith("http://"):
        # Local development deployments use plain http; production Core always sends https.
        kept["upgrade_url"] = upgrade_url
    elif isinstance(contact_url, str) and contact_url.startswith("http://"):
        kept["contact_url"] = contact_url
    if trial_max is not None:
        kept["full_trial"] = {"available": True, "max_stops": trial_max}
    return DomainError(ErrorCode.PLAN_UPGRADE_REQUIRED, _clip(message), suggestion=suggestion, details=kept)


def _quota(message: str, details: dict[str, Any]) -> DomainError:
    remaining = _int(details.get("stops_remaining"))
    asked = _int(details.get("requested"))
    resets = details.get("period_ends_at")
    if remaining is not None and asked is not None:
        message = (
            f"This optimization needs {_fmt(asked)} stops but {_fmt(remaining)} remain in the current "
            "Vepathos billing period."
        )
    suggestion = "Reduce the number of stops, wait for the quota to renew, or upgrade the Vepathos plan."
    if isinstance(resets, str):
        suggestion = (
            "Reduce the number of stops, wait for the quota to renew "
            f"({resets}), or upgrade the Vepathos plan."
        )
    suggestion += f" {WRONG_ACCOUNT_HINT}"
    # A plan run that expected another try says why it is charged (charged_because).
    free_retry = _as_dict(details.get("free_retry"))
    if free_retry.get("charged_because") == "stops_changed":
        suggestion += (
            " Another try does not apply: its stops differ from the charged run. "
            "Only the same stops or fewer qualify as another try."
        )
    kept: dict[str, Any] = {
        k: details[k]
        for k in ("stops_remaining", "requested", "period_ends_at", "upgrade_url", "free_retry")
        if k in details
    }
    return DomainError(ErrorCode.QUOTA_EXCEEDED, _clip(message), suggestion=suggestion, details=kept)


def _concurrency(message: str, details: dict[str, Any], retry_after: int | None) -> DomainError:
    limit = _int(details.get("limit"))
    active = _as_list(details.get("active_job_ids"))
    if limit is not None:
        noun = "optimization" if limit == 1 else "optimizations"
        message = f"Your Vepathos plan allows {limit} {noun} running at a time."
    suggestion = (
        "Wait for the running optimization to finish (use get_optimization_result), then submit again."
    )
    return DomainError(
        ErrorCode.CONCURRENT_OPTIMIZATION_LIMIT,
        _clip(message),
        suggestion=suggestion,
        details={"active_optimization_ids": [str(a) for a in active][:10]},
        retry_after_seconds=retry_after or 30,
    )


def from_core_error(status: int, body: Any, retry_after: int | None = None) -> DomainError:
    """Map a non-2xx Core channel response to a `DomainError`."""

    envelope = body.get("error") if isinstance(body, Mapping) else None
    code_raw = str(envelope.get("code")) if isinstance(envelope, Mapping) else ""
    message = str(envelope.get("message") or "") if isinstance(envelope, Mapping) else ""
    details_raw = envelope.get("details") if isinstance(envelope, Mapping) else None
    details: dict[str, Any] = dict(details_raw) if isinstance(details_raw, Mapping) else {}

    match code_raw:
        case "PLAN_UPGRADE_REQUIRED":
            return _plan_upgrade(
                message or "Your current Vepathos plan cannot run this optimization.", details
            )
        case "AUTOMATION_NOT_INCLUDED":
            # Not a limit that resets: the account's plan has no automations at all, so the only honest
            # advice is an upgrade — and the reminder that limits belong to the connected account.
            current = _int(details.get("current_limit"))
            return DomainError(
                ErrorCode.AUTOMATION_NOT_INCLUDED,
                _clip(message or "This Vepathos plan does not include automations."),
                suggestion=(
                    "Tell the user their plan does not include automations, and that they can upgrade "
                    f"in the Vepathos dashboard. {WRONG_ACCOUNT_HINT}"
                ),
                details={"current_limit": current} if current is not None else None,
            )
        case "QUOTA_EXCEEDED":
            return _quota(message or "The Vepathos plan quota for this period is exhausted.", details)
        case "CONCURRENT_OPTIMIZATION_LIMIT":
            return _concurrency(message or "Too many optimizations are running.", details, retry_after)
        case "INVALID_INPUT":
            issues = _as_list(details.get("issues"))
            return DomainError(
                ErrorCode.INVALID_INPUT,
                _clip(message or "The optimization request is invalid."),
                suggestion=_issues_suggestion(issues),
                details={"issues": issues[:10]} if issues else None,
            )
        case "INVALID_COORDINATES":
            stop_ids = _as_list(details.get("stop_ids"))
            radius = details.get("max_distance_km")
            return DomainError(
                ErrorCode.INVALID_COORDINATES,
                _clip(message or "Some stops have coordinates that cannot be routed from this depot."),
                suggestion=(
                    "Check latitude/longitude of the listed stops; stops must be within "
                    f"{radius} km of the depot. Split far-away stops into a separate optimization."
                    if radius is not None
                    else "Check latitude/longitude of the listed stops or optimize them from another depot."
                ),
                details={"stop_ids": [str(s) for s in stop_ids][:25], "count": len(stop_ids)},
            )
        case "IDEMPOTENCY_CONFLICT":
            return DomainError(
                ErrorCode.IDEMPOTENCY_CONFLICT,
                "This idempotency_key was already used for a different optimization request.",
                suggestion="Use a new idempotency_key (or omit it) for a different request.",
            )
        case "PAYLOAD_TOO_LARGE":
            return DomainError(
                ErrorCode.PAYLOAD_TOO_LARGE,
                _clip(message or "The optimization request is too large."),
                suggestion="Split the problem (for example by zone or depot) into smaller optimizations.",
                details={k: details[k] for k in ("limit_bytes", "received_bytes") if k in details} or None,
            )
        case "AUTHENTICATION_REQUIRED" | "INVALID_CREDENTIALS":
            return DomainError(ErrorCode(code_raw), _clip(AUTH_MESSAGE), suggestion=AUTH_SUGGESTION)
        case "OPTIMIZATION_NOT_FOUND":
            return DomainError(
                ErrorCode.OPTIMIZATION_NOT_FOUND,
                "No optimization with this id exists for the connected Vepathos account.",
                suggestion="Check the optimization_id, or start a new optimization.",
            )
        case "OPTIMIZATION_EXPIRED":
            return DomainError(
                ErrorCode.OPTIMIZATION_EXPIRED,
                "The results of this optimization have expired.",
                suggestion="Run the optimization again to get fresh results.",
            )
        case "GEOCODE_NOT_FOUND":
            return DomainError(
                ErrorCode.GEOCODE_NOT_FOUND,
                "No geocoding job with this id exists for the connected Vepathos account.",
                suggestion="Check geocode_id, or start a new geocode_addresses call.",
            )
        case "GEOCODE_EXPIRED":
            return DomainError(
                ErrorCode.GEOCODE_EXPIRED,
                "This geocoding job is no longer available.",
                suggestion="Call geocode_addresses again with the same addresses.",
            )
        case "IMPORT_NOT_FOUND":
            return DomainError(
                ErrorCode.IMPORT_NOT_FOUND,
                "No import with this id exists for the connected Vepathos account.",
                suggestion="Check the import_id, or import the file again.",
            )
        case "DATASET_NOT_FOUND":
            return DomainError(
                ErrorCode.DATASET_NOT_FOUND,
                "No import with this dataset_id exists for the connected Vepathos account, or it expired.",
                suggestion="Imports are kept 24 hours. Optimize the plan it loaded into by plan_id "
                "(list_plans), or import the file again.",
            )
        case "NAME_TAKEN":
            existing = _as_dict(details.get("existing"))
            return DomainError(
                ErrorCode.NAME_TAKEN,
                _clip(message or "The account already has one with this name."),
                suggestion="Tell the user it exists and how it differs, then ask: update that one "
                "(action=update with its id) or save this one under another name.",
                details={"existing": existing} if existing else None,
                retryable=False,
            )
        case "VEHICLE_NOT_FOUND":
            return DomainError(
                ErrorCode.VEHICLE_NOT_FOUND,
                "No saved vehicle with this vehicle_id exists in the connected Vepathos account.",
                suggestion="Call list_fleet and use a vehicle_id from its top-level vehicles.",
                retryable=False,
            )
        case "DEPOT_NOT_FOUND":
            return DomainError(
                ErrorCode.DEPOT_NOT_FOUND,
                "No saved depot with this depot_id exists in the connected Vepathos account.",
                suggestion="Call list_fleet and use a depot_id from its depots.",
                retryable=False,
            )
        case "PLAN_NOT_FOUND":
            return DomainError(
                ErrorCode.PLAN_NOT_FOUND,
                "No plan with this plan_id exists in the connected Vepathos account.",
                suggestion="Call list_plans to find the plan. It may have been deleted or replaced "
                "to make room in the library.",
            )
        case "PLAN_BUSY":
            return DomainError(
                ErrorCode.PLAN_BUSY,
                "This plan is already optimizing.",
                suggestion="Wait for that run to finish (get_optimization_result), then try again.",
                retry_after_seconds=retry_after or 30,
            )
        case "BACKEND_UNAVAILABLE":
            # Core's own sentence, when it sent one: "Import is not configured." and what Smart Import
            # answered are the difference between a diagnosis and three blind retries. The generic
            # sentence stays for the 5xx below, where the body is not ours and may be anything.
            return DomainError(
                ErrorCode.BACKEND_UNAVAILABLE,
                _clip(message or "Vepathos is temporarily unavailable."),
                suggestion="Retry in a moment; identical requests are safe to resend.",
                retry_after_seconds=retry_after or 30,
            )
        case "SERVICE_UNAUTHORIZED" | "CHANNEL_DISABLED":
            # Operator configuration problems: do not leak details to the agent.
            return DomainError(
                ErrorCode.INTERNAL_ERROR,
                "The Vepathos MCP service is misconfigured.",
                suggestion="Try again later. If it persists, contact Vepathos support.",
                retryable=False,
            )

    if status in (401, 403) and not code_raw:
        return DomainError(ErrorCode.AUTHENTICATION_REQUIRED, _clip(AUTH_MESSAGE), suggestion=AUTH_SUGGESTION)
    if status == 413:
        return DomainError(ErrorCode.PAYLOAD_TOO_LARGE, "The optimization request is too large.")
    if status >= 500:
        return DomainError(
            ErrorCode.BACKEND_UNAVAILABLE,
            "Vepathos is temporarily unavailable.",
            suggestion="Retry in a moment; identical requests are safe to resend.",
            retry_after_seconds=retry_after or 30,
        )
    return DomainError(
        ErrorCode.INTERNAL_ERROR,
        "Vepathos returned an unexpected response.",
        suggestion="Try again later. If it persists, contact Vepathos support.",
        retryable=False,
    )


def _issues_suggestion(issues: list[Any]) -> str:
    lines: list[str] = []
    for issue in issues[:5]:
        if isinstance(issue, Mapping):
            path = str(issue.get("path") or "(root)")
            msg = str(issue.get("message") or "invalid value")
            lines.append(f"{path}: {msg}")
    if not lines:
        return "Fix the request fields and try again."
    return "Fix these fields and try again — " + "; ".join(lines)

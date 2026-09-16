"""get_account — which Vepathos account this connection uses, and what its plan allows.

Connecting a client can silently land on the wrong account (an older sign-in, a second company,
a personal account created during OAuth). Without this tool the mismatch only surfaces when a
request hits a limit, which reads as a Vepathos problem instead of a connection problem. The
label is also cached per caller so plan errors can name the account that was actually charged.
"""

from __future__ import annotations

import logging
from typing import Annotated, Any

from mcp.server.mcpserver.context import Context
from mcp_types import CallToolResult

from vepathos_mcp.clients.core_models import CoreAccount
from vepathos_mcp.schemas.inputs import parse_get_account_input
from vepathos_mcp.schemas.outputs import AccountInfo, AccountPlan, AccountUsage
from vepathos_mcp.telemetry.logging import log_event
from vepathos_mcp.tools.rendering import success_result
from vepathos_mcp.tools.runtime import RequestIdentity, ToolDeps, instrumented

TOOL_NAME = "get_account"

# The server instructions tell the model to offer a constraint only when the plan lists it, so a
# feature the tools cannot request must not be listed: OptimizeInput has no `objective`, and Core
# always runs minimize_distance through this channel. Drop this set when the objective is exposed.
UNEXPOSED_FEATURES = frozenset({"minimize_duration"})


def mask_email(email: str) -> str:
    """`martin@stormtech.com` -> `m***@stormtech.com`: enough to recognise, not to harvest."""

    local, _, domain = email.partition("@")
    if not domain or not local:
        return "***"
    return f"{local[0]}***@{domain}"


def account_label(account: CoreAccount) -> str | None:
    """What to call this account in a sentence, preferring the least personal identifier."""

    identity = account.account
    company = (identity.company_name or "").strip()
    if company:
        return company
    email = (identity.email or "").strip()
    if email:
        return mask_email(email)
    return (identity.account_id or "").strip() or None


def to_account_info(account: CoreAccount) -> AccountInfo:
    plan = account.plan
    usage = account.usage
    return AccountInfo(
        account_label=account_label(account),
        account_id=account.account.account_id,
        plan=AccountPlan(
            id=plan.id,
            name=plan.name,
            # Core sends null for "unlimited"; the flag keeps that from reading as "unknown".
            max_stops_per_request=None if plan.unlimited_stops_per_request else plan.max_stops_per_request,
            max_fleet_units=plan.max_fleet_units,
            max_stops_per_route=plan.max_stops_per_route,
            max_active_optimizations=plan.max_active_optimizations,
            features=[f for f in (plan.features or []) if f not in UNEXPOSED_FEATURES] or None,
        ),
        usage=AccountUsage(
            stops_limit=usage.stops_limit,
            stops_used=usage.stops_used,
            stops_remaining=usage.stops_remaining,
            period_start=usage.period_start,
            period_end=usage.period_end,
        ),
        full_trial_available=account.full_trial_available,
    )


async def remember_account(deps: ToolDeps, identity: RequestIdentity) -> str | None:
    """Best-effort account label for error messages. Never raises: an error must still reach the agent."""

    cached = deps.account_labels.get(identity.subject, deps.clock())
    if cached is not None:
        return cached or None
    try:
        account = await deps.core.get_account(identity.call)
    except Exception:
        log_event("account_lookup_failed", logging.DEBUG)
        # Cache the miss too, so a failing Core is not probed once per error.
        deps.account_labels.put(identity.subject, "", deps.clock())
        return None
    label = account_label(account)
    deps.account_labels.put(identity.subject, label or "", deps.clock())
    return label


def make_get_account_tool(deps: ToolDeps) -> Any:
    async def get_account(ctx: Context) -> Annotated[CallToolResult, AccountInfo]:
        async def handle(identity: RequestIdentity, arguments: dict[str, Any]) -> CallToolResult:
            parse_get_account_input(arguments)
            deps.rate_limiter.check(identity.subject, "calls")
            account = await deps.core.get_account(identity.call)
            info = to_account_info(account)
            deps.account_labels.put(identity.subject, info.account_label or "", deps.clock())
            return success_result(info)

        return await instrumented(TOOL_NAME, ctx, deps, handle)

    return get_account

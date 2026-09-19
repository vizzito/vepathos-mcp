"""optimize_delivery_routes — submit an asynchronous vehicle routing optimization.

Optimizing spends the account's monthly stops, and Core charges again for any changed request, so
a call arrives twice: once with `confirmed` false, which only describes what would be sent, and
once with it true, after the user has agreed. The gate is here and not only in the tool
description because a description is advice a model may skip; this cannot be skipped. With the gate
off, the published description and schema stop mentioning `confirmed` (tools/descriptions.py).
"""

from __future__ import annotations

import logging
from typing import Annotated, Any

from mcp.server.mcpserver.context import Context
from mcp_types import CallToolResult

from vepathos_mcp.clients.core_models import CoreJobCreated
from vepathos_mcp.schemas.inputs import OptimizeInput, parse_optimize_input
from vepathos_mcp.schemas.mapping import (
    constraints_enforced,
    preflight_facts,
    request_fingerprint,
    resolve_schedule_date,
    stops_identity,
    to_core_request,
)
from vepathos_mcp.schemas.outputs import (
    DepotResolved,
    FullTrialApplied,
    OptimizeResult,
    Preflight,
    PreflightPlan,
    Progress,
)
from vepathos_mcp.schemas.preflight_checks import collect_warnings, reject_impossible
from vepathos_mcp.telemetry import metrics
from vepathos_mcp.telemetry.logging import log_event
from vepathos_mcp.tools.account import account_label
from vepathos_mcp.tools.plans import replaced_plan
from vepathos_mcp.tools.rendering import success_result
from vepathos_mcp.tools.results import POLL_AFTER_SECONDS, failure_error, fetch_result_view
from vepathos_mcp.tools.runtime import RequestIdentity, ToolDeps, instrumented, wait_for_terminal

TOOL_NAME = "optimize_delivery_routes"

CONFIRM_WITH = (
    "Nothing ran and nothing was charged. Show the user these figures, including the stops this "
    "charges and what is left, then call again with the same arguments and confirmed=true."
)


async def plan_check(
    deps: ToolDeps,
    identity: RequestIdentity,
    *,
    stops: int,
    charges_stops: int,
    constraints: list[str],
) -> PreflightPlan | None:
    """The plan's side of the preflight. Never raises: a preflight must not be what blocks a user.

    `stops` is checked against the plan's per-request limit even when `charges_stops` is 0: a plan's
    free retry waives the quota, not the plan limits.
    """

    try:
        account = await deps.core.get_account(identity.call)
    except Exception:
        log_event("preflight_account_lookup_failed", logging.DEBUG)
        return None
    label = account_label(account)
    deps.account_labels.put(identity.subject, label or "", deps.clock())
    plan = account.plan
    # Core sends null for "unlimited"; only the flag distinguishes that from "unknown".
    maximum = None if plan.unlimited_stops_per_request else plan.max_stops_per_request
    remaining = account.usage.stops_remaining
    missing = [f for f in constraints if f not in (plan.features or [])]
    fits = not missing
    if maximum is not None and stops > maximum:
        fits = False
    if remaining is not None and charges_stops > remaining:
        fits = False
    return PreflightPlan(
        account_label=label,
        plan_name=plan.name,
        max_stops_per_request=maximum,
        stops_remaining=remaining,
        stops_remaining_after=None if remaining is None else remaining - charges_stops,
        fits=fits,
        missing_features=missing or None,
    )


async def build_preflight(
    deps: ToolDeps,
    identity: RequestIdentity,
    inp: OptimizeInput,
    body: dict[str, Any],
    schedule_date: str,
) -> Preflight:
    facts = preflight_facts(inp, schedule_date)
    fleet_ids: set[str] | None = None
    try:
        catalog = await deps.core.get_catalog(identity.call)
        fleet_ids = {v.vehicle_id for v in catalog.vehicles}
        for fleet in catalog.fleets:
            for v in fleet.vehicles:
                fleet_ids.add(v.vehicle_id)
    except Exception:
        fleet_ids = None
    warnings = collect_warnings(inp, fleet_vehicle_ids=fleet_ids)
    return Preflight(
        **facts,
        stops_identity=stops_identity(body),
        plan=await plan_check(
            deps,
            identity,
            stops=len(inp.stops),
            charges_stops=len(inp.stops),
            constraints=constraints_enforced(inp),
        ),
        warnings=warnings or None,
        confirm_with=CONFIRM_WITH,
    )


PLAN_FREE_RETRY_BILLING = "plan_free_retry"


def created_output(
    created: CoreJobCreated, *, vehicles_available: int | None, schedule_date: str | None
) -> OptimizeResult:
    """A submitted run as the model sees it: the plan it lives in and what it cost."""

    billing = created.billing
    return OptimizeResult(
        optimization_id=created.job_id,
        status=created.status,
        plan_id=created.plan_id,
        plan_name=created.plan_name,
        account_url=created.account_url,
        plan_replaced=replaced_plan(created.plan_replaced),
        plan_temporary=True if created.plan_temporary else None,
        idempotent_replay=created.idempotent_replay,
        submitted_stops=created.submitted_stops,
        vehicles_available=created.vehicles_available or vehicles_available,
        schedule_date=created.schedule_date or schedule_date,
        expires_at=created.expires_at,
        stops_remaining_this_period=billing.stops_remaining_this_period if billing else None,
        quota_charged=billing.quota_charged if billing else None,
        free_retry=True if billing and billing.mode == PLAN_FREE_RETRY_BILLING else None,
        free_retries_remaining=billing.free_retries_remaining if billing else None,
        full_trial_applied=(
            FullTrialApplied(
                max_stops=created.full_trial_applied.max_stops,
                features=created.full_trial_applied.features,
                quota_charged=bool(billing and billing.quota_charged),
            )
            if created.full_trial_applied
            else None
        ),
    )


async def submit_optimization(
    ctx: Context,
    deps: ToolDeps,
    identity: RequestIdentity,
    body: dict[str, Any],
    idempotency_key: str,
    *,
    vehicles_available: int | None,
    schedule_date: str | None,
    depot_resolved: DepotResolved | None = None,
) -> CallToolResult:
    """Create the job, then wait inline for a fast one. Shared by every optimize tool."""

    created = await deps.core.create_job(identity.call, body, idempotency_key)
    if not created.idempotent_replay:
        metrics.OPTIMIZATION_STOPS.observe(created.submitted_stops)
    output = created_output(created, vehicles_available=vehicles_available, schedule_date=schedule_date)
    output.depot_resolved = depot_resolved

    if deps.settings.optimize_inline_wait_seconds <= 0 and created.status != "completed":
        output.poll_after_seconds = POLL_AFTER_SECONDS
        return success_result(output)

    status = await deps.core.get_job(identity.call, created.job_id)
    if not status.is_terminal and deps.settings.optimize_inline_wait_seconds > 0:
        status = await wait_for_terminal(
            ctx, deps, identity, status, deps.settings.optimize_inline_wait_seconds
        )

    if status.status == "failed":
        raise failure_error(status)
    if status.status == "completed":
        output.status = "completed"
        output.result = await fetch_result_view(deps, identity, created.job_id, detail="summary")
    else:
        output.status = status.status
        output.poll_after_seconds = POLL_AFTER_SECONDS
        if status.progress:
            output.progress = Progress(percent=status.progress.percent, stage=status.progress.stage)
    return success_result(output)


def make_optimize_tool(deps: ToolDeps) -> Any:
    async def optimize_delivery_routes(ctx: Context) -> Annotated[CallToolResult, OptimizeResult]:
        async def handle(identity: RequestIdentity, arguments: dict[str, Any]) -> CallToolResult:
            inp = parse_optimize_input(arguments)
            reject_impossible(inp)
            schedule_date = resolve_schedule_date(inp)
            body = to_core_request(inp, schedule_date)

            if deps.settings.confirm_before_optimize and not inp.confirmed:
                # No job, no quota: this costs a read of the account, so it uses the generic budget.
                deps.rate_limiter.check(identity.subject, "calls")
                return success_result(
                    OptimizeResult(preflight=await build_preflight(deps, identity, inp, body, schedule_date))
                )

            deps.rate_limiter.check(identity.subject, "optimize")
            idempotency_key = inp.idempotency_key or request_fingerprint(body)
            return await submit_optimization(
                ctx,
                deps,
                identity,
                body,
                idempotency_key,
                vehicles_available=inp.vehicles_available,
                schedule_date=schedule_date,
            )

        return await instrumented(TOOL_NAME, ctx, deps, handle)

    return optimize_delivery_routes

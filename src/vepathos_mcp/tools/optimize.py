"""optimize_delivery_routes — submit an asynchronous vehicle routing optimization.

Optimizing spends the account's monthly stops, and Core charges again for any changed request, so
a call arrives twice: once with `confirmed` false, which only describes what would be sent, and
once with it true, after the user has agreed. The gate is here and not only in the tool
description because a description is advice a model may skip; this cannot be skipped.
"""

from __future__ import annotations

import logging
from typing import Annotated, Any

from mcp.server.mcpserver.context import Context
from mcp_types import CallToolResult

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
    FullTrialApplied,
    OptimizeResult,
    Preflight,
    PreflightPlan,
    Progress,
)
from vepathos_mcp.telemetry import metrics
from vepathos_mcp.telemetry.logging import log_event
from vepathos_mcp.tools.account import account_label
from vepathos_mcp.tools.rendering import success_result
from vepathos_mcp.tools.results import POLL_AFTER_SECONDS, failure_error, fetch_result_view
from vepathos_mcp.tools.runtime import RequestIdentity, ToolDeps, instrumented, wait_for_terminal

TOOL_NAME = "optimize_delivery_routes"

CONFIRM_WITH = (
    "Nothing ran and nothing was charged. Show the user these figures, including the stops this "
    "charges and what is left, then call again with the same arguments and confirmed=true."
)


async def _plan_check(deps: ToolDeps, identity: RequestIdentity, inp: OptimizeInput) -> PreflightPlan | None:
    """The plan's side of the preflight. Never raises: a preflight must not be what blocks a user."""

    try:
        account = await deps.core.get_account(identity.call)
    except Exception:
        log_event("preflight_account_lookup_failed", logging.DEBUG)
        return None
    label = account_label(account)
    deps.account_labels.put(identity.subject, label or "", deps.clock())
    plan = account.plan
    stops = len(inp.stops)
    # Core sends null for "unlimited"; only the flag distinguishes that from "unknown".
    maximum = None if plan.unlimited_stops_per_request else plan.max_stops_per_request
    remaining = account.usage.stops_remaining
    missing = [f for f in constraints_enforced(inp) if f not in (plan.features or [])]
    fits = not missing
    if maximum is not None and stops > maximum:
        fits = False
    if remaining is not None and stops > remaining:
        fits = False
    return PreflightPlan(
        account_label=label,
        plan_name=plan.name,
        max_stops_per_request=maximum,
        stops_remaining=remaining,
        stops_remaining_after=None if remaining is None else remaining - stops,
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
    return Preflight(
        **facts,
        stops_identity=stops_identity(body),
        plan=await _plan_check(deps, identity, inp),
        confirm_with=CONFIRM_WITH,
    )


def make_optimize_tool(deps: ToolDeps) -> Any:
    async def optimize_delivery_routes(ctx: Context) -> Annotated[CallToolResult, OptimizeResult]:
        async def handle(identity: RequestIdentity, arguments: dict[str, Any]) -> CallToolResult:
            inp = parse_optimize_input(arguments)
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

            created = await deps.core.create_job(identity.call, body, idempotency_key)
            if not created.idempotent_replay:
                metrics.OPTIMIZATION_STOPS.observe(created.submitted_stops)

            output = OptimizeResult(
                optimization_id=created.job_id,
                status=created.status,
                idempotent_replay=created.idempotent_replay,
                submitted_stops=created.submitted_stops,
                vehicles_available=created.vehicles_available or inp.vehicles_available,
                schedule_date=created.schedule_date or schedule_date,
                expires_at=created.expires_at,
                stops_remaining_this_period=(
                    created.billing.stops_remaining_this_period if created.billing else None
                ),
                full_trial_applied=(
                    FullTrialApplied(
                        max_stops=created.full_trial_applied.max_stops,
                        features=created.full_trial_applied.features,
                        quota_charged=bool(created.billing and created.billing.quota_charged),
                    )
                    if created.full_trial_applied
                    else None
                ),
            )

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

        return await instrumented(TOOL_NAME, ctx, deps, handle)

    return optimize_delivery_routes

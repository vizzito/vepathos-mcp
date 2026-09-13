"""optimize_delivery_routes — submit an asynchronous vehicle routing optimization."""

from __future__ import annotations

from typing import Annotated, Any

from mcp.server.mcpserver.context import Context
from mcp_types import CallToolResult

from vepathos_mcp.schemas.inputs import parse_optimize_input
from vepathos_mcp.schemas.mapping import request_fingerprint, resolve_schedule_date, to_core_request
from vepathos_mcp.schemas.outputs import FullTrialApplied, OptimizeResult, Progress
from vepathos_mcp.telemetry import metrics
from vepathos_mcp.tools.rendering import success_result
from vepathos_mcp.tools.results import POLL_AFTER_SECONDS, failure_error, fetch_result_view
from vepathos_mcp.tools.runtime import RequestIdentity, ToolDeps, instrumented, wait_for_terminal

TOOL_NAME = "optimize_delivery_routes"


def make_optimize_tool(deps: ToolDeps) -> Any:
    async def optimize_delivery_routes(ctx: Context) -> Annotated[CallToolResult, OptimizeResult]:
        async def handle(identity: RequestIdentity, arguments: dict[str, Any]) -> CallToolResult:
            inp = parse_optimize_input(arguments)
            deps.rate_limiter.check(identity.subject, "optimize")

            schedule_date = resolve_schedule_date(inp)
            body = to_core_request(inp, schedule_date)
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

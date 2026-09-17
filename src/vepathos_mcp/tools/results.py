"""get_optimization_result — status, progress and paginated results of an optimization."""

from __future__ import annotations

from typing import Annotated, Any

from mcp.server.mcpserver.context import Context
from mcp_types import CallToolResult

from vepathos_mcp.clients.core_models import CoreJobResult, CoreJobStatusResponse
from vepathos_mcp.errors.codes import DomainError, ErrorCode
from vepathos_mcp.errors.mapping import MAX_MESSAGE_CHARS
from vepathos_mcp.schemas.inputs import parse_get_result_input
from vepathos_mcp.schemas.outputs import (
    OptimizationResult,
    Page,
    Progress,
    ResultSummary,
    RouteMetrics,
    StopVisit,
)
from vepathos_mcp.tools.rendering import success_result
from vepathos_mcp.tools.runtime import RequestIdentity, ToolDeps, instrumented, wait_for_terminal

TOOL_NAME = "get_optimization_result"
POLL_AFTER_SECONDS = 10


def failure_error(status: CoreJobStatusResponse) -> DomainError:
    message = "The optimization failed and was not charged."
    if status.failure and status.failure.message:
        message = f"The optimization failed and was not charged: {status.failure.message}"[:MAX_MESSAGE_CHARS]
    return DomainError(
        ErrorCode.OPTIMIZATION_FAILED,
        message,
        suggestion="Check the inputs (coordinates, capacities, time windows) and run the optimization again.",
        details={"optimization_id": status.job_id},
    )


def pending_output(status: CoreJobStatusResponse, detail: str | None) -> OptimizationResult:
    progress = (
        Progress(percent=status.progress.percent, stage=status.progress.stage) if status.progress else None
    )
    return OptimizationResult(
        optimization_id=status.job_id,
        status=status.status,
        detail=detail,  # type: ignore[arg-type]
        progress=progress,
        poll_after_seconds=POLL_AFTER_SECONDS,
        expires_at=status.expires_at,
    )


def completed_output(result: CoreJobResult, detail: str) -> OptimizationResult:
    try:
        return OptimizationResult(
            optimization_id=result.job_id,
            status="completed",
            detail=detail,  # type: ignore[arg-type]
            expires_at=result.expires_at,
            request=result.request,
            summary=ResultSummary.model_validate(result.summary) if result.summary is not None else None,
            routes=[RouteMetrics.model_validate(r) for r in result.routes]
            if result.routes is not None
            else None,
            stops=[StopVisit.model_validate(s) for s in result.stops] if result.stops is not None else None,
            unassigned_stop_ids=result.unassigned_stop_ids,
            page=Page.model_validate(result.page.model_dump()) if result.page is not None else None,
        )
    except ValueError:
        raise DomainError(
            ErrorCode.INTERNAL_ERROR,
            "Vepathos returned an unexpected result format.",
            suggestion="Try again later. If it persists, contact Vepathos support.",
            retryable=False,
        ) from None


async def fetch_result_view(
    deps: ToolDeps,
    identity: RequestIdentity,
    job_id: str,
    *,
    detail: str,
    offset: int = 0,
    limit: int | None = None,
    route_id: str | None = None,
) -> OptimizationResult:
    result = await deps.core.get_result(
        identity.call, job_id, view=detail, offset=offset, limit=limit, route_id=route_id
    )
    if result.status == "failed":
        raise failure_error(result)
    if result.status != "completed":
        return pending_output(result, detail)
    return completed_output(result, detail)


def make_get_result_tool(deps: ToolDeps) -> Any:
    async def get_optimization_result(ctx: Context) -> Annotated[CallToolResult, OptimizationResult]:
        async def handle(identity: RequestIdentity, arguments: dict[str, Any]) -> CallToolResult:
            inp = parse_get_result_input(arguments)
            deps.rate_limiter.check(identity.subject, "calls")

            status = await deps.core.get_job(identity.call, inp.optimization_id)
            if not status.is_terminal and deps.settings.result_longpoll_seconds > 0:
                status = await wait_for_terminal(
                    ctx, deps, identity, status, deps.settings.result_longpoll_seconds
                )
            if status.status == "failed":
                raise failure_error(status)
            if status.status != "completed":
                return success_result(pending_output(status, inp.detail))

            output = await fetch_result_view(
                deps,
                identity,
                inp.optimization_id,
                detail=inp.detail,
                offset=inp.offset,
                limit=inp.limit,
                route_id=inp.route_id,
            )
            return success_result(output)

        return await instrumented(TOOL_NAME, ctx, deps, handle)

    return get_optimization_result

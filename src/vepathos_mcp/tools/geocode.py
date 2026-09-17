"""geocode_addresses / get_geocode_result — Smart Import via the Core MCP channel."""

from __future__ import annotations

import logging
from typing import Annotated, Any

from mcp.server.mcpserver.context import Context
from mcp_types import CallToolResult

from vepathos_mcp.clients.core_models import CoreGeocodedStop, CoreGeocodeResult
from vepathos_mcp.errors.codes import DomainError, ErrorCode
from vepathos_mcp.schemas.inputs import parse_geocode_input, parse_get_geocode_input
from vepathos_mcp.schemas.mapping import request_fingerprint
from vepathos_mcp.schemas.outputs import GeocodedStop, GeocodeResult, Progress
from vepathos_mcp.telemetry.logging import log_event
from vepathos_mcp.tools.rendering import success_result
from vepathos_mcp.tools.results import POLL_AFTER_SECONDS
from vepathos_mcp.tools.runtime import RequestIdentity, ToolDeps, instrumented

GEOCODE_TOOL = "geocode_addresses"
GET_GEOCODE_TOOL = "get_geocode_result"
# Smart Import scores below this (0-1, or 0-100) are treated as review.
REVIEW_CONFIDENCE = 0.8


def confidence_ratio(value: float | None) -> float | None:
    if value is None:
        return None
    return value / 100.0 if value > 1 else value


def classify_geocoded_stops(stops: list[GeocodedStop]) -> tuple[list[str], list[str]]:
    unresolved: list[str] = []
    review: list[str] = []
    for stop in stops:
        has_pin = stop.latitude is not None and stop.longitude is not None
        if not has_pin or stop.band == "needs_geocoding":
            unresolved.append(stop.stop_id)
            continue
        score = confidence_ratio(stop.confidence)
        if stop.band == "review" or (score is not None and score < REVIEW_CONFIDENCE):
            review.append(stop.stop_id)
    return unresolved, review


def to_core_geocode_body(inp: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "stops": [
            {
                "id": stop.stop_id,
                "address": stop.address,
                **({} if not stop.city else {"city": stop.city}),
                **({} if not stop.region else {"region": stop.region}),
                **({} if not stop.postcode else {"postcode": stop.postcode}),
                **({} if not stop.country else {"country": stop.country}),
            }
            for stop in inp.addresses
        ]
    }
    if inp.depot is not None:
        body["depot"] = {"lat": inp.depot.latitude, "lng": inp.depot.longitude}
    if inp.city:
        body["city"] = inp.city
    if inp.country:
        body["country"] = inp.country
    if inp.timezone:
        body["timezone"] = inp.timezone
    return body


def _map_stops(rows: list[CoreGeocodedStop] | None) -> list[GeocodedStop] | None:
    if rows is None:
        return None
    return [
        GeocodedStop(
            stop_id=row.id,
            latitude=row.lat,
            longitude=row.lng,
            band=row.band,
            confidence=row.confidence,
            matched_address=row.matched_address,
        )
        for row in rows
    ]


def _from_core(result: CoreGeocodeResult) -> GeocodeResult:
    stops = _map_stops(result.stops)
    unresolved, review = classify_geocoded_stops(stops) if stops else ([], [])
    needs_confirmation = bool(unresolved or review) if stops is not None else None
    return GeocodeResult(
        geocode_id=result.job_id,
        status=result.status,
        submitted_stops=result.submitted_stops,
        resolved_stops=result.resolved_stops,
        unresolved_stop_ids=unresolved or None,
        review_stop_ids=review or None,
        needs_confirmation=needs_confirmation,
        poll_after_seconds=None if result.is_terminal else POLL_AFTER_SECONDS,
        progress=(
            Progress(percent=result.progress.percent, stage=result.progress.stage)
            if result.progress
            else None
        ),
        stops=stops,
    )


async def _wait_geocode(
    ctx: Context, deps: ToolDeps, identity: RequestIdentity, job_id: str, budget: float
) -> CoreGeocodeResult:
    deadline = deps.clock() + budget
    status = await deps.core.get_geocode(identity.call, job_id)
    while not status.is_terminal and deps.clock() < deadline:
        percent = status.progress.percent if status.progress else None
        if percent is not None:
            try:
                await ctx.report_progress(percent, 100, message="geocoding")
            except Exception:
                log_event("progress_notification_failed", logging.DEBUG)
        wait = min(deps.settings.poll_interval_seconds, max(0.2, deadline - deps.clock()))
        await deps.sleep(wait)
        status = await deps.core.get_geocode(identity.call, job_id)
    return status


def make_geocode_tool(deps: ToolDeps) -> Any:
    async def geocode_addresses(ctx: Context) -> Annotated[CallToolResult, GeocodeResult]:
        async def handle(identity: RequestIdentity, arguments: dict[str, Any]) -> CallToolResult:
            inp = parse_geocode_input(arguments)
            deps.rate_limiter.check(identity.subject, "calls")
            body = to_core_geocode_body(inp)
            created = await deps.core.create_geocode(identity.call, body, request_fingerprint(body))
            output = GeocodeResult(
                geocode_id=created.job_id,
                status=created.status,
                submitted_stops=created.submitted_stops,
            )
            if deps.settings.optimize_inline_wait_seconds <= 0:
                output.poll_after_seconds = POLL_AFTER_SECONDS
                return success_result(output)
            status = await _wait_geocode(
                ctx, deps, identity, created.job_id, deps.settings.optimize_inline_wait_seconds
            )
            if status.status == "failed":
                raise DomainError(
                    ErrorCode.INTERNAL_ERROR,
                    status.message or "Geocoding failed.",
                    suggestion="Check the addresses and city or depot, then try again.",
                    details={"geocode_id": status.job_id},
                    retryable=False,
                )
            mapped = _from_core(status)
            if mapped.status != "completed":
                mapped.poll_after_seconds = POLL_AFTER_SECONDS
            return success_result(mapped)

        return await instrumented(GEOCODE_TOOL, ctx, deps, handle)

    return geocode_addresses


def make_get_geocode_tool(deps: ToolDeps) -> Any:
    async def get_geocode_result(ctx: Context) -> Annotated[CallToolResult, GeocodeResult]:
        async def handle(identity: RequestIdentity, arguments: dict[str, Any]) -> CallToolResult:
            inp = parse_get_geocode_input(arguments)
            deps.rate_limiter.check(identity.subject, "calls")
            status = await deps.core.get_geocode(identity.call, inp.geocode_id)
            if not status.is_terminal and deps.settings.result_longpoll_seconds > 0:
                status = await _wait_geocode(
                    ctx, deps, identity, inp.geocode_id, deps.settings.result_longpoll_seconds
                )
            if status.status == "failed":
                raise DomainError(
                    ErrorCode.INTERNAL_ERROR,
                    status.message or "Geocoding failed.",
                    suggestion="Check the addresses and city or depot, then try again.",
                    details={"geocode_id": status.job_id},
                    retryable=False,
                )
            return success_result(_from_core(status))

        return await instrumented(GET_GEOCODE_TOOL, ctx, deps, handle)

    return get_geocode_result

"""Tool output schemas (published as `outputSchema`).

Results are compact by default: a summary plus one page of per-route metrics. Full stop sequences
are paginated and never include coordinates the caller already has.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

JobStatus = Literal["queued", "running", "completed", "failed"]


class OutputModel(BaseModel):
    model_config = ConfigDict(extra="ignore")


class Progress(OutputModel):
    percent: int | None = Field(None, description="Real progress reported by the optimizer, 0-100.")
    stage: str | None = Field(None, description="Current stage, e.g. assigning_stops or sequencing_routes.")


class FullTrialApplied(OutputModel):
    max_stops: int = Field(description="Maximum stops the one-time trial covers.")
    features: list[str] = Field(description="Constraints enabled by the trial for this optimization.")
    quota_charged: bool = Field(False, description="Whether the plan's monthly stop quota was charged.")


class TimeWindowStats(OutputModel):
    stops_with_window: int | None = None
    met: int | None = None
    violated: int | None = None


class ResultSummary(OutputModel):
    stops_submitted: int | None = None
    stops_assigned: int | None = None
    stops_unassigned: int | None = None
    vehicles_available: int | None = None
    vehicles_used: int | None = None
    total_distance_km: float | None = None
    total_duration_minutes: float | None = None
    time_windows: TimeWindowStats | None = None
    charged_stops: int | None = Field(None, description="Stops charged to the plan for this optimization.")


class RouteMetrics(OutputModel):
    route_id: str
    vehicle_id: str | None = None
    stops: int | None = Field(None, description="Number of stops on the route.")
    distance_km: float | None = None
    duration_minutes: float | None = None
    weight_kg: float | None = None
    volume_m3: float | None = None


class StopVisit(OutputModel):
    route_id: str
    sequence: int = Field(description="1-based position of the stop on its route.")
    stop_id: str
    arrival_time: str | None = Field(None, description="Estimated arrival, local HH:MM.")


class Page(OutputModel):
    offset: int
    limit: int
    total: int | None = None
    next_offset: int | None = Field(None, description="Pass as offset to get the next page; null when done.")


class OptimizationResult(OutputModel):
    """Output of get_optimization_result."""

    optimization_id: str
    status: JobStatus
    detail: Literal["summary", "stops", "unassigned"] | None = None
    progress: Progress | None = None
    poll_after_seconds: int | None = Field(
        None, description="When status is queued or running, call again after about this many seconds."
    )
    expires_at: str | None = Field(None, description="When results stop being available (UTC).")
    summary: ResultSummary | None = None
    routes: list[RouteMetrics] | None = None
    stops: list[StopVisit] | None = None
    unassigned_stop_ids: list[str] | None = None
    page: Page | None = None


class OptimizeResult(OutputModel):
    """Output of optimize_delivery_routes."""

    optimization_id: str = Field(description="Handle for get_optimization_result.")
    status: JobStatus
    idempotent_replay: bool = Field(
        False, description="True when identical arguments returned an optimization that already existed."
    )
    submitted_stops: int
    vehicles_available: int | None = None
    schedule_date: str | None = None
    expires_at: str | None = None
    stops_remaining_this_period: int | None = Field(
        None, description="Stops left in the plan's current billing period (null means unlimited)."
    )
    full_trial_applied: FullTrialApplied | None = None
    progress: Progress | None = None
    poll_after_seconds: int | None = None
    result: OptimizationResult | None = Field(
        None, description="Present when the optimization finished within the call."
    )


class GeocodedStop(OutputModel):
    stop_id: str
    latitude: float | None = None
    longitude: float | None = None
    band: Literal["valid", "review", "needs_geocoding"] | None = Field(
        None, description="valid: use as-is. review: check. needs_geocoding: no pin."
    )
    confidence: float | None = Field(None, description="Smart Import score, 0-1. Below 0.8 is review.")


class GeocodeResult(OutputModel):
    """Output of geocode_addresses and get_geocode_result."""

    geocode_id: str = Field(description="Handle for get_geocode_result.")
    status: JobStatus
    submitted_stops: int | None = None
    resolved_stops: int | None = Field(None, description="Stops that received a latitude and longitude.")
    unresolved_stop_ids: list[str] | None = Field(
        None, description="Stops with no pin. Do not invent coordinates. Ask before optimizing."
    )
    review_stop_ids: list[str] | None = Field(
        None,
        description="Pins to confirm (band=review or confidence below 0.8). Ask before routing them.",
    )
    needs_confirmation: bool | None = Field(
        None,
        description="True when any stop is unresolved or in review. Wait for the user before optimize.",
    )
    poll_after_seconds: int | None = None
    progress: Progress | None = None
    stops: list[GeocodedStop] | None = None

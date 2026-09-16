"""Tool output schemas (published as `outputSchema`).

Results are compact by default: a summary plus one page of per-route metrics. Full stop sequences
are paginated and never include coordinates the caller already has.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

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

    optimization_id: str | None = None
    status: JobStatus | None = None
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
    error: dict[str, Any] | None = None

    @model_validator(mode="after")
    def validate_variant(self) -> OptimizationResult:
        success = self.optimization_id is not None and self.status is not None
        if success == (self.error is not None):
            raise ValueError("output must contain either an optimization result or an error")
        return self


class PreflightPlan(OutputModel):
    """The connected plan's side of the check. Absent when the account could not be read."""

    account_label: str | None = Field(None, description="Account that would be charged.")
    plan_name: str | None = None
    max_stops_per_request: int | None = Field(None, description="Null means unlimited on this plan.")
    stops_remaining: int | None = Field(None, description="Before this optimization. Null: unlimited.")
    stops_remaining_after: int | None = Field(None, description="Projected, if it is charged.")
    fits: bool | None = Field(None, description="False when the plan would reject this request.")
    missing_features: list[str] | None = Field(
        None, description="Constraints this request needs that the plan does not include."
    )


class Preflight(OutputModel):
    """What optimize_delivery_routes would send, returned instead of optimizing when confirmed is false."""

    stops: int = Field(description="Stops that would be sent.")
    charges_stops: int = Field(description="Stops this would charge against the plan's period.")
    total_weight_kg: float | None = Field(None, description="Null when no stop declares weight.")
    total_volume_m3: float | None = None
    stops_with_time_window: int = 0
    depot: dict[str, float] | None = None
    vehicle_types: int | None = None
    vehicle_units: int | None = Field(None, description="Total vehicles available across all types.")
    constraints_enforced: list[str] = Field(
        default_factory=list, description="Empty means distance only: no capacity or window is enforced."
    )
    objective: str | None = Field(None, description="Objective this would run with.")
    schedule_date: str | None = None
    route_start_time: str | None = Field(
        None, description="Null means the result carries no wall-clock arrival times."
    )
    time_zone: str | None = None
    service_time_minutes: float | None = None
    stops_identity: str | None = Field(
        None, description="Identity of the depot and stop set, shared by every variant of this day."
    )
    plan: PreflightPlan | None = None
    confirm_with: str = Field(
        description="What to do next: show this to the user and call again with confirmed=true."
    )


class OptimizeResult(OutputModel):
    """Output of optimize_delivery_routes."""

    optimization_id: str | None = Field(None, description="Handle for get_optimization_result.")
    status: JobStatus | None = None
    idempotent_replay: bool = Field(
        False, description="True when identical arguments returned an optimization that already existed."
    )
    submitted_stops: int | None = None
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
    preflight: Preflight | None = Field(
        None,
        description="Present instead of an optimization when confirmed was false: nothing ran, "
        "nothing was charged.",
    )
    error: dict[str, Any] | None = None

    @model_validator(mode="after")
    def validate_variant(self) -> OptimizeResult:
        success = (
            self.optimization_id is not None
            and self.status is not None
            and self.submitted_stops is not None
        )
        # Exactly one of three outcomes: an optimization, a preflight awaiting confirmation, an error.
        variants = [success, self.preflight is not None, self.error is not None]
        if sum(1 for present in variants if present) != 1:
            raise ValueError(
                "output must contain exactly one of an optimization result, a preflight, or an error"
            )
        return self


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

    geocode_id: str | None = Field(None, description="Handle for get_geocode_result.")
    status: JobStatus | None = None
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
    error: dict[str, Any] | None = None

    @model_validator(mode="after")
    def validate_variant(self) -> GeocodeResult:
        success = self.geocode_id is not None and self.status is not None
        if success == (self.error is not None):
            raise ValueError("output must contain either a geocode result or an error")
        return self


class AccountPlan(OutputModel):
    id: str | None = None
    name: str | None = Field(None, description="Plan name as Vepathos shows it, e.g. Free or Enterprise.")
    max_stops_per_request: int | None = Field(
        None, description="Stops allowed in one optimize_delivery_routes call. Null when unlimited."
    )
    max_fleet_units: int | None = Field(
        None, description="Vehicles allowed per optimization. Null when unlimited."
    )
    max_stops_per_route: int | None = Field(
        None, description="Stops allowed on one route. Null when unlimited."
    )
    max_active_optimizations: int | None = Field(None, description="Optimizations that may run at once.")
    features: list[str] | None = Field(
        None,
        description="Constraints this plan includes, e.g. time_windows, weight_capacity, volume_capacity.",
    )


class AccountUsage(OutputModel):
    stops_limit: int | None = Field(
        None, description="Stops included in the billing period. Null when unlimited."
    )
    stops_used: int | None = None
    stops_remaining: int | None = None
    period_start: str | None = None
    period_end: str | None = Field(None, description="When the period's stop quota renews.")


class AccountInfo(OutputModel):
    """Output of get_account: which Vepathos account this connection uses, and what it allows."""

    account_label: str | None = Field(
        None,
        description="Human-readable owner of the connected account: company name, or a masked email.",
    )
    account_id: str | None = Field(None, description="Vepathos account id. Use it to tell accounts apart.")
    plan: AccountPlan | None = None
    usage: AccountUsage | None = None
    full_trial_available: bool | None = Field(
        None, description="Whether the one-time full-feature optimization is still unused."
    )
    error: dict[str, Any] | None = None

    @model_validator(mode="after")
    def validate_variant(self) -> AccountInfo:
        if (self.plan is not None) == (self.error is not None):
            raise ValueError("output must contain either account information or an error")
        return self


class FleetVehicle(OutputModel):
    """A vehicle as the account has it, shaped to drop straight into optimize_delivery_routes."""

    vehicle_id: str = Field(description="Pass as vehicles[].vehicle_id so routes name the real vehicle.")
    name: str | None = Field(None, description="Label the account gave it, for talking to the user.")
    count: int | None = Field(None, description="Units of this vehicle in the fleet.")
    max_weight_kg: float | None = Field(None, description="Payload capacity, kg. Null when not set.")
    max_volume_m3: float | None = Field(None, description="Cargo volume, m3. Null when not set.")


class Fleet(OutputModel):
    fleet_id: str
    name: str | None = None
    total_units: int | None = Field(None, description="Vehicles in the fleet, counting repeats.")
    vehicles: list[FleetVehicle] = Field(default_factory=list)


class FleetCatalog(OutputModel):
    """Output of list_fleet: the account's own fleets and vehicles."""

    fleets: list[Fleet] | None = None
    vehicles: list[FleetVehicle] | None = Field(
        None, description="Vehicles in the account, including any that belong to no fleet."
    )
    empty: bool | None = Field(
        None,
        description="True when the account has no fleet loaded: ask the user to describe the vehicles.",
    )
    error: dict[str, Any] | None = None

    @model_validator(mode="after")
    def validate_variant(self) -> FleetCatalog:
        if (self.fleets is not None) == (self.error is not None):
            raise ValueError("output must contain either a catalog or an error")
        return self

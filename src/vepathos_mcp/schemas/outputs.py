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


class ReplacedPlan(OutputModel):
    plan_id: str = Field(description="Id of the plan that was removed from the library.")
    name: str | None = Field(None, description="Its name, to tell the user which plan was replaced.")


PLAN_ID_DESCRIPTION = "The plan this lives in, in the user's Vepathos account (list_plans)."
ACCOUNT_URL_DESCRIPTION = (
    "Opens the plan in the user's Vepathos account. Requires signing in; private to the account."
)
PLAN_REPLACED_DESCRIPTION = (
    "Set when the library was full: this plan was removed to make room. Tell the user its name."
)
PLAN_TEMPORARY_DESCRIPTION = (
    "True when every library slot is kept or favorite: the plan lives outside the library and is "
    "deleted later. Tell the user."
)


class FreeRetryFields(OutputModel):
    """What the next optimization of a plan costs (the rule the dashboard shares)."""

    next_optimize_charged: bool | None = Field(
        None,
        description="True: the next run of this plan charges its stops to the monthly allowance. "
        "False: it is another try (same stops or fewer, within the 24 h window) — no stops charged. "
        "When speaking to the user: English 'another try', Spanish 'otro intento'; never call a run free.",
    )
    free_retries_allowed: int | None = Field(
        None,
        description="How many other tries a charged run opens on this account plan (field name is "
        "historical; say 'other tries' / 'otros intentos', not that the run is free).",
    )
    free_retries_remaining: int | None = Field(
        None,
        description="Other tries left in the open 24 h window. 0 when no window is open. Tell the user "
        "the count and the deadline (free_retry_window_ends_at); do not call the run free.",
    )
    free_retry_window_ends_at: str | None = Field(
        None,
        description="When the other-try window closes (UTC). Null when no window is open. "
        "Always mention this deadline when next_optimize_charged is false.",
    )
    charged_because: str | None = Field(
        None,
        description="Why the next run is charged: no_billed_run, stops_changed (a stop was added or "
        "moved), allowance_used, window_closed or no_allowance.",
    )


class PlanPlacementFields(OutputModel):
    plan_id: str | None = Field(None, description=PLAN_ID_DESCRIPTION)
    account_url: str | None = Field(None, description=ACCOUNT_URL_DESCRIPTION)
    plan_replaced: ReplacedPlan | None = Field(None, description=PLAN_REPLACED_DESCRIPTION)
    plan_temporary: bool | None = Field(None, description=PLAN_TEMPORARY_DESCRIPTION)


class FullTrialApplied(OutputModel):
    max_stops: int = Field(description="Maximum stops the one-time trial covers.")
    features: list[str] = Field(description="Constraints enabled by the trial for this optimization.")
    quota_charged: bool = Field(False, description="Whether the plan's monthly stop quota was charged.")


class TimeWindowStats(OutputModel):
    stops_with_window: int | None = Field(None, description="Stops that had a time_window in the request.")
    met: int | None = Field(None, description="Assigned stops whose arrival falls inside the window.")
    violated: int | None = Field(
        None, description="Assigned stops whose arrival is outside the window (late or early)."
    )


class ResultSummary(OutputModel):
    stops_submitted: int | None = Field(
        None, description="Stops sent in this optimization (after exclude_stop_ids, if any)."
    )
    stops_assigned: int | None = Field(
        None, description="Stops placed on a route. Prefer this over vehicles_available for sizing talk."
    )
    stops_unassigned: int | None = Field(
        None,
        description="Stops the optimizer could not place. Use detail=unassigned for their ids.",
    )
    vehicles_available: int | None = Field(
        None, description="Fleet units declared in the request (sum of vehicles[].count)."
    )
    vehicles_used: int | None = Field(
        None,
        description="Routes that actually received stops. Often less than vehicles_available "
        "when force_vehicles_fleet_match is off or demand is low.",
    )
    total_distance_km: float | None = Field(
        None, description="Sum of per-route distance_km (driving distance only)."
    )
    total_duration_minutes: float | None = Field(
        None,
        description="Sum of per-route duration_minutes: driving plus service_time at every stop "
        "(and return to depot when the engine includes it). Not last_arrival - first_arrival.",
    )
    time_windows: TimeWindowStats | None = Field(
        None, description="Present when any stop had a time_window; null otherwise."
    )
    charged_stops: int | None = Field(
        None,
        description="Stops billed to the account for this job. 0 on another try of the plan or the trial.",
    )


class RouteMetrics(OutputModel):
    route_id: str = Field(description="Stable id for this route within the optimization.")
    vehicle_id: str | None = Field(
        None, description="vehicles[].vehicle_id that drives this route (echoed from the request)."
    )
    stops: int | None = Field(None, description="Number of stops on the route (excludes the depot).")
    distance_km: float | None = Field(None, description="Driving distance for this route, kilometres.")
    duration_minutes: float | None = Field(
        None,
        description="Route working time in minutes: driving plus schedule.service_time_minutes at each stop.",
    )
    weight_kg: float | None = Field(
        None, description="Total stop weight on this route, kilograms (null if weight unused)."
    )
    volume_m3: float | None = Field(
        None, description="Total stop volume on this route, cubic metres (null if volume unused)."
    )


class StopVisit(OutputModel):
    route_id: str
    sequence: int = Field(description="1-based position of the stop on its route.")
    stop_id: str
    arrival_time: str | None = Field(
        None,
        description="Estimated clock time the vehicle reaches this stop, local HH:MM, anchored on "
        "schedule.route_start_time: driving plus the service time of the stops before it. What "
        "the driver sees.",
    )


class Page(OutputModel):
    offset: int = Field(description="Index of the first item in this page.")
    limit: int = Field(description="Page size requested.")
    total: int | None = Field(None, description="Total items available for this detail view.")
    next_offset: int | None = Field(None, description="Pass as offset to get the next page; null when done.")


class OptimizationResult(OutputModel):
    """Output of get_optimization_result."""

    optimization_id: str | None = Field(None, description="Same handle returned by optimize.")
    status: JobStatus | None = Field(None, description="queued / running / completed / failed.")
    plan_id: str | None = Field(None, description=PLAN_ID_DESCRIPTION)
    history_id: str | None = Field(None, description="This run in the plan's history.")
    account_url: str | None = Field(None, description=ACCOUNT_URL_DESCRIPTION)
    detail: Literal["summary", "stops", "unassigned"] | None = Field(
        None, description="Which slice of the result this payload carries."
    )
    progress: Progress | None = Field(None, description="Present while queued or running.")
    poll_after_seconds: int | None = Field(
        None, description="When status is queued or running, call again after about this many seconds."
    )
    expires_at: str | None = Field(
        None, description="When results stop being available (UTC). Absent: kept with the plan."
    )
    request: dict[str, Any] | None = Field(
        None,
        description="What this optimization ran with: depot, vehicles, schedule, objective, dataset. "
        "Reuse it to repeat or vary the run; confirm the depot and departure with the user first.",
    )
    summary: ResultSummary | None = Field(None, description="Present when detail=summary and completed.")
    routes: list[RouteMetrics] | None = Field(None, description="Per-route metrics page when detail=summary.")
    stops: list[StopVisit] | None = Field(
        None, description="Ordered visits when detail=stops (no coordinates echoed)."
    )
    unassigned_stop_ids: list[str] | None = Field(
        None, description="Ids that could not be routed when detail=unassigned."
    )
    page: Page | None = Field(None, description="Pagination for routes or stops.")
    error: dict[str, Any] | None = Field(None, description="Structured error when the tool call failed.")

    @model_validator(mode="after")
    def validate_variant(self) -> OptimizationResult:
        success = self.optimization_id is not None and self.status is not None
        if success == (self.error is not None):
            raise ValueError("output must contain either an optimization result or an error")
        return self


class DepotResolved(OutputModel):
    """The depot the server geocoded from an address, so the user can confirm it."""

    matched_address: str | None = Field(None, description="The address the geocoder matched.")
    latitude: float = Field(description="Resolved depot latitude.")
    longitude: float = Field(description="Resolved depot longitude.")


class PreflightPlan(OutputModel):
    """The connected plan's side of the check. Absent when the account could not be read."""

    account_label: str | None = Field(None, description="Account that would be charged.")
    plan_name: str | None = Field(None, description="Plan name as shown in Vepathos, e.g. Free.")
    max_stops_per_request: int | None = Field(None, description="Stops allowed in one call. Null: unlimited.")
    stops_remaining: int | None = Field(None, description="Before this optimization. Null: unlimited.")
    stops_remaining_after: int | None = Field(
        None, description="Projected remaining after this run's charge (unchanged on another try)."
    )
    fits: bool | None = Field(None, description="False when the plan would reject this request.")
    missing_features: list[str] | None = Field(
        None, description="Constraints this request needs that the plan does not include."
    )


class Preflight(OutputModel):
    """What optimize would send, returned instead of optimizing when confirmed is false."""

    stops: int = Field(description="Stops that would be sent.")
    charges_stops: int = Field(
        description="Stops this would charge to the monthly allowance. 0 when another try applies."
    )
    total_weight_kg: float | None = Field(None, description="Sum of stop weights. Null when weight unused.")
    total_volume_m3: float | None = Field(None, description="Sum of stop volumes. Null when volume unused.")
    stops_with_time_window: int = Field(0, description="How many stops carry a time_window in this request.")
    depot: dict[str, float] | None = Field(
        None, description="Depot lat/lng that would be sent (keys latitude, longitude)."
    )
    vehicle_types: int | None = Field(None, description="Distinct vehicles[] entries.")
    vehicle_units: int | None = Field(None, description="Total vehicles available (sum of count).")
    constraints_enforced: list[str] = Field(
        default_factory=list,
        description="Empty means distance only: no capacity or window is enforced.",
    )
    objective: str | None = Field(None, description="Objective this would run with.")
    schedule_date: str | None = Field(None, description="Delivery date YYYY-MM-DD (defaults to today in TZ).")
    route_start_time: str | None = Field(
        None, description="Null means the result carries no wall-clock arrival times."
    )
    time_zone: str | None = Field(None, description="IANA TZ for windows and arrival times.")
    service_time_minutes: float | None = Field(
        None, description="Minutes spent at each stop; added into duration_minutes."
    )
    stops_identity: str | None = Field(
        None,
        description="Fingerprint of depot+stops, or plan:… / dataset:… for stored stops.",
    )
    plan_id: str | None = Field(None, description="The stored plan that would run, when there is one.")
    charged_because: str | None = Field(None, description="Why another try does not apply (see list_plans).")
    free_retry_window_ends_at: str | None = Field(None, description="When another try stops applying (UTC).")
    plan: PreflightPlan | None = Field(None, description="Plan/quota check; absent if account lookup failed.")
    depot_resolved: DepotResolved | None = Field(
        None,
        description="Present when the depot was given as an address: tell the user the matched address.",
    )
    warnings: list[dict[str, Any]] | None = Field(
        None,
        description="Non-blocking issues: stop near depot, unknown vehicle_id, tight min/max stops, etc.",
    )
    confirm_with: str = Field(
        description="What to do next: show this to the user and call again with confirmed=true."
    )


class OptimizeResult(OutputModel):
    """Output of optimize_routes."""

    optimization_id: str | None = Field(None, description="Handle for get_optimization_result.")
    plan_id: str | None = Field(None, description=PLAN_ID_DESCRIPTION)
    plan_name: str | None = Field(None, description="Name of that plan.")
    account_url: str | None = Field(None, description=ACCOUNT_URL_DESCRIPTION)
    plan_replaced: ReplacedPlan | None = Field(None, description=PLAN_REPLACED_DESCRIPTION)
    plan_temporary: bool | None = Field(None, description=PLAN_TEMPORARY_DESCRIPTION)
    status: JobStatus | None = Field(
        None, description="queued / running / completed / failed. Absent on a preflight-only reply."
    )
    idempotent_replay: bool = Field(
        False, description="True when identical arguments returned an optimization that already existed."
    )
    submitted_stops: int | None = Field(None, description="Stops accepted into this job.")
    vehicles_available: int | None = Field(
        None, description="Fleet units declared (sum of vehicles[].count)."
    )
    schedule_date: str | None = Field(None, description="Date used for the run, YYYY-MM-DD.")
    expires_at: str | None = Field(
        None, description="While running: when an unfinished run is dropped (UTC). Completed runs stay."
    )
    stops_remaining_this_period: int | None = Field(
        None, description="Stops left in the plan's current billing period (null means unlimited)."
    )
    quota_charged: bool | None = Field(
        None, description="False when this run is another try of the plan or the trial: no stops charged."
    )
    free_retry: bool | None = Field(
        None,
        description="True when this run is another try (no monthly stops charged). Say 'another try' / "
        "'otro intento'; never call it free.",
    )
    free_retries_remaining: int | None = Field(
        None,
        description="Other tries of this plan still available after this run: within 24 h, same stops "
        "or fewer, by plan_id. Say 'other tries' / 'otros intentos'; do not call the run free.",
    )
    full_trial_applied: FullTrialApplied | None = Field(
        None, description="Present when this job used the one-time MCP full trial."
    )
    progress: Progress | None = Field(None, description="Present while queued or running.")
    poll_after_seconds: int | None = Field(
        None, description="Call get_optimization_result again after about this many seconds."
    )
    result: OptimizationResult | None = Field(
        None, description="Present when the optimization finished within the call."
    )
    depot_resolved: DepotResolved | None = Field(
        None,
        description="Present when the depot was given as an address: tell the user the matched address "
        "the routes start from.",
    )
    preflight: Preflight | None = Field(
        None,
        description="Present instead of an optimization when confirmed was false: nothing ran, "
        "nothing was charged.",
    )
    error: dict[str, Any] | None = Field(None, description="Structured error when the tool call failed.")

    @model_validator(mode="after")
    def validate_variant(self) -> OptimizeResult:
        success = (
            self.optimization_id is not None and self.status is not None and self.submitted_stops is not None
        )
        # Exactly one of three outcomes: an optimization, a preflight awaiting confirmation, an error.
        variants = [success, self.preflight is not None, self.error is not None]
        if sum(1 for present in variants if present) != 1:
            raise ValueError(
                "output must contain exactly one of an optimization result, a preflight, or an error"
            )
        return self


class GeocodedStop(OutputModel):
    stop_id: str = Field(description="Same id sent in the geocode request.")
    latitude: float | None = Field(None, description="Null when band is needs_geocoding.")
    longitude: float | None = Field(None, description="Null when band is needs_geocoding.")
    band: Literal["valid", "review", "needs_geocoding"] | None = Field(
        None, description="valid: use as-is. review: check. needs_geocoding: no pin."
    )
    confidence: float | None = Field(None, description="Smart Import score, 0-1. Below 0.8 is review.")
    matched_address: str | None = Field(
        None,
        description="Address the gazetteer matched. Prefer this over confidence alone when reviewing pins.",
    )


class GeocodeResult(OutputModel):
    """Output of geocode_addresses and get_geocode_result."""

    geocode_id: str | None = Field(None, description="Handle for get_geocode_result.")
    status: JobStatus | None = Field(None, description="queued / running / completed / failed.")
    submitted_stops: int | None = Field(None, description="Addresses sent to Smart Import.")
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
    poll_after_seconds: int | None = Field(
        None, description="Call get_geocode_result again after about this many seconds."
    )
    progress: Progress | None = Field(None, description="Present while the geocode job is running.")
    stops: list[GeocodedStop] | None = Field(
        None, description="Per-stop pins when status is completed (includes matched_address)."
    )
    error: dict[str, Any] | None = Field(None, description="Structured error when the tool call failed.")

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
        None, description="Stops allowed in one optimize_routes call. Null when unlimited."
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
    """A vehicle as the account has it, shaped to drop straight into optimize_routes."""

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


class SavedDepot(OutputModel):
    """A depot saved in the account, shaped to drop straight into an optimization's depot."""

    depot_id: str = Field(
        description="Pass as depot.depot_id to optimize_routes, or to manage_catalog when the user "
        "wants it changed."
    )
    name: str | None = Field(None, description="Label the account gave it, for talking to the user.")
    latitude: float = Field(description="Where it is. To route from it, pass depot_id to optimize_routes.")
    longitude: float = Field(description="Where it is. To route from it, pass depot_id to optimize_routes.")


class FleetCatalog(OutputModel):
    """Output of list_fleet: the account's own fleets and vehicles."""

    fleets: list[Fleet] | None = None
    vehicles: list[FleetVehicle] | None = Field(
        None, description="Vehicles in the account, including any that belong to no fleet."
    )
    depots: list[SavedDepot] | None = Field(
        None, description="Depots saved in the account: where routes can start."
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

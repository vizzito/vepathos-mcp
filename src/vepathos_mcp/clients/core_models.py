"""Response models of the Vepathos Core MCP channel (docs/core-channel-contract.md).

Parsing is lenient on purpose: optional metrics the engine did not produce are simply absent, and
unknown fields added by newer Core versions are ignored.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import AliasChoices, BaseModel, ConfigDict, Field

CoreJobStatus = Literal["queued", "running", "completed", "failed"]
TERMINAL_STATUSES: frozenset[str] = frozenset({"completed", "failed"})


class CoreModel(BaseModel):
    model_config = ConfigDict(extra="ignore")


class CoreProgress(CoreModel):
    percent: int | None = None
    stage: str | None = None


class CoreBilling(CoreModel):
    # plan | plan_free_retry | mcp_full_trial (mcp_dataset_replan on jobs from before plans).
    mode: str | None = None
    quota_charged: bool | None = None
    stops_remaining_this_period: int | None = None
    # Retries of the job's plan that stay free after this run completes (same stops or fewer, 24 h).
    free_retries_remaining: int | None = None
    dataset_id: str | None = None


class CoreReplacedPlan(CoreModel):
    """The library plan Core removed to make room for a new one. Core sends `{id, displayName}`."""

    id: str = Field(validation_alias=AliasChoices("id", "plan_id"))
    display_name: str | None = Field(None, validation_alias=AliasChoices("displayName", "name"))


class CoreFreeRetry(CoreModel):
    """What the next optimization of a plan costs, under the rule shared with the dashboard."""

    next_optimize_charged: bool | None = None
    free_retries_allowed: int | None = None
    free_retries_remaining: int | None = None
    free_retry_window_ends_at: str | None = None
    # no_allowance | no_billed_run | window_closed | allowance_used | stops_changed (only when charged)
    charged_because: str | None = None


class CorePlacement(CoreModel):
    """Where stops landed: a plan, and whether loading them rotated the library."""

    plan_id: str | None = None
    account_url: str | None = None
    plan_replaced: CoreReplacedPlan | None = None
    plan_temporary: bool | None = None


class CoreDataset(CoreFreeRetry, CorePlacement):
    """`GET /datasets/{dataset_id}`: counts only, never the stops."""

    dataset_id: str
    status: str | None = None
    filename: str | None = None
    stops: int = 0
    with_weight: int | None = None
    with_volume: int | None = None
    with_time_window: int | None = None
    total_weight_kg: float | None = None
    total_volume_m3: float | None = None
    needs_confirmation: bool | None = None
    expires_at: str | None = None
    last_run: dict[str, Any] | None = None


class CorePlanDepot(CoreModel):
    name: str | None = None
    lat: float | None = None
    lng: float | None = None


class CorePlan(CoreFreeRetry):
    """`GET /plans/{plan_id}` and each row of `GET /plans`: counts and cost, never the stops."""

    plan_id: str
    name: str | None = None
    created_by: str | None = None
    temporary: bool | None = None
    kept: bool | None = None
    favorite: bool | None = None
    # Bumps whenever the plan's stops or settings change. Core replays by idempotency key, so a key
    # derived from the arguments must include it.
    revision: int | None = None
    stops: int = 0
    stops_not_optimizable: int | None = None
    total_weight_kg: float | None = None
    total_volume_m3: float | None = None
    with_weight: int | None = None
    with_volume: int | None = None
    with_time_window: int | None = None
    depot: CorePlanDepot | None = None
    optimizing: bool | None = None
    last_run_history_id: str | None = None
    last_run_at: str | None = None
    account_url: str | None = None
    created_at: str | None = None
    updated_at: str | None = None
    # Only on GET /plans/{plan_id}: the MCP parameters the plan last ran with, or null.
    last_agent_run: dict[str, Any] | None = None


class CorePlanLibrary(CoreModel):
    plans_in_library: int | None = None
    max_plans: int | None = None
    kept_plans: int | None = None
    max_kept_plans: int | None = None


class CorePlanList(CoreModel):
    plans: list[CorePlan] = Field(default_factory=list)
    library: CorePlanLibrary | None = None


class CoreFullTrial(CoreModel):
    max_stops: int
    features: list[str] = Field(default_factory=list)


class CoreFailure(CoreModel):
    code: str | None = None
    message: str | None = None


class CoreJobCreated(CorePlacement):
    job_id: str
    plan_name: str | None = None
    status: CoreJobStatus = "queued"
    idempotent_replay: bool = False
    submitted_stops: int
    vehicles_available: int | None = None
    schedule_date: str | None = None
    created_at: str | None = None
    expires_at: str | None = None
    billing: CoreBilling | None = None
    full_trial_applied: CoreFullTrial | None = None


class CoreJobStatusResponse(CoreModel):
    job_id: str
    status: CoreJobStatus
    plan_id: str | None = None
    # The plan's history run, once the job settled into it.
    history_id: str | None = None
    account_url: str | None = None
    progress: CoreProgress | None = None
    submitted_stops: int | None = None
    created_at: str | None = None
    completed_at: str | None = None
    # Null once a plan job settled: its results are kept with the plan.
    expires_at: str | None = None
    billing: CoreBilling | None = None
    failure: CoreFailure | None = None

    @property
    def is_terminal(self) -> bool:
        return self.status in TERMINAL_STATUSES


class CorePage(CoreModel):
    offset: int = 0
    limit: int
    total: int | None = None
    next_offset: int | None = None


class CoreGeocodedStop(CoreModel):
    id: str
    lat: float | None = None
    lng: float | None = None
    band: Literal["valid", "review", "needs_geocoding"] | None = None
    confidence: float | None = None
    matched_address: str | None = None


class CoreGeocodeCreated(CoreModel):
    job_id: str
    status: CoreJobStatus = "queued"
    submitted_stops: int
    poll_after_ms: int | None = None


class CoreGeocodeResult(CoreModel):
    job_id: str
    status: CoreJobStatus
    submitted_stops: int | None = None
    resolved_stops: int | None = None
    progress: CoreProgress | None = None
    poll_after_ms: int | None = None
    message: str | None = None
    stops: list[CoreGeocodedStop] | None = None

    @property
    def is_terminal(self) -> bool:
        return self.status in TERMINAL_STATUSES


class CoreAccountIdentity(CoreModel):
    """Who the credential belongs to. Core may send a full email; the tool masks it before output."""

    account_id: str | None = None
    email: str | None = None
    company_name: str | None = None


class CoreAccountPlan(CoreModel):
    id: str | None = None
    name: str | None = None
    # null means unlimited on this plan, so absent and null must stay distinguishable.
    max_stops_per_request: int | None = None
    unlimited_stops_per_request: bool = False
    max_fleet_units: int | None = None
    max_stops_per_route: int | None = None
    features: list[str] = Field(default_factory=list)
    max_active_optimizations: int | None = None


class CoreAccountUsage(CoreModel):
    stops_limit: int | None = None
    stops_used: int | None = None
    stops_remaining: int | None = None
    period_start: str | None = None
    period_end: str | None = None


class CoreAccount(CoreModel):
    account: CoreAccountIdentity = Field(default_factory=CoreAccountIdentity)
    plan: CoreAccountPlan = Field(default_factory=CoreAccountPlan)
    usage: CoreAccountUsage = Field(default_factory=CoreAccountUsage)
    full_trial_available: bool | None = None


class CoreCatalogVehicle(CoreModel):
    vehicle_id: str
    name: str | None = None
    count: int | None = None
    max_weight_kg: float | None = None
    max_volume_m3: float | None = None


class CoreCatalogFleet(CoreModel):
    fleet_id: str
    name: str | None = None
    total_units: int | None = None
    vehicles: list[CoreCatalogVehicle] = Field(default_factory=list)


class CoreCatalogDepot(CoreModel):
    depot_id: str
    name: str | None = None
    latitude: float
    longitude: float


class CoreCatalog(CoreModel):
    fleets: list[CoreCatalogFleet] = Field(default_factory=list)
    vehicles: list[CoreCatalogVehicle] = Field(default_factory=list)
    # Absent on a Core from before catalog master data: an older deployment simply has none to show.
    depots: list[CoreCatalogDepot] = Field(default_factory=list)


class CoreVehicleSaved(CoreModel):
    """A vehicle written to (or already in) the account: outcome is created, updated or already_existed."""

    vehicle: CoreCatalogVehicle
    outcome: str = "created"
    account_url: str | None = None


class CoreFleetSaved(CoreModel):
    """A fleet written to (or already in) the account: outcome is created, updated or already_existed."""

    fleet: CoreCatalogFleet
    outcome: str = "created"
    account_url: str | None = None


class CoreDepotSaved(CoreModel):
    depot: CoreCatalogDepot
    outcome: str = "created"
    account_url: str | None = None


class CoreAutomation(CoreModel):
    """A standing rule, as the account channel reports it."""

    automation_id: str
    name: str | None = None
    mode: str | None = None
    status: str | None = None
    enabled: bool | None = None
    timezone: str | None = None
    days: list[int] = Field(default_factory=list)
    looks_at: str | None = None
    window_from: str | None = None
    window_to: str | None = None
    every_minutes: int | None = None
    min_orders: int | None = None
    match_tags: list[str] = Field(default_factory=list)
    fill_by: str | None = None
    vehicle_type_id: str | None = None
    max_units: int | None = None
    stops_per_vehicle: int | None = None
    plan_name: str | None = None
    plan_owned: bool | None = None
    plan_missing: bool | None = None
    depot_name: str | None = None
    last_decision: str | None = None
    last_reason: str | None = None
    last_decided_at: str | None = None
    run_count: int | None = None
    next_look_at: str | None = None
    account_url: str | None = None


class CoreConnectedStore(CoreModel):
    integration_account_id: str
    kind: str | None = None
    name: str | None = None
    last_sync_at: str | None = None


class CoreAutomationLimits(CoreModel):
    max_enabled: int | None = None
    enabled: int | None = None
    max_runs_per_day: int | None = None
    max_stops_per_day: int | None = None


class CoreAutomationList(CoreModel):
    automations: list[CoreAutomation] = Field(default_factory=list)
    stores: list[CoreConnectedStore] = Field(default_factory=list)
    limits: CoreAutomationLimits | None = None
    account_url: str | None = None


class CoreAutomationVehicleOption(CoreModel):
    id: str
    name: str


class CoreAutomationCreated(CoreModel):
    automation: CoreAutomation
    replayed: bool = False
    vehicle_options: list[CoreAutomationVehicleOption] = Field(default_factory=list)
    missing: list[str] = Field(default_factory=list)
    enabled: bool | None = None
    account_url: str | None = None


class CoreJobResult(CoreJobStatusResponse):
    # What the optimization ran with (depot, vehicles, schedule); null for jobs recorded before runs.
    request: dict[str, Any] | None = None
    summary: dict[str, Any] | None = None
    routes: list[dict[str, Any]] | None = None
    stops: list[dict[str, Any]] | None = None
    unassigned_stop_ids: list[str] | None = None
    page: CorePage | None = None

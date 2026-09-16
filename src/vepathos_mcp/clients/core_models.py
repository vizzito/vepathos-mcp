"""Response models of the Vepathos Core MCP channel (docs/core-channel-contract.md).

Parsing is lenient on purpose: optional metrics the engine did not produce are simply absent, and
unknown fields added by newer Core versions are ignored.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

CoreJobStatus = Literal["queued", "running", "completed", "failed"]
TERMINAL_STATUSES: frozenset[str] = frozenset({"completed", "failed"})


class CoreModel(BaseModel):
    model_config = ConfigDict(extra="ignore")


class CoreProgress(CoreModel):
    percent: int | None = None
    stage: str | None = None


class CoreBilling(CoreModel):
    mode: str | None = None
    quota_charged: bool | None = None
    stops_remaining_this_period: int | None = None


class CoreFullTrial(CoreModel):
    max_stops: int
    features: list[str] = Field(default_factory=list)


class CoreFailure(CoreModel):
    code: str | None = None
    message: str | None = None


class CoreJobCreated(CoreModel):
    job_id: str
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
    progress: CoreProgress | None = None
    submitted_stops: int | None = None
    created_at: str | None = None
    completed_at: str | None = None
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


class CoreCatalog(CoreModel):
    fleets: list[CoreCatalogFleet] = Field(default_factory=list)
    vehicles: list[CoreCatalogVehicle] = Field(default_factory=list)


class CoreJobResult(CoreJobStatusResponse):
    summary: dict[str, Any] | None = None
    routes: list[dict[str, Any]] | None = None
    stops: list[dict[str, Any]] | None = None
    unassigned_stop_ids: list[str] | None = None
    page: CorePage | None = None

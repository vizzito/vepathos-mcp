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


class CoreJobResult(CoreJobStatusResponse):
    summary: dict[str, Any] | None = None
    routes: list[dict[str, Any]] | None = None
    stops: list[dict[str, Any]] | None = None
    unassigned_stop_ids: list[str] | None = None
    page: CorePage | None = None

"""Standing rules: read them, and prepare one for the person to switch on.

An automation routes without anyone asking each time — "every weekday at 08:00, take whatever my shop
left waiting, and send the vans out". That is a useful thing for an agent to set up, and a dangerous
thing for one to start: it spends the account's stops unattended, on a schedule nobody is watching.

So the split is deliberate. `list_automations` reads. `create_automation` writes a rule that is
switched OFF, and answers with the link where its owner turns it on. Nothing here can make routes.

The rule keeps a plan of its own, copied from one the account already has: nobody is asked to pick a
container for their deliveries, and the plan id never leaves this module — an id in an agent's hands
is an id it could optimize by hand, spending the rule's stops and moving the revision its next batch
checks against.
"""

from __future__ import annotations

from typing import Annotated, Any

from mcp.server.mcpserver.context import Context
from mcp_types import CallToolResult
from pydantic import Field, ValidationError, model_validator

from vepathos_mcp.clients.core_models import CoreAutomation, CoreAutomationList
from vepathos_mcp.errors.codes import DomainError, ErrorCode
from vepathos_mcp.schemas.inputs import StrictModel, coerce_json_fields, validation_error_to_domain
from vepathos_mcp.schemas.outputs import ACCOUNT_URL_DESCRIPTION, OutputModel
from vepathos_mcp.tools.rendering import success_result
from vepathos_mcp.tools.runtime import RequestIdentity, ToolDeps, instrumented

LIST_TOOL_NAME = "list_automations"
CREATE_TOOL_NAME = "create_automation"

# 15 minutes is the scheduler's own floor: a rule cannot be more precise than the loop that reads it.
MIN_EVERY_MINUTES = 15


class ListAutomationsInput(StrictModel):
    """No arguments: it always answers for the account the credential belongs to."""


class CreateAutomationInput(StrictModel):
    name: str = Field(
        min_length=1, max_length=120, description="What to call the rule, e.g. 'Reparto de la mañana'."
    )
    template_plan_id: str = Field(
        min_length=1,
        max_length=64,
        description=(
            "A saved plan (list_plans) to copy the depot, the vehicles and the optimizer settings from. "
            "It is copied once, not linked: editing or deleting that plan later does not touch the rule."
        ),
    )
    use_plan_stops: bool = Field(
        False,
        description=(
            "True for a route that repeats: the plan's own stops are the batch. False when a store feeds it."
        ),
    )
    integration_account_id: str | None = Field(
        None,
        max_length=64,
        description="A connected store to take orders from (from list_automations `stores`).",
    )
    days: list[int] = Field(
        default_factory=lambda: [1, 2, 3, 4, 5],
        description="Days it looks, 0 = Sunday … 6 = Saturday.",
    )
    looks_at: str | None = Field(
        None,
        pattern=r"^([01]\d|2[0-3]):[0-5]\d$",
        description="One time of day, HH:MM: it looks once, then. Use this for 'every day at 8'.",
    )
    window_from: str | None = Field(
        None, pattern=r"^([01]\d|2[0-3]):[0-5]\d$", description="Start of a window it looks inside."
    )
    window_to: str | None = Field(
        None, pattern=r"^([01]\d|2[0-3]):[0-5]\d$", description="End of that window."
    )
    every_minutes: int | None = Field(
        None, ge=MIN_EVERY_MINUTES, le=1440, description="How often it looks inside the window."
    )
    min_orders: int = Field(
        5, ge=1, le=100_000, description="Fewer waiting orders than this and the slot is skipped."
    )
    match_tags: list[str] = Field(
        default_factory=list,
        max_length=20,
        description="Only orders carrying these tags, e.g. ml:flex, ship:envio-a-domicilio.",
    )
    max_units: int = Field(
        1, ge=1, le=10_000, description="Vehicles the user can actually put on the street."
    )
    stops_per_vehicle: int = Field(25, ge=1, le=10_000, description="Stops each vehicle takes.")
    mode: str = Field(
        "suggest",
        pattern="^(suggest|auto)$",
        description="suggest: it works the routes out and asks. auto: it routes on its own once switched on.",
    )
    timezone: str = Field(
        "UTC", min_length=1, max_length=64, description="IANA time zone the schedule is read in."
    )
    operation_id: str = Field(
        min_length=8,
        max_length=200,
        description=(
            "Your id for this attempt. Sending it twice returns the same rule instead of a second one."
        ),
    )

    @model_validator(mode="after")
    def _check(self) -> CreateAutomationInput:
        if not self.use_plan_stops and not self.integration_account_id:
            raise ValueError("Say where the orders come from: use_plan_stops, or integration_account_id.")
        if self.window_from and self.window_to and self.window_to < self.window_from:
            raise ValueError("window_to is before window_from.")
        if not self.days:
            raise ValueError("A rule that looks on no day never looks.")
        return self


class AutomationView(OutputModel):
    automation_id: str
    name: str | None = None
    mode: str | None = Field(None, description="suggest: it asks. auto: it routes by itself.")
    status: str | None = Field(None, description="draft, enabled or blocked.")
    enabled: bool | None = Field(None, description="False: it decides nothing until the user switches it on.")
    timezone: str | None = None
    days: list[int] | None = Field(None, description="0 = Sunday … 6 = Saturday.")
    looks_at: str | None = Field(None, description="The one time of day it looks, when it looks once.")
    window_from: str | None = None
    window_to: str | None = None
    every_minutes: int | None = None
    min_orders: int | None = None
    match_tags: list[str] | None = Field(None, description="Only orders carrying these qualify.")
    fill_by: str | None = Field(
        None, description="What 'enough to route' is measured in, when it waits for a load."
    )
    max_units: int | None = None
    stops_per_vehicle: int | None = None
    plan_name: str | None = None
    plan_missing: bool | None = Field(
        None, description="Its plan was deleted: it cannot run until that is fixed."
    )
    depot_name: str | None = None
    last_decision: str | None = Field(None, description="fired, suggested, skipped, refused or failed.")
    last_reason: str | None = None
    last_decided_at: str | None = None
    run_count: int | None = Field(None, description="Slots that actually launched.")
    next_look_at: str | None = None
    account_url: str | None = Field(None, description=ACCOUNT_URL_DESCRIPTION)


class ConnectedStore(OutputModel):
    integration_account_id: str = Field(description="Pass to create_automation to take that store's orders.")
    kind: str | None = Field(None, description="mercadolibre, shopify or tiendanube.")
    name: str | None = None
    last_sync_at: str | None = None


class AutomationLimits(OutputModel):
    max_enabled: int | None = Field(
        None, description="How many rules this account may have switched on at once."
    )
    enabled: int | None = None
    max_runs_per_day: int | None = None
    max_stops_per_day: int | None = None


class AutomationsResult(OutputModel):
    automations: list[AutomationView] | None = None
    stores: list[ConnectedStore] | None = Field(None, description="Stores the account has connected.")
    limits: AutomationLimits | None = None
    empty: bool | None = Field(None, description="True: no automation yet.")
    account_url: str | None = Field(None, description=ACCOUNT_URL_DESCRIPTION)


class CreatedAutomationResult(OutputModel):
    automation: AutomationView
    enabled: bool = Field(description="Always false: only the user switches a rule on, in the dashboard.")
    missing: list[str] | None = Field(
        None,
        description="What a run would still lack (depot, fleet). Empty: the user only has to switch it on.",
    )
    account_url: str | None = Field(None, description=ACCOUNT_URL_DESCRIPTION)


def _view(row: CoreAutomation) -> AutomationView:
    return AutomationView(
        automation_id=row.automation_id,
        name=row.name,
        mode=row.mode,
        status=row.status,
        enabled=row.enabled,
        timezone=row.timezone,
        days=row.days or None,
        looks_at=row.looks_at,
        window_from=row.window_from,
        window_to=row.window_to,
        every_minutes=row.every_minutes,
        min_orders=row.min_orders,
        match_tags=row.match_tags or None,
        fill_by=row.fill_by,
        max_units=row.max_units,
        stops_per_vehicle=row.stops_per_vehicle,
        plan_name=row.plan_name,
        # Only when it is true: a false on every row is noise in every answer.
        plan_missing=True if row.plan_missing else None,
        depot_name=row.depot_name,
        last_decision=row.last_decision,
        last_reason=row.last_reason,
        last_decided_at=row.last_decided_at,
        run_count=row.run_count,
        next_look_at=row.next_look_at,
        account_url=row.account_url,
    )


def to_result(payload: CoreAutomationList) -> AutomationsResult:
    rows = [_view(row) for row in payload.automations]
    stores = [
        ConnectedStore(
            integration_account_id=store.integration_account_id,
            kind=store.kind,
            name=store.name,
            last_sync_at=store.last_sync_at,
        )
        for store in payload.stores
    ]
    limits = payload.limits
    return AutomationsResult(
        automations=rows or None,
        stores=stores or None,
        limits=AutomationLimits(**limits.model_dump()) if limits else None,
        empty=True if not rows else None,
        account_url=payload.account_url,
    )


def _minutes(hhmm: str) -> int:
    hours, minutes = hhmm.split(":")
    return int(hours) * 60 + int(minutes)


def to_core_body(inp: CreateAutomationInput) -> dict[str, Any]:
    """The rule as the account channel's schema expects it, with the plan it will own."""

    if inp.looks_at is not None:
        # One time of day is a window that starts and ends at the same minute: that is how the
        # schedule spells "once, at 8", and how every screen reads it back.
        start = end = _minutes(inp.looks_at)
    else:
        start = _minutes(inp.window_from) if inp.window_from else 10 * 60
        end = _minutes(inp.window_to) if inp.window_to else max(start, 14 * 60)
    return {
        "name": inp.name,
        "mode": inp.mode,
        # Never true from here. The account channel refuses to enable on create anyway; sending false
        # is this tool saying the same thing out loud.
        "enabled": False,
        "timezone": inp.timezone,
        "windowDays": sorted(set(inp.days)),
        "windowFromMin": start,
        "windowToMin": end,
        "everyMinutes": inp.every_minutes or max(MIN_EVERY_MINUTES, 120),
        "minOrders": inp.min_orders,
        "maxOrders": None,
        "matchTags": inp.match_tags,
        "objective": "balance",
        "vehicleTypeId": None,
        "maxUnits": inp.max_units,
        "stopsPerVehicle": inp.stops_per_vehicle,
        "onOverflow": "refuse",
        "dailyStopBudget": None,
        "notifyOwnerEmail": True,
        "notifyDrivers": False,
        "ownedPlan": {
            "operationId": inp.operation_id,
            "templatePlanId": inp.template_plan_id,
            "copyStops": inp.use_plan_stops,
            "routeTimezone": inp.timezone,
            **(
                {"source": {"integrationAccountId": inp.integration_account_id}}
                if inp.integration_account_id
                else {}
            ),
        },
    }


def make_list_automations_tool(deps: ToolDeps) -> Any:
    async def list_automations(ctx: Context) -> Annotated[CallToolResult, AutomationsResult]:
        async def handle(identity: RequestIdentity, arguments: dict[str, Any]) -> CallToolResult:
            try:
                ListAutomationsInput.model_validate(arguments or {})
            except ValidationError as exc:
                raise validation_error_to_domain(exc) from None
            deps.rate_limiter.check(identity.subject, "calls")
            payload = await deps.core.list_automations(identity.call)
            return success_result(to_result(payload))

        return await instrumented(LIST_TOOL_NAME, ctx, deps, handle)

    return list_automations


def make_create_automation_tool(deps: ToolDeps) -> Any:
    async def create_automation(ctx: Context) -> Annotated[CallToolResult, CreatedAutomationResult]:
        async def handle(identity: RequestIdentity, arguments: dict[str, Any]) -> CallToolResult:
            try:
                # Some hosts send nested values as JSON strings; the lists here are the ones that suffer.
                inp = CreateAutomationInput.model_validate(
                    coerce_json_fields(arguments or {}, "days", "match_tags")
                )
            except ValidationError as exc:
                raise validation_error_to_domain(exc) from None
            deps.rate_limiter.check(identity.subject, "calls")
            created = await deps.core.create_automation(
                identity.call, to_core_body(inp), operation_id=inp.operation_id
            )
            if created.enabled:
                # The channel is not supposed to be able to do this; if it ever did, say so rather than
                # reporting a rule as prepared while it is already spending the account's stops.
                raise DomainError(
                    ErrorCode.INTERNAL_ERROR,
                    "Vepathos reported the automation as already switched on.",
                    suggestion="Ask the user to check it in the Vepathos dashboard.",
                    retryable=False,
                )
            return success_result(
                CreatedAutomationResult(
                    automation=_view(created.automation),
                    enabled=False,
                    missing=created.missing or None,
                    account_url=created.account_url,
                )
            )

        return await instrumented(CREATE_TOOL_NAME, ctx, deps, handle)

    return create_automation

"""list_plans — the plans in the connected account, and the plan fields every run and import reports.

Every MCP optimization lives in a plan, the same OptimizationPlan the dashboard shows (docs/
architecture-mcp-plans.md). Published on every server: plans exist for every account, whether or not
this server publishes the import tools.
"""

from __future__ import annotations

from typing import Annotated, Any

from mcp.server.mcpserver.context import Context
from mcp_types import CallToolResult
from pydantic import Field, ValidationError, model_validator

from vepathos_mcp.clients.core_models import (
    CoreFreeRetry,
    CorePlacement,
    CorePlan,
    CoreReplacedPlan,
)
from vepathos_mcp.schemas.inputs import PLAN_ID_PATTERN, StrictModel, validation_error_to_domain
from vepathos_mcp.schemas.outputs import (
    ACCOUNT_URL_DESCRIPTION,
    FreeRetryFields,
    OutputModel,
    ReplacedPlan,
)
from vepathos_mcp.tools.rendering import success_result
from vepathos_mcp.tools.runtime import RequestIdentity, ToolDeps, instrumented

TOOL_NAME = "list_plans"


def replaced_plan(core: CoreReplacedPlan | None) -> ReplacedPlan | None:
    return ReplacedPlan(plan_id=core.id, name=core.display_name) if core is not None else None


def placement_fields(data: dict[str, Any] | CorePlacement) -> dict[str, Any]:
    """`plan_id`, `account_url`, `plan_replaced`, `plan_temporary` for an output model.

    `plan_temporary` is only reported when true: false is the normal case and says nothing."""

    placement = data if isinstance(data, CorePlacement) else CorePlacement.model_validate(data)
    return {
        "plan_id": placement.plan_id,
        "account_url": placement.account_url,
        "plan_replaced": replaced_plan(placement.plan_replaced),
        "plan_temporary": True if placement.plan_temporary else None,
    }


def free_retry_fields(data: dict[str, Any] | CoreFreeRetry) -> dict[str, Any]:
    state = data if isinstance(data, CoreFreeRetry) else CoreFreeRetry.model_validate(data)
    return state.model_dump(include=set(FreeRetryFields.model_fields))


class ListPlansInput(StrictModel):
    """Arguments of list_plans."""

    plan_id: str | None = Field(
        None,
        pattern=PLAN_ID_PATTERN,
        description="Return this plan only, with last_agent_run (what it last ran with).",
    )
    query: str | None = Field(
        None, min_length=1, max_length=200, description="Only plans whose name contains this text."
    )
    limit: int = Field(20, ge=1, le=50, description="Plans to return, favorites first, then newest.")


class PlanDepot(OutputModel):
    name: str | None = None
    latitude: float | None = None
    longitude: float | None = None


class PlanSummary(FreeRetryFields):
    plan_id: str = Field(description="Pass to optimize by plan, or to list_plans for its details.")
    name: str | None = None
    created_by: str | None = Field(None, description="agent (a chat) or dashboard.")
    stops: int | None = Field(None, description="Stops in the plan.")
    depot: PlanDepot | None = Field(
        None, description="The plan's depot. Confirm it with the user before reusing it."
    )
    kept: bool | None = Field(None, description="Kept or favorite: never replaced to make room.")
    favorite: bool | None = None
    temporary: bool | None = Field(
        None, description="Outside the library (every slot was protected); deleted later."
    )
    optimizing: bool | None = Field(None, description="A run of this plan is in progress.")
    last_run_at: str | None = Field(None, description="When the plan last ran (UTC). Null: never.")
    updated_at: str | None = None
    account_url: str | None = Field(None, description=ACCOUNT_URL_DESCRIPTION)


class PlanDetail(PlanSummary):
    stops_not_optimizable: int | None = Field(
        None, description="Stops whose id an agent cannot send; the plan must be fixed in the dashboard."
    )
    with_weight: int | None = Field(None, description="Stops carrying weight_kg.")
    with_volume: int | None = Field(None, description="Stops carrying volume_m3.")
    with_time_window: int | None = Field(None, description="Stops carrying a time window.")
    total_weight_kg: float | None = Field(None, description="Sum of stop weights. Null: no stop has one.")
    total_volume_m3: float | None = Field(None, description="Sum of stop volumes. Null: no stop has one.")
    last_run_history_id: str | None = None
    created_at: str | None = None
    last_agent_run: dict[str, Any] | None = Field(
        None,
        description="What an agent last ran this plan with: depot, vehicles, schedule, constraints. "
        "Null when no agent ran it. Offer to repeat or vary it, confirming depot and departure first.",
    )


class PlanLibrary(OutputModel):
    plans_in_library: int | None = None
    max_plans: int | None = Field(
        None, description="Library size. When full, a new plan replaces the oldest unprotected one."
    )
    kept_plans: int | None = None
    max_kept_plans: int | None = Field(None, description="Kept + favorite plans allowed. Null: unlimited.")


class PlansResult(OutputModel):
    """Output of list_plans."""

    plans: list[PlanSummary] | None = Field(None, description="Without plan_id: the account's plans.")
    library: PlanLibrary | None = None
    plan: PlanDetail | None = Field(None, description="With plan_id: that plan.")
    error: dict[str, Any] | None = None

    @model_validator(mode="after")
    def validate_variant(self) -> PlansResult:
        variants = [self.plans is not None, self.plan is not None, self.error is not None]
        if sum(variants) != 1:
            raise ValueError("output must contain exactly one of a plan list, a plan, or an error")
        return self


def _plan_output[PlanT: PlanSummary](plan: CorePlan, model: type[PlanT]) -> PlanT:
    data = plan.model_dump(exclude={"depot"})
    depot = (
        PlanDepot(name=plan.depot.name, latitude=plan.depot.lat, longitude=plan.depot.lng)
        if plan.depot is not None
        else None
    )
    return model.model_validate({**data, "depot": depot})


def make_list_plans_tool(deps: ToolDeps) -> Any:
    async def list_plans(ctx: Context) -> Annotated[CallToolResult, PlansResult]:
        async def handle(identity: RequestIdentity, arguments: dict[str, Any]) -> CallToolResult:
            try:
                inp = ListPlansInput.model_validate(arguments or {})
            except ValidationError as exc:
                raise validation_error_to_domain(exc) from None
            deps.rate_limiter.check(identity.subject, "calls")
            if inp.plan_id is not None:
                plan = await deps.core.get_plan(identity.call, inp.plan_id)
                return success_result(PlansResult(plan=_plan_output(plan, PlanDetail)))
            listed = await deps.core.list_plans(identity.call, limit=inp.limit, query=inp.query)
            return success_result(
                PlansResult(
                    plans=[_plan_output(plan, PlanSummary) for plan in listed.plans],
                    library=(
                        PlanLibrary.model_validate(listed.library.model_dump())
                        if listed.library is not None
                        else None
                    ),
                )
            )

        return await instrumented(TOOL_NAME, ctx, deps, handle)

    return list_plans

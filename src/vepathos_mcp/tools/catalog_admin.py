"""manage_resources — the account's master data: its vehicles and its depots.

Two kinds of facts reach an agent, and only one belongs here. MASTER DATA is what the account keeps:
the vehicles it owns with their capacity, the depots its routes start from. PLAN SETTINGS are what one
run uses: how many vehicles, stops per vehicle, a capacity or a depot for today, a vehicle that is out
tomorrow. "Use 25 vehicles" is a plan setting; read as master data it would write 25 vehicles into
somebody's account.

The shape defends that line so the wording does not have to. `resource` says which kind of row the call
writes, and the fields are flat: one row per call, no `count`, no `available`, no list. A vehicle is a
type with its capacity; how many units run is `count` on the plan. Depots take coordinates, never an
address: the user confirms where it is before it is saved, the same rule optimize follows. Each field
belongs to one resource, and sending a depot's coordinates for a vehicle is refused rather than ignored.

Core converges a create by name, so the same request twice answers the row that exists
(`already_existed`) instead of a second one; the same name with other values is NAME_TAKEN, for the
person to decide. There is no delete, and nothing here spends plan stops.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from mcp.server.mcpserver.context import Context
from mcp_types import CallToolResult
from pydantic import Field, ValidationError, model_validator

from vepathos_mcp.clients.core_models import CoreDepotSaved, CoreVehicleSaved
from vepathos_mcp.schemas.inputs import (
    DEPOT_NAME_MAX,
    StrictModel,
    validation_error_to_domain,
)
from vepathos_mcp.schemas.outputs import FleetVehicle, OutputModel, SavedDepot
from vepathos_mcp.tools.fleet import vehicle_id_for_optimize
from vepathos_mcp.tools.rendering import success_result
from vepathos_mcp.tools.runtime import RequestIdentity, ToolDeps, instrumented

MANAGE_RESOURCES_TOOL = "manage_resources"
VEHICLE_NAME_MAX = 120
NAME_MAX = max(VEHICLE_NAME_MAX, DEPOT_NAME_MAX)
CATALOG_ID_PATTERN = r"^[A-Za-z0-9_.-]{1,64}$"

Resource = Literal["vehicle", "depot"]
Outcome = Literal["created", "updated", "already_existed"]

# Which flat fields belong to which resource. A field sent for the other one is refused, because
# silently dropping it would save a row the user did not describe.
VEHICLE_FIELDS = ("max_weight_kg", "max_volume_m3")
DEPOT_FIELDS = ("latitude", "longitude")


class ManageResourcesInput(StrictModel):
    resource: Resource = Field(
        description="vehicle saves a vehicle the account owns; depot saves a place routes start from."
    )
    action: Literal["create", "update"] = Field(
        description="create saves a new one; update changes one the account already has."
    )
    resource_id: str | None = Field(
        None,
        pattern=CATALOG_ID_PATTERN,
        description="For update: a vehicle_id or depot_id from list_fleet.",
    )
    name: str | None = Field(
        None,
        min_length=1,
        max_length=NAME_MAX,
        description="What the user calls it, e.g. 'Sprinter' or 'Barracas'. Required to create.",
    )
    max_weight_kg: float | None = Field(
        None,
        gt=0,
        le=1_000_000,
        description=(
            "Vehicle only. Payload capacity in kilograms. Omit when the user did not give one: never "
            "invent a capacity."
        ),
    )
    max_volume_m3: float | None = Field(
        None,
        gt=0,
        le=10_000,
        description="Vehicle only. Cargo volume in cubic meters. Omit when the user did not give one.",
    )
    latitude: float | None = Field(
        None,
        ge=-90,
        le=90,
        description=(
            "Depot only. Decimal degrees (WGS84). From geocode_addresses when given an address. "
            "Required to create a depot."
        ),
    )
    longitude: float | None = Field(
        None, ge=-180, le=180, description="Depot only. Decimal degrees (WGS84). Send it with latitude."
    )

    def _given(self, names: tuple[str, ...]) -> list[str]:
        return [name for name in names if getattr(self, name) is not None]

    @model_validator(mode="after")
    def _fields_follow_resource(self) -> ManageResourcesInput:
        stray = self._given(DEPOT_FIELDS if self.resource == "vehicle" else VEHICLE_FIELDS)
        if stray:
            other = "depot" if self.resource == "vehicle" else "vehicle"
            raise ValueError(f"{', '.join(stray)} belong to a {other}, not to a {self.resource}.")
        if (self.latitude is None) != (self.longitude is None):
            raise ValueError("latitude and longitude travel together.")
        return self

    @model_validator(mode="after")
    def _shape_follows_action(self) -> ManageResourcesInput:
        if self.action == "create":
            if self.resource_id is not None:
                raise ValueError("create takes no resource_id; it is for update.")
            if self.name is None:
                raise ValueError(f"create needs a name for the {self.resource}.")
            if self.resource == "depot" and self.latitude is None:
                raise ValueError("create needs latitude and longitude for a depot.")
        else:
            if self.resource_id is None:
                raise ValueError("update needs resource_id.")
            if not self._given(("name", *VEHICLE_FIELDS, *DEPOT_FIELDS)):
                raise ValueError("update needs at least one field to change.")
        return self


_OUTCOME = (
    "created, updated, or already_existed: the account had this exact one, so nothing was written. "
    "Say which to the user."
)


class ManagedResourcesResult(OutputModel):
    resource: Resource = Field(description="Which kind of row was written.")
    vehicle: FleetVehicle | None = Field(None, description="The saved vehicle, when resource=vehicle.")
    depot: SavedDepot | None = Field(None, description="The saved depot, when resource=depot.")
    outcome: Outcome = Field(description=_OUTCOME)
    account_url: str | None = Field(
        None,
        description="Opens the account's saved vehicles and depots in the Vepathos dashboard. "
        "Requires signing in.",
    )


def core_body(inp: ManageResourcesInput) -> dict[str, Any]:
    """What Core receives: the row on create, only the changed fields on update. Same shape either
    way, because Core's create and update take the same field names."""

    fields = ("name", *(VEHICLE_FIELDS if inp.resource == "vehicle" else DEPOT_FIELDS))
    return {name: getattr(inp, name) for name in fields if getattr(inp, name) is not None}


def _outcome(raw: str, action: str) -> Outcome:
    if raw == "already_existed":
        return "already_existed"
    if raw in ("created", "updated"):
        return "created" if raw == "created" else "updated"
    # A newer Core may say something else; what the call did is still known from the action.
    return "created" if action == "create" else "updated"


def vehicle_result(saved: CoreVehicleSaved, action: str) -> ManagedResourcesResult:
    row = saved.vehicle
    return ManagedResourcesResult(
        resource="vehicle",
        vehicle=FleetVehicle(
            # Same reshaping as list_fleet, so the id drops straight into an optimization.
            vehicle_id=vehicle_id_for_optimize(row.vehicle_id, set()),
            name=row.name,
            count=row.count,
            max_weight_kg=row.max_weight_kg,
            max_volume_m3=row.max_volume_m3,
        ),
        outcome=_outcome(saved.outcome, action),
        account_url=saved.account_url,
    )


def depot_result(saved: CoreDepotSaved, action: str) -> ManagedResourcesResult:
    return ManagedResourcesResult(
        resource="depot",
        depot=SavedDepot(**saved.depot.model_dump()),
        outcome=_outcome(saved.outcome, action),
        account_url=saved.account_url,
    )


def make_manage_resources_tool(deps: ToolDeps) -> Any:
    async def manage_resources(ctx: Context) -> Annotated[CallToolResult, ManagedResourcesResult]:
        async def handle(identity: RequestIdentity, arguments: dict[str, Any]) -> CallToolResult:
            from vepathos_mcp.schemas.mapping import request_fingerprint

            try:
                inp = ManageResourcesInput.model_validate(arguments or {})
            except ValidationError as exc:
                raise validation_error_to_domain(exc) from None
            deps.rate_limiter.check(identity.subject, "catalog_writes")
            body = core_body(inp)
            if inp.resource == "vehicle":
                if inp.action == "create":
                    saved_vehicle = await deps.core.create_vehicle(
                        identity.call, body, idempotency_key=f"vehicle:{request_fingerprint(body)}"
                    )
                else:
                    assert inp.resource_id is not None
                    saved_vehicle = await deps.core.update_vehicle(identity.call, inp.resource_id, body)
                return success_result(vehicle_result(saved_vehicle, inp.action))
            if inp.action == "create":
                saved_depot = await deps.core.create_depot(
                    identity.call, body, idempotency_key=f"depot:{request_fingerprint(body)}"
                )
            else:
                assert inp.resource_id is not None
                saved_depot = await deps.core.update_depot(identity.call, inp.resource_id, body)
            return success_result(depot_result(saved_depot, inp.action))

        return await instrumented(MANAGE_RESOURCES_TOOL, ctx, deps, handle)

    return manage_resources

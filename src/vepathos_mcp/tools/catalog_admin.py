"""manage_vehicle / manage_depot — the account's master data.

Two kinds of facts reach an agent, and only one belongs here. MASTER DATA is what the account keeps:
the vehicles it owns with their capacity, the depots its routes start from. PLAN SETTINGS are what one
run uses: how many vehicles, stops per vehicle, a capacity or a depot for today, a vehicle that is out
tomorrow. "Use 25 vehicles" is a plan setting; read as master data it would write 25 vehicles into
somebody's account.

The shape defends that line so the wording does not have to: one vehicle per call (a vehicle is a type
with its capacity; how many units run is `count` on the plan), and no `count`, no `available`, no list.
A call writes one row at most. Depots take coordinates, never an address: the user confirms where it
is before it is saved, the same rule optimize follows.

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
    coerce_json_fields,
    validation_error_to_domain,
)
from vepathos_mcp.schemas.outputs import FleetVehicle, OutputModel, SavedDepot
from vepathos_mcp.tools.fleet import vehicle_id_for_optimize
from vepathos_mcp.tools.rendering import success_result
from vepathos_mcp.tools.runtime import RequestIdentity, ToolDeps, instrumented

MANAGE_VEHICLE_TOOL = "manage_vehicle"
MANAGE_DEPOT_TOOL = "manage_depot"
VEHICLE_NAME_MAX = 120
CATALOG_ID_PATTERN = r"^[A-Za-z0-9_.-]{1,64}$"

Outcome = Literal["created", "updated", "already_existed"]

_WEIGHT = "Payload capacity in kilograms. Omit when the user did not give one: never invent a capacity."
_VOLUME = "Cargo volume in cubic meters. Omit when the user did not give one."


class VehicleSpec(StrictModel):
    """A vehicle to save: a type with its capacity, not a number of units."""

    name: str = Field(
        min_length=1, max_length=VEHICLE_NAME_MAX, description="What the user calls it, e.g. 'Sprinter'."
    )
    max_weight_kg: float | None = Field(None, gt=0, le=1_000_000, description=_WEIGHT)
    max_volume_m3: float | None = Field(None, gt=0, le=10_000, description=_VOLUME)


class VehicleChanges(StrictModel):
    """Only the fields to change; what is omitted stays as it is."""

    name: str | None = Field(None, min_length=1, max_length=VEHICLE_NAME_MAX, description="New name.")
    max_weight_kg: float | None = Field(None, gt=0, le=1_000_000, description="New payload capacity, kg.")
    max_volume_m3: float | None = Field(None, gt=0, le=10_000, description="New cargo volume, m3.")

    @model_validator(mode="after")
    def _something_changes(self) -> VehicleChanges:
        if self.name is None and self.max_weight_kg is None and self.max_volume_m3 is None:
            raise ValueError("changes needs at least one field.")
        return self


class ManageVehicleInput(StrictModel):
    action: Literal["create", "update"] = Field(
        description="create saves a new vehicle; update changes one the account already has."
    )
    vehicle: VehicleSpec | None = Field(None, description="For create: the vehicle to save.")
    vehicle_id: str | None = Field(
        None,
        pattern=CATALOG_ID_PATTERN,
        description="For update: a vehicle_id from list_fleet's top-level vehicles.",
    )
    changes: VehicleChanges | None = Field(None, description="For update: the fields to change.")

    @model_validator(mode="after")
    def _shape_follows_action(self) -> ManageVehicleInput:
        if self.action == "create":
            if self.vehicle is None:
                raise ValueError("create needs vehicle.")
            if self.vehicle_id is not None or self.changes is not None:
                raise ValueError("create takes vehicle only; vehicle_id and changes are for update.")
        else:
            if self.vehicle_id is None or self.changes is None:
                raise ValueError("update needs vehicle_id and changes.")
            if self.vehicle is not None:
                raise ValueError("update takes vehicle_id and changes; vehicle is for create.")
        return self


class DepotSpec(StrictModel):
    """A depot to save: a name and where it is."""

    name: str = Field(
        min_length=1, max_length=DEPOT_NAME_MAX, description="What the user calls it, e.g. 'Barracas'."
    )
    latitude: float = Field(
        ge=-90, le=90, description="Decimal degrees (WGS84). From geocode_addresses when given an address."
    )
    longitude: float = Field(ge=-180, le=180, description="Decimal degrees (WGS84).")


class DepotChanges(StrictModel):
    """Only the fields to change. Moving a depot takes both coordinates."""

    name: str | None = Field(None, min_length=1, max_length=DEPOT_NAME_MAX, description="New name.")
    latitude: float | None = Field(None, ge=-90, le=90, description="New latitude; send longitude too.")
    longitude: float | None = Field(None, ge=-180, le=180, description="New longitude; send latitude too.")

    @model_validator(mode="after")
    def _something_changes(self) -> DepotChanges:
        if (self.latitude is None) != (self.longitude is None):
            raise ValueError("latitude and longitude change together.")
        if self.name is None and self.latitude is None:
            raise ValueError("changes needs at least one field.")
        return self


class ManageDepotInput(StrictModel):
    action: Literal["create", "update"] = Field(
        description="create saves a new depot; update renames or moves one the account already has."
    )
    depot: DepotSpec | None = Field(None, description="For create: the depot to save.")
    depot_id: str | None = Field(
        None, pattern=CATALOG_ID_PATTERN, description="For update: a depot_id from list_fleet's depots."
    )
    changes: DepotChanges | None = Field(None, description="For update: the fields to change.")

    @model_validator(mode="after")
    def _shape_follows_action(self) -> ManageDepotInput:
        if self.action == "create":
            if self.depot is None:
                raise ValueError("create needs depot.")
            if self.depot_id is not None or self.changes is not None:
                raise ValueError("create takes depot only; depot_id and changes are for update.")
        else:
            if self.depot_id is None or self.changes is None:
                raise ValueError("update needs depot_id and changes.")
            if self.depot is not None:
                raise ValueError("update takes depot_id and changes; depot is for create.")
        return self


_OUTCOME = (
    "created, updated, or already_existed: the account had this exact one, so nothing was written. "
    "Say which to the user."
)


class ManagedVehicleResult(OutputModel):
    vehicle: FleetVehicle
    outcome: Outcome = Field(description=_OUTCOME)
    account_url: str | None = Field(
        None,
        description="Opens the account's saved vehicles in the Vepathos dashboard. Requires signing in.",
    )


class ManagedDepotResult(OutputModel):
    depot: SavedDepot
    outcome: Outcome = Field(description=_OUTCOME)
    account_url: str | None = Field(
        None, description="Opens the account's saved depots in the Vepathos dashboard. Requires signing in."
    )


def vehicle_body(inp: ManageVehicleInput) -> dict[str, Any]:
    """What Core receives: the vehicle on create, only the changed fields on update."""

    source = inp.vehicle if inp.action == "create" else inp.changes
    assert source is not None  # the input validator guarantees it
    return source.model_dump(exclude_none=True)


def depot_body(inp: ManageDepotInput) -> dict[str, Any]:
    source = inp.depot if inp.action == "create" else inp.changes
    assert source is not None  # the input validator guarantees it
    return source.model_dump(exclude_none=True)


def _outcome(raw: str, action: str) -> Outcome:
    if raw == "already_existed":
        return "already_existed"
    if raw in ("created", "updated"):
        return "created" if raw == "created" else "updated"
    # A newer Core may say something else; what the call did is still known from the action.
    return "created" if action == "create" else "updated"


def vehicle_result(saved: CoreVehicleSaved, action: str) -> ManagedVehicleResult:
    row = saved.vehicle
    return ManagedVehicleResult(
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


def depot_result(saved: CoreDepotSaved, action: str) -> ManagedDepotResult:
    return ManagedDepotResult(
        depot=SavedDepot(**saved.depot.model_dump()),
        outcome=_outcome(saved.outcome, action),
        account_url=saved.account_url,
    )


def make_manage_vehicle_tool(deps: ToolDeps) -> Any:
    async def manage_vehicle(ctx: Context) -> Annotated[CallToolResult, ManagedVehicleResult]:
        async def handle(identity: RequestIdentity, arguments: dict[str, Any]) -> CallToolResult:
            from vepathos_mcp.schemas.mapping import request_fingerprint

            try:
                inp = ManageVehicleInput.model_validate(
                    coerce_json_fields(arguments or {}, "vehicle", "changes")
                )
            except ValidationError as exc:
                raise validation_error_to_domain(exc) from None
            deps.rate_limiter.check(identity.subject, "catalog_writes")
            body = vehicle_body(inp)
            if inp.action == "create":
                saved = await deps.core.create_vehicle(
                    identity.call, body, idempotency_key=f"vehicle:{request_fingerprint(body)}"
                )
            else:
                assert inp.vehicle_id is not None
                saved = await deps.core.update_vehicle(identity.call, inp.vehicle_id, body)
            return success_result(vehicle_result(saved, inp.action))

        return await instrumented(MANAGE_VEHICLE_TOOL, ctx, deps, handle)

    return manage_vehicle


def make_manage_depot_tool(deps: ToolDeps) -> Any:
    async def manage_depot(ctx: Context) -> Annotated[CallToolResult, ManagedDepotResult]:
        async def handle(identity: RequestIdentity, arguments: dict[str, Any]) -> CallToolResult:
            from vepathos_mcp.schemas.mapping import request_fingerprint

            try:
                inp = ManageDepotInput.model_validate(coerce_json_fields(arguments or {}, "depot", "changes"))
            except ValidationError as exc:
                raise validation_error_to_domain(exc) from None
            deps.rate_limiter.check(identity.subject, "catalog_writes")
            body = depot_body(inp)
            if inp.action == "create":
                saved = await deps.core.create_depot(
                    identity.call, body, idempotency_key=f"depot:{request_fingerprint(body)}"
                )
            else:
                assert inp.depot_id is not None
                saved = await deps.core.update_depot(identity.call, inp.depot_id, body)
            return success_result(depot_result(saved, inp.action))

        return await instrumented(MANAGE_DEPOT_TOOL, ctx, deps, handle)

    return manage_depot

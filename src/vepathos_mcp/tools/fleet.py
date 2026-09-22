"""list_fleet — the account's own vehicles and depots, ready to pass to optimize_routes.

Without it an assistant invents a fleet, and the plan it produces is geometric only: it cannot
respect what a vehicle actually carries, and the route ids mean nothing to whoever drives them.

Catalog ids are free-form in Core but `optimize_routes` accepts a narrower vehicle_id, so
ids are normalized here — the tool's output is meant to be copied straight into an optimization.
"""

from __future__ import annotations

import re
from typing import Annotated, Any

from mcp.server.mcpserver.context import Context
from mcp_types import CallToolResult

from vepathos_mcp.clients.core_models import CoreCatalog, CoreCatalogVehicle
from vepathos_mcp.schemas.inputs import VEHICLE_ID_PATTERN, parse_list_fleet_input
from vepathos_mcp.schemas.outputs import Fleet, FleetCatalog, FleetVehicle, SavedDepot
from vepathos_mcp.tools.rendering import success_result
from vepathos_mcp.tools.runtime import RequestIdentity, ToolDeps, instrumented

TOOL_NAME = "list_fleet"
MAX_VEHICLE_ID_CHARS = 32
_INVALID_ID_CHARS = re.compile(r"[^A-Za-z0-9_.-]")
_VALID_VEHICLE_ID = re.compile(VEHICLE_ID_PATTERN)


def vehicle_id_for_optimize(raw: str, taken: set[str]) -> str:
    """A catalog id reshaped to what optimize accepts, kept unique within one answer."""

    candidate = _INVALID_ID_CHARS.sub("-", raw)[:MAX_VEHICLE_ID_CHARS].strip("-") or "vehicle"
    if candidate not in taken and _VALID_VEHICLE_ID.match(candidate):
        taken.add(candidate)
        return candidate
    # Truncation and substitution can collide; a suffix keeps every vehicle addressable.
    stem = candidate[: MAX_VEHICLE_ID_CHARS - 4]
    for suffix in range(2, 1000):
        unique = f"{stem}-{suffix}"
        if unique not in taken:
            taken.add(unique)
            return unique
    taken.add(candidate)
    return candidate


def _vehicle(row: CoreCatalogVehicle, taken: set[str]) -> FleetVehicle:
    return FleetVehicle(
        vehicle_id=vehicle_id_for_optimize(row.vehicle_id, taken),
        name=row.name,
        count=row.count,
        max_weight_kg=row.max_weight_kg,
        max_volume_m3=row.max_volume_m3,
    )


def to_catalog(catalog: CoreCatalog) -> FleetCatalog:
    fleets: list[Fleet] = []
    for fleet in catalog.fleets:
        # Ids only need to be unique within one optimization, so each fleet gets its own namespace.
        taken: set[str] = set()
        fleets.append(
            Fleet(
                fleet_id=fleet.fleet_id,
                name=fleet.name,
                total_units=fleet.total_units,
                vehicles=[_vehicle(row, taken) for row in fleet.vehicles],
            )
        )
    loose: set[str] = set()
    vehicles = [_vehicle(row, loose) for row in catalog.vehicles]
    return FleetCatalog(
        fleets=fleets,
        vehicles=vehicles or None,
        depots=[SavedDepot(**row.model_dump()) for row in catalog.depots] or None,
        empty=True if not fleets and not vehicles else None,
    )


def make_list_fleet_tool(deps: ToolDeps) -> Any:
    async def list_fleet(ctx: Context) -> Annotated[CallToolResult, FleetCatalog]:
        async def handle(identity: RequestIdentity, arguments: dict[str, Any]) -> CallToolResult:
            parse_list_fleet_input(arguments)
            deps.rate_limiter.check(identity.subject, "calls")
            catalog = await deps.core.get_catalog(identity.call)
            return success_result(to_catalog(catalog))

        return await instrumented(TOOL_NAME, ctx, deps, handle)

    return list_fleet

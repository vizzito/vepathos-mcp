"""Preflight warnings and hard capacity checks for optimize requests."""

from __future__ import annotations

import math
from typing import Any

from vepathos_mcp.errors.codes import DomainError, ErrorCode
from vepathos_mcp.schemas.inputs import OptimizeInput

# Keep in sync with Core STOP_MARGIN_RATIO / motor constant.
STOP_MARGIN_RATIO = 0.8
DEPOT_NEAR_METERS = 100.0


def haversine_m(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    r = 6_371_000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lng2 - lng1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    return 2 * r * math.asin(min(1.0, math.sqrt(a)))


def reject_impossible(inp: OptimizeInput) -> None:
    """Raise INVALID_INPUT for plans that cannot succeed."""

    fleet_slots = 0
    for vehicle in inp.vehicles:
        if vehicle.max_stops is not None:
            fleet_slots += vehicle.count * vehicle.max_stops
    if fleet_slots > 0 and len(inp.stops) > fleet_slots:
        raise DomainError(
            ErrorCode.INVALID_INPUT,
            "More stops than the fleet can carry under max_stops.",
            suggestion="Raise max_stops, add vehicles, or remove stops.",
            details={
                "stops": len(inp.stops),
                "fleet_max_stops": fleet_slots,
                "field": "vehicles.max_stops",
            },
        )

    if inp.uses_weight:
        total_weight = sum(s.weight_kg or 0 for s in inp.stops)
        fleet_weight = sum((v.max_weight_kg or 0) * v.count for v in inp.vehicles)
        if fleet_weight > 0 and total_weight > fleet_weight:
            raise DomainError(
                ErrorCode.INVALID_INPUT,
                "Total stop weight exceeds fleet capacity.",
                suggestion="Add vehicles or reduce weight.",
                details={
                    "total_weight_kg": total_weight,
                    "fleet_capacity_kg": fleet_weight,
                    "field": "vehicles.max_weight_kg",
                },
            )
        heaviest = max((s.weight_kg or 0) for s in inp.stops)
        biggest = max((v.max_weight_kg or 0) for v in inp.vehicles)
        if biggest > 0 and heaviest > biggest:
            raise DomainError(
                ErrorCode.INVALID_INPUT,
                "A stop is heavier than the largest vehicle.",
                suggestion="Use a larger vehicle or split the stop.",
                details={"stop_weight_kg": heaviest, "max_vehicle_kg": biggest, "field": "stops.weight_kg"},
            )

    if inp.uses_volume:
        total_vol = sum(s.volume_m3 or 0 for s in inp.stops)
        fleet_vol = sum((v.max_volume_m3 or 0) * v.count for v in inp.vehicles)
        if fleet_vol > 0 and total_vol > fleet_vol:
            raise DomainError(
                ErrorCode.INVALID_INPUT,
                "Total stop volume exceeds fleet capacity.",
                suggestion="Add vehicles or reduce volume.",
                details={
                    "total_volume_m3": total_vol,
                    "fleet_capacity_m3": fleet_vol,
                    "field": "vehicles.max_volume_m3",
                },
            )

    start = inp.schedule.route_start_time if inp.schedule else None
    if start:
        for stop in inp.stops:
            if stop.time_window and stop.time_window.end <= start:
                raise DomainError(
                    ErrorCode.INVALID_INPUT,
                    "A time window ends before route_start_time.",
                    suggestion="Move route_start_time earlier or widen the window.",
                    details={
                        "stop_id": stop.stop_id,
                        "time_window_end": stop.time_window.end,
                        "route_start_time": start,
                        "field": "stops.time_window",
                    },
                )


def collect_warnings(
    inp: OptimizeInput,
    *,
    fleet_vehicle_ids: set[str] | None = None,
) -> list[dict[str, Any]]:
    """Non-blocking warnings for preflight and summaries."""

    warnings: list[dict[str, Any]] = []
    depot = inp.depot

    near: list[str] = []
    for stop in inp.stops:
        if haversine_m(depot.latitude, depot.longitude, stop.latitude, stop.longitude) < DEPOT_NEAR_METERS:
            near.append(stop.stop_id)
    if near:
        warnings.append(
            {
                "code": "stop_near_depot",
                "stop_ids": near[:50],
                "message": f"{len(near)} stop(s) are within {int(DEPOT_NEAR_METERS)} m of the depot.",
            }
        )

    seen: dict[tuple[float, float], list[str]] = {}
    for stop in inp.stops:
        key = (round(stop.latitude, 5), round(stop.longitude, 5))
        seen.setdefault(key, []).append(stop.stop_id)
    for ids in seen.values():
        if len(ids) > 1:
            warnings.append(
                {
                    "code": "duplicate_coordinates",
                    "stop_ids": ids[:50],
                    "message": f"{len(ids)} stops share the same coordinates.",
                }
            )

    if fleet_vehicle_ids:
        unknown = [v.vehicle_id for v in inp.vehicles if v.vehicle_id not in fleet_vehicle_ids]
        if unknown:
            warnings.append(
                {
                    "code": "unknown_vehicle_id",
                    "stop_ids": [],
                    "message": "vehicle_id not in list_fleet: " + ", ".join(unknown[:10]),
                    "vehicle_ids": unknown[:20],
                }
            )

    if inp.schedule is None or inp.schedule.route_start_time is None:
        warnings.append(
            {
                "code": "missing_route_start_time",
                "stop_ids": [],
                "message": "No route_start_time: arrival times will not match the driver's day.",
            }
        )

    for vehicle in inp.vehicles:
        if vehicle.min_stops is None or vehicle.max_stops is None:
            continue
        floor = math.floor(vehicle.max_stops * STOP_MARGIN_RATIO)
        if vehicle.min_stops > floor:
            units = inp.vehicles_available
            strength = "strong" if units >= 20 else "moderate" if units >= 5 else "mild"
            warnings.append(
                {
                    "code": "tight_stop_margin",
                    "stop_ids": [],
                    "message": (
                        f"min_stops {vehicle.min_stops} is above the recommended "
                        f"floor(max_stops×{STOP_MARGIN_RATIO})={floor} for {vehicle.vehicle_id} "
                        f"({strength}; {units} vehicle units)."
                    ),
                    "vehicle_id": vehicle.vehicle_id,
                }
            )

    # Depot far from the stop cloud (rough: > 50 km from centroid).
    if inp.stops:
        clat = sum(s.latitude for s in inp.stops) / len(inp.stops)
        clng = sum(s.longitude for s in inp.stops) / len(inp.stops)
        if haversine_m(depot.latitude, depot.longitude, clat, clng) > 50_000:
            warnings.append(
                {
                    "code": "depot_far_from_stops",
                    "stop_ids": [],
                    "message": "Depot is more than 50 km from the stop centroid — check coordinates.",
                }
            )

    return warnings

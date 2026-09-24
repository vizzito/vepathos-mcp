"""Preflight warnings and hard capacity checks for optimize requests."""

from __future__ import annotations

import math
from collections.abc import Iterable
from typing import Any, Protocol

from vepathos_mcp.errors.codes import DomainError, ErrorCode
from vepathos_mcp.schemas.inputs import OptimizeInput

# The per-vehicle stop band every channel shares. The engine applies it to every request
# (route-optimizer-app src/helpers/stop_band.py); Core (validate.ts) and this preflight mirror it
# only to tell the user before the run.
DEFAULT_MIN_STOPS_RATIO = 0.5
MIN_STOP_BAND_MARGIN = 0.10
FLEET_STOP_MARGIN = 0.05
DEPOT_NEAR_METERS = 100.0
# Vehicles fill up to this share of capacity unless the request sets max_load_ratio (every channel).
DEFAULT_MAX_LOAD_RATIO = 0.95


class BandVehicle(Protocol):
    vehicle_id: str
    count: int
    min_stops: int | None
    max_stops: int | None


def resolve_stop_band(min_stops: int | None, max_stops: int | None) -> tuple[int | None, str | None]:
    """The minimum the engine applies to one vehicle, and why it differs from what was sent."""

    if max_stops is None or max_stops < 1:
        return min_stops, None
    if min_stops is None:
        return math.floor(max_stops * DEFAULT_MIN_STOPS_RATIO), "default"
    ceiling = math.floor(max_stops * (1 - MIN_STOP_BAND_MARGIN))
    if min_stops > ceiling:
        return ceiling, "margin"
    return min_stops, None


def stop_band_warnings(vehicles: Iterable[BandVehicle], stop_count: int) -> list[dict[str, Any]]:
    """What the engine will change in the min/max stop band, said before the run."""

    warnings: list[dict[str, Any]] = []
    applied: list[tuple[BandVehicle, int]] = []
    for vehicle in vehicles:
        min_applied, reason = resolve_stop_band(vehicle.min_stops, vehicle.max_stops)
        if vehicle.max_stops is None or min_applied is None:
            continue
        applied.append((vehicle, min_applied))
        if reason == "margin":
            warnings.append(
                {
                    "code": "stop_band_margin",
                    "stop_ids": [],
                    "vehicle_id": vehicle.vehicle_id,
                    "message": (
                        f"min_stops {vehicle.min_stops} for {vehicle.vehicle_id} will run as {min_applied}: "
                        f"the minimum must stay at least {round(MIN_STOP_BAND_MARGIN * 100)}% under "
                        f"max_stops {vehicle.max_stops}."
                    ),
                }
            )
    units = sum(vehicle.count for vehicle, _ in applied)
    if stop_count <= 0 or units <= 0:
        return warnings
    limit = math.ceil(stop_count * (1 - FLEET_STOP_MARGIN))
    required = sum(vehicle.count * min_applied for vehicle, min_applied in applied)
    if required > limit:
        per_vehicle = math.floor(limit / units)
        warnings.append(
            {
                "code": "fleet_min_above_stops",
                "stop_ids": [],
                "message": (
                    f"The vehicles' minimums add up to {required} stops for {stop_count}: the engine will "
                    f"lower each minimum above {per_vehicle} to {per_vehicle}. Omitted min_stops count "
                    f"as 50% of max_stops."
                ),
            }
        )
    return warnings


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


OUTLIER_MIN_METERS = 10_000.0
OUTLIER_MEDIAN_FACTOR = 5.0


def outlier_stop_warnings(inp: OptimizeInput) -> list[dict[str, Any]]:
    """Stops far from all the others: a wrong geocode (Boston Common from memory, a pin in another
    city) sits well outside the cloud. Flags stops more than 10 km and 5x the median distance from
    the stops' centroid."""

    if len(inp.stops) < 4:
        return []
    clat = sum(s.latitude for s in inp.stops) / len(inp.stops)
    clng = sum(s.longitude for s in inp.stops) / len(inp.stops)
    distances = [(haversine_m(clat, clng, s.latitude, s.longitude), s.stop_id) for s in inp.stops]
    ordered = sorted(d for d, _ in distances)
    median = ordered[len(ordered) // 2]
    threshold = max(OUTLIER_MIN_METERS, OUTLIER_MEDIAN_FACTOR * median)
    far = [stop_id for d, stop_id in distances if d > threshold]
    # Half the stops "far" is two clusters, not outliers.
    if not far or len(far) * 2 >= len(inp.stops):
        return []
    return [
        {
            "code": "stop_far_from_rest",
            "stop_ids": far[:50],
            "message": f"{len(far)} stop(s) are more than {round(threshold / 1000)} km from the rest; "
            "check their coordinates.",
        }
    ]


def load_margin_warnings(inp: OptimizeInput) -> list[dict[str, Any]]:
    """The load fits the fleet but not within the fill margin: the engine will struggle or overload."""

    ratio = inp.max_load_ratio if inp.max_load_ratio is not None else DEFAULT_MAX_LOAD_RATIO
    if ratio >= 1:
        return []
    warnings: list[dict[str, Any]] = []
    for used, unit, total, capacity in (
        (
            inp.uses_weight,
            "kg",
            sum(s.weight_kg or 0 for s in inp.stops),
            sum((v.max_weight_kg or 0) * v.count for v in inp.vehicles),
        ),
        (
            inp.uses_volume,
            "m3",
            sum(s.volume_m3 or 0 for s in inp.stops),
            sum((v.max_volume_m3 or 0) * v.count for v in inp.vehicles),
        ),
    ):
        if used and capacity > 0 and capacity * ratio < total <= capacity:
            warnings.append(
                {
                    "code": "load_above_margin",
                    "stop_ids": [],
                    "message": (
                        f"The load ({round(total, 2)} {unit}) needs more than {round(ratio * 100)}% "
                        f"of the fleet's capacity ({round(capacity, 2)} {unit}). Add a vehicle, or set "
                        "max_load_ratio=1 if the user wants vehicles filled completely."
                    ),
                }
            )
    return warnings


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

    warnings.extend(stop_band_warnings(inp.vehicles, len(inp.stops)))
    warnings.extend(load_margin_warnings(inp))

    warnings.extend(outlier_stop_warnings(inp))

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

"""MCP tool arguments → Vepathos Core channel request (see docs/core-channel-contract.md).

Also the one place that derives facts from a parsed request: the confirmation preflight and the
two identities. `request_fingerprint` is the whole request (what Core deduplicates on);
`stops_identity` is the stop set alone, so a variant of an already-charged day is recognisable.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from vepathos_mcp.schemas.inputs import OptimizeInput


def resolve_schedule_date(inp: OptimizeInput, now: datetime | None = None) -> str:
    """The delivery date, defaulting to today in the requested time zone."""

    if inp.schedule is not None and inp.schedule.date is not None:
        return inp.schedule.date
    tz = ZoneInfo(inp.schedule.time_zone if inp.schedule is not None else "UTC")
    moment = now.astimezone(tz) if now is not None else datetime.now(tz)
    return moment.date().isoformat()


def _drop_none(value: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in value.items() if v is not None}


def to_core_request(inp: OptimizeInput, schedule_date: str) -> dict[str, Any]:
    schedule = inp.schedule
    return {
        "depot": {"lat": inp.depot.latitude, "lng": inp.depot.longitude},
        "vehicles": [
            _drop_none(
                {
                    "id": v.vehicle_id,
                    "count": v.count,
                    "max_stops": v.max_stops,
                    "max_weight_kg": v.max_weight_kg,
                    "max_volume_m3": v.max_volume_m3,
                }
            )
            for v in inp.vehicles
        ],
        "stops": [
            _drop_none(
                {
                    "id": s.stop_id,
                    "lat": s.latitude,
                    "lng": s.longitude,
                    "weight_kg": s.weight_kg,
                    "volume_m3": s.volume_m3,
                    "time_window": (
                        {"start": s.time_window.start, "end": s.time_window.end} if s.time_window else None
                    ),
                }
            )
            for s in inp.stops
        ],
        "schedule": _drop_none(
            {
                "date": schedule_date,
                "route_start_time": schedule.route_start_time if schedule else None,
                "time_zone": schedule.time_zone if schedule else "UTC",
                "service_time_minutes": schedule.service_time_minutes if schedule else None,
            }
        ),
    }


def request_fingerprint(core_request: dict[str, Any]) -> str:
    """Deterministic idempotency key: identical optimization requests map to the same job.

    Core scopes keys per account, so the fingerprint needs no account data.
    """

    canonical = json.dumps(core_request, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return "mcp-fp-" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:48]


def stops_identity(core_request: dict[str, Any]) -> str:
    """Identity of the delivery day itself: the depot and the stops, ignoring how it is routed.

    Two requests over the same stops with different vehicles, caps or schedule share this value
    and differ in `request_fingerprint` — which is exactly the case Core charges twice.
    """

    stops = sorted((str(s.get("id")), s.get("lat"), s.get("lng")) for s in core_request.get("stops", []))
    canonical = json.dumps(
        {"depot": core_request.get("depot"), "stops": stops},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return "mcp-si-" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:32]


def constraints_enforced(inp: OptimizeInput) -> list[str]:
    """The constraint names this request actually turns on, in the plan's own vocabulary."""

    active = []
    if inp.uses_weight:
        active.append("weight_capacity")
    if inp.uses_volume:
        active.append("volume_capacity")
    if inp.uses_time_windows:
        active.append("time_windows")
    return active


def _total(values: list[float | None]) -> float | None:
    """Sum of a declared quantity, or None when no stop declares it. 0.0 is a real total."""

    present = [v for v in values if v is not None]
    if not present:
        return None
    return round(sum(present), 6)


def preflight_facts(inp: OptimizeInput, schedule_date: str) -> dict[str, Any]:
    """What the user is about to send, derived from the arguments alone: no Core call, no charge."""

    return {
        "stops": len(inp.stops),
        "charges_stops": len(inp.stops),
        "total_weight_kg": _total([s.weight_kg for s in inp.stops]),
        "total_volume_m3": _total([s.volume_m3 for s in inp.stops]),
        "stops_with_time_window": sum(1 for s in inp.stops if s.time_window is not None),
        "depot": {"latitude": inp.depot.latitude, "longitude": inp.depot.longitude},
        "vehicle_types": len(inp.vehicles),
        "vehicle_units": inp.vehicles_available,
        "constraints_enforced": constraints_enforced(inp),
        "objective": "minimize_distance",
        "schedule_date": schedule_date,
        "route_start_time": inp.schedule.route_start_time if inp.schedule else None,
        "time_zone": inp.schedule.time_zone if inp.schedule else "UTC",
        "service_time_minutes": inp.schedule.service_time_minutes if inp.schedule else None,
    }

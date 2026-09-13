"""MCP tool arguments → Vepathos Core channel request (see docs/core-channel-contract.md)."""

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

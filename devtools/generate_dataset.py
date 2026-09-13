"""Generate reproducible delivery datasets (tool arguments) for smoke tests and benchmarks.

    python -m devtools.generate_dataset --stops 120 --vehicles 5 --out devtools/out/case1.json
    python -m devtools.generate_dataset --stops 2500 --vehicles 30 --weight --out devtools/out/case2.json
    python -m devtools.generate_dataset --stops 300 --vehicles 12 --time-windows --out devtools/out/case3.json

Stops are scattered around a depot (default: Tandil, Argentina, a region the local engine commonly has
graphs for). Coordinates are synthetic and contain no customer data.
"""

from __future__ import annotations

import argparse
import json
import math
import random
from pathlib import Path
from typing import Any


def generate(
    *,
    stops: int,
    vehicles: int,
    depot: tuple[float, float],
    radius_km: float,
    weight: bool,
    volume: bool,
    time_windows: bool,
    seed: int,
) -> dict[str, Any]:
    rng = random.Random(seed)
    lat0, lng0 = depot
    items: list[dict[str, Any]] = []
    for index in range(stops):
        distance = radius_km * math.sqrt(rng.random())
        angle = rng.uniform(0, 2 * math.pi)
        dlat = (distance / 111.32) * math.cos(angle)
        dlng = (distance / (111.32 * math.cos(math.radians(lat0)))) * math.sin(angle)
        stop: dict[str, Any] = {
            "stop_id": f"ORD-{index + 1:06d}",
            "latitude": round(lat0 + dlat, 6),
            "longitude": round(lng0 + dlng, 6),
        }
        if weight:
            stop["weight_kg"] = round(rng.uniform(0.5, 25.0), 2)
        if volume:
            stop["volume_m3"] = round(rng.uniform(0.001, 0.08), 4)
        if time_windows:
            start_hour = rng.choice([9, 10, 11, 13, 14, 15])
            stop["time_window"] = {"start": f"{start_hour:02d}:00", "end": f"{start_hour + 2:02d}:00"}
        items.append(stop)

    per_vehicle_stops = math.ceil(stops / max(1, vehicles) * 1.3)
    vehicle: dict[str, Any] = {"vehicle_id": "van", "count": vehicles, "max_stops": per_vehicle_stops}
    if weight:
        vehicle["max_weight_kg"] = round(sum(s["weight_kg"] for s in items) / vehicles * 1.3, 1)
    if volume:
        vehicle["max_volume_m3"] = round(sum(s["volume_m3"] for s in items) / vehicles * 1.3, 3)

    arguments: dict[str, Any] = {
        "depot": {"latitude": lat0, "longitude": lng0},
        "vehicles": [vehicle],
        "stops": items,
        "schedule": {"time_zone": "America/Argentina/Buenos_Aires", "service_time_minutes": 3},
    }
    if time_windows:
        arguments["schedule"]["route_start_time"] = "08:00"
    return arguments


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--stops", type=int, default=120)
    parser.add_argument("--vehicles", type=int, default=5)
    parser.add_argument("--depot", type=float, nargs=2, default=(-37.3217, -59.1332), metavar=("LAT", "LNG"))
    parser.add_argument("--radius-km", type=float, default=8.0)
    parser.add_argument("--weight", action="store_true")
    parser.add_argument("--volume", action="store_true")
    parser.add_argument("--time-windows", action="store_true")
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()

    data = generate(
        stops=args.stops,
        vehicles=args.vehicles,
        depot=(args.depot[0], args.depot[1]),
        radius_km=args.radius_km,
        weight=args.weight,
        volume=args.volume,
        time_windows=args.time_windows,
        seed=args.seed,
    )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(data, separators=(",", ":")))
    size = args.out.stat().st_size
    print(f"wrote {args.out} ({args.stops} stops, {size:,} bytes, ~{size // 4:,} tokens as arguments)")


if __name__ == "__main__":
    main()

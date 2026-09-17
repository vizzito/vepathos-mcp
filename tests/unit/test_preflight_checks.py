"""Preflight capacity rejections and warnings (T12 / T13 / T18 — bad requests of 16/09)."""

from __future__ import annotations

import pytest

from vepathos_mcp.errors.codes import DomainError, ErrorCode
from vepathos_mcp.schemas.inputs import OptimizeInput
from vepathos_mcp.schemas.preflight_checks import (
    collect_warnings,
    reject_impossible,
    resolve_stop_band,
    stop_band_warnings,
)


def _base(**overrides):
    body = {
        "depot": {"latitude": -34.6, "longitude": -58.4},
        "vehicles": [{"vehicle_id": "van", "count": 1, "max_stops": 2}],
        "stops": [
            {"stop_id": "a", "latitude": -34.61, "longitude": -58.41},
            {"stop_id": "b", "latitude": -34.62, "longitude": -58.42},
            {"stop_id": "c", "latitude": -34.63, "longitude": -58.43},
        ],
    }
    body.update(overrides)
    return OptimizeInput.model_validate(body)


def test_rejects_more_stops_than_fleet_max_stops() -> None:
    with pytest.raises(DomainError) as exc:
        reject_impossible(_base())
    assert exc.value.code is ErrorCode.INVALID_INPUT
    assert exc.value.details["stops"] == 3
    assert exc.value.details["fleet_max_stops"] == 2


def test_rejects_total_weight_over_fleet() -> None:
    inp = _base(
        vehicles=[{"vehicle_id": "van", "count": 1, "max_stops": 10, "max_weight_kg": 100}],
        stops=[
            {"stop_id": "a", "latitude": -34.61, "longitude": -58.41, "weight_kg": 60},
            {"stop_id": "b", "latitude": -34.62, "longitude": -58.42, "weight_kg": 60},
        ],
    )
    with pytest.raises(DomainError) as exc:
        reject_impossible(inp)
    assert "weight" in exc.value.message.lower()


def test_rejects_heavier_than_largest_van() -> None:
    inp = _base(
        vehicles=[{"vehicle_id": "van", "count": 2, "max_stops": 10, "max_weight_kg": 50}],
        stops=[
            {"stop_id": "a", "latitude": -34.61, "longitude": -58.41, "weight_kg": 80},
            {"stop_id": "b", "latitude": -34.62, "longitude": -58.42, "weight_kg": 10},
        ],
    )
    with pytest.raises(DomainError) as exc:
        reject_impossible(inp)
    assert "heavier" in exc.value.message.lower()


def test_rejects_window_ending_before_route_start() -> None:
    inp = _base(
        vehicles=[{"vehicle_id": "van", "count": 1, "max_stops": 10}],
        schedule={"route_start_time": "09:00", "time_zone": "UTC"},
        stops=[
            {
                "stop_id": "a",
                "latitude": -34.61,
                "longitude": -58.41,
                "time_window": {"start": "07:00", "end": "08:00"},
            }
        ],
    )
    with pytest.raises(DomainError) as exc:
        reject_impossible(inp)
    assert "time window" in exc.value.message.lower()


def test_mirrors_the_engine_stop_band() -> None:
    assert resolve_stop_band(None, 100) == (50, "default")
    assert resolve_stop_band(90, 100) == (90, None)
    assert resolve_stop_band(95, 100) == (90, "margin")
    assert resolve_stop_band(6, 6) == (5, "margin")
    assert resolve_stop_band(5, None) == (5, None)


def test_warns_when_the_engine_will_lower_min_stops() -> None:
    inp = _base(
        vehicles=[{"vehicle_id": "van", "count": 2, "min_stops": 95, "max_stops": 100}],
        stops=[{"stop_id": f"s{i}", "latitude": -34.6 + i * 0.001, "longitude": -58.4} for i in range(300)],
    )
    warning = next(w for w in collect_warnings(inp) if w["code"] == "stop_band_margin")
    assert warning["vehicle_id"] == "van"
    assert "will run as 90" in warning["message"]


def test_warns_when_the_fleet_minimums_exceed_the_stops() -> None:
    # 20 vans with max 150 and no min: the engine uses 75 each, 1500 stops for 300.
    inp = _base(
        vehicles=[{"vehicle_id": "van", "count": 20, "max_stops": 150}],
        stops=[{"stop_id": f"s{i}", "latitude": -34.6 + i * 0.0001, "longitude": -58.4} for i in range(300)],
    )
    codes = [w["code"] for w in stop_band_warnings(inp.vehicles, len(inp.stops))]
    assert codes == ["fleet_min_above_stops"]


def test_a_comfortable_band_says_nothing() -> None:
    inp = _base(
        vehicles=[{"vehicle_id": "van", "count": 80, "min_stops": 80, "max_stops": 150}],
        stops=[
            {"stop_id": f"s{i}", "latitude": -34.6 + i * 0.00001, "longitude": -58.4} for i in range(8200)
        ],
    )
    assert stop_band_warnings(inp.vehicles, len(inp.stops)) == []


def test_warns_missing_route_start_and_near_depot() -> None:
    inp = OptimizeInput.model_validate(
        {
            "depot": {"latitude": -34.6, "longitude": -58.4},
            "vehicles": [{"vehicle_id": "van", "count": 1, "max_stops": 50}],
            "stops": [
                {"stop_id": "near", "latitude": -34.6001, "longitude": -58.4001},
                {"stop_id": "dup1", "latitude": -34.7, "longitude": -58.5},
                {"stop_id": "dup2", "latitude": -34.7, "longitude": -58.5},
            ],
        }
    )
    warnings = collect_warnings(inp, fleet_vehicle_ids={"other"})
    codes = {w["code"] for w in warnings}
    assert "missing_route_start_time" in codes
    assert "stop_near_depot" in codes
    assert "duplicate_coordinates" in codes
    assert "unknown_vehicle_id" in codes

from __future__ import annotations

import copy

import pytest

from tests.conftest import sample_arguments
from vepathos_mcp.errors.codes import DomainError, ErrorCode
from vepathos_mcp.schemas.inputs import parse_geocode_input, parse_get_result_input, parse_optimize_input


def expect_error(arguments: dict[str, object], code: ErrorCode) -> DomainError:
    with pytest.raises(DomainError) as info:
        parse_optimize_input(arguments)
    assert info.value.code == code, info.value.to_payload()
    return info.value


def test_valid_minimal_request() -> None:
    inp = parse_optimize_input(sample_arguments(stops=3))
    assert inp.vehicles_available == 3
    assert not inp.uses_weight and not inp.uses_volume and not inp.uses_time_windows


def test_unknown_field_is_rejected_not_ignored() -> None:
    args = sample_arguments()
    args["stops"][0]["priority"] = "high"
    err = expect_error(args, ErrorCode.INVALID_INPUT)
    assert "stops[0].priority" in err.suggestion  # type: ignore[operator]


def test_unknown_top_level_field_is_rejected() -> None:
    expect_error(sample_arguments(return_to_depot=False), ErrorCode.INVALID_INPUT)


def test_no_stops_and_no_vehicles_have_specific_codes() -> None:
    args = sample_arguments()
    args["stops"] = []
    expect_error(args, ErrorCode.NO_STOPS)
    args = sample_arguments()
    args["vehicles"] = []
    expect_error(args, ErrorCode.NO_VEHICLES)


def test_invalid_coordinates() -> None:
    args = sample_arguments()
    args["stops"][2]["latitude"] = 123.0
    err = expect_error(args, ErrorCode.INVALID_COORDINATES)
    assert err.details["issues"][0]["path"] == "stops[2].latitude"


def test_duplicate_ids_rejected() -> None:
    args = sample_arguments()
    args["stops"][1]["stop_id"] = args["stops"][0]["stop_id"]
    expect_error(args, ErrorCode.INVALID_INPUT)
    args = sample_arguments()
    args["vehicles"] = [{"vehicle_id": "Van"}, {"vehicle_id": "van"}]
    expect_error(args, ErrorCode.INVALID_INPUT)


def test_weight_capacity_requires_complete_data() -> None:
    args = sample_arguments(stops=2)
    args["vehicles"] = [{"vehicle_id": "van", "count": 2, "max_weight_kg": 900}]
    err = expect_error(args, ErrorCode.INVALID_INPUT)
    assert "weight_kg" in err.suggestion  # type: ignore[operator]
    for stop in args["stops"]:
        stop["weight_kg"] = 3.5
    assert parse_optimize_input(args).uses_weight


def test_stop_weights_without_capacity_are_informational() -> None:
    args = sample_arguments(stops=2)
    for stop in args["stops"]:
        stop["weight_kg"] = 2.0
    assert not parse_optimize_input(args).uses_weight


def test_time_windows_require_route_start_time() -> None:
    args = sample_arguments(stops=2)
    args["stops"][0]["time_window"] = {"start": "09:00", "end": "12:00"}
    expect_error(args, ErrorCode.INVALID_INPUT)
    args["schedule"]["route_start_time"] = "08:00"
    assert parse_optimize_input(args).uses_time_windows


def test_time_window_order_and_format() -> None:
    args = sample_arguments(stops=1)
    args["schedule"]["route_start_time"] = "08:00"
    bad = copy.deepcopy(args)
    bad["stops"][0]["time_window"] = {"start": "12:00", "end": "09:00"}
    expect_error(bad, ErrorCode.INVALID_INPUT)
    bad["stops"][0]["time_window"] = {"start": "9am", "end": "12:00"}
    expect_error(bad, ErrorCode.INVALID_INPUT)


def test_schedule_validation() -> None:
    args = sample_arguments(stops=1)
    args["schedule"] = {"time_zone": "Mars/Olympus"}
    expect_error(args, ErrorCode.INVALID_INPUT)
    args["schedule"] = {"date": "2026-02-30"}
    expect_error(args, ErrorCode.INVALID_INPUT)


def test_issue_list_is_capped_and_never_echoes_input() -> None:
    args = sample_arguments(stops=500)
    for stop in args["stops"]:
        stop["latitude"] = "not-a-number-" + "x" * 50
    err = expect_error(args, ErrorCode.INVALID_COORDINATES)
    assert len(err.details["issues"]) <= 10
    assert err.details["issue_count"] == 500
    assert "not-a-number" not in str(err.to_payload())


def test_geocode_accepts_inspector_stringified_addresses() -> None:
    inp = parse_geocode_input(
        {
            "addresses": ('[{"stop_id":"A1","address":"Av. Corrientes 1000","city":"CABA","country":"AR"}]'),
            "city": "CABA",
            "country": "AR",
        }
    )
    assert inp.addresses[0].stop_id == "A1"
    assert inp.city == "CABA"


def test_get_result_input() -> None:
    assert parse_get_result_input({"optimization_id": "mcp_" + "a" * 32}).detail == "summary"
    with pytest.raises(DomainError):
        parse_get_result_input({"optimization_id": "../../etc"})
    with pytest.raises(DomainError):
        parse_get_result_input({"optimization_id": "mcp_" + "a" * 32, "detail": "everything"})

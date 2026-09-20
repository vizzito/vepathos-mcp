"""manage_vehicle / manage_depot: master data keeps its shape, and plan settings cannot get in."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from vepathos_mcp.clients.core_models import CoreDepotSaved, CoreVehicleSaved
from vepathos_mcp.tools.catalog_admin import (
    ManageDepotInput,
    ManageVehicleInput,
    depot_body,
    depot_result,
    vehicle_body,
    vehicle_result,
)


def test_create_sends_the_vehicle_and_omits_what_the_user_did_not_say() -> None:
    inp = ManageVehicleInput.model_validate(
        {"action": "create", "vehicle": {"name": " Sprinter ", "max_weight_kg": 1500}}
    )
    assert vehicle_body(inp) == {"name": "Sprinter", "max_weight_kg": 1500.0}


def test_update_sends_only_the_changed_fields() -> None:
    inp = ManageVehicleInput.model_validate(
        {"action": "update", "vehicle_id": "12", "changes": {"max_volume_m3": 12}}
    )
    assert vehicle_body(inp) == {"max_volume_m3": 12.0}


@pytest.mark.parametrize(
    "arguments",
    [
        {"action": "create"},
        {"action": "create", "vehicle": {"name": "x"}, "vehicle_id": "12"},
        {"action": "update", "vehicle_id": "12"},
        {"action": "update", "changes": {"name": "x"}},
        {"action": "update", "vehicle_id": "12", "changes": {}},
        {"action": "update", "vehicle_id": "12", "changes": {"name": "x"}, "vehicle": {"name": "y"}},
        {"action": "delete", "vehicle_id": "12"},
        {"action": "create", "vehicle": {"name": "x", "max_weight_kg": 0}},
    ],
)
def test_the_shape_must_follow_the_action(arguments: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        ManageVehicleInput.model_validate(arguments)


@pytest.mark.parametrize(
    "arguments",
    [
        # "Use 25 vehicles" is a plan setting: no field can carry it into the account.
        {"action": "create", "vehicle": {"name": "Sprinter"}, "count": 25},
        {"action": "create", "vehicle": {"name": "Sprinter", "count": 25}},
        {"action": "update", "vehicle_id": "12", "changes": {"available": False}},
        {"action": "create", "vehicles": [{"name": "a"}, {"name": "b"}]},
    ],
)
def test_plan_settings_and_batches_have_no_way_in(arguments: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        ManageVehicleInput.model_validate(arguments)


def test_a_depot_moves_with_both_coordinates_or_neither() -> None:
    with pytest.raises(ValidationError, match="change together"):
        ManageDepotInput.model_validate({"action": "update", "depot_id": "3", "changes": {"latitude": -34.6}})
    renamed = ManageDepotInput.model_validate(
        {"action": "update", "depot_id": "3", "changes": {"name": "Barracas Sur"}}
    )
    assert depot_body(renamed) == {"name": "Barracas Sur"}
    created = ManageDepotInput.model_validate(
        {"action": "create", "depot": {"name": "Barracas", "latitude": -34.6441, "longitude": -58.3816}}
    )
    assert depot_body(created) == {"name": "Barracas", "latitude": -34.6441, "longitude": -58.3816}


def test_a_depot_takes_coordinates_not_an_address() -> None:
    with pytest.raises(ValidationError):
        ManageDepotInput.model_validate(
            {"action": "create", "depot": {"name": "x", "address": "San Martín 700"}}
        )


def test_the_saved_vehicle_comes_back_ready_for_an_optimization() -> None:
    saved = CoreVehicleSaved.model_validate(
        {
            "vehicle": {"vehicle_id": "veh 12/á", "name": "Sprinter", "max_weight_kg": 1500},
            "outcome": "already_existed",
            "account_url": "https://vepathos.com/dashboard/vehicles",
        }
    )
    out = vehicle_result(saved, "create")
    assert out.vehicle.vehicle_id == "veh-12"
    assert out.outcome == "already_existed" and out.account_url


def test_an_outcome_this_build_does_not_know_falls_back_to_what_the_call_did() -> None:
    saved = CoreDepotSaved.model_validate(
        {"depot": {"depot_id": "3", "name": "B", "latitude": 1, "longitude": 2}, "outcome": "merged"}
    )
    assert depot_result(saved, "update").outcome == "updated"
    assert depot_result(saved, "create").outcome == "created"

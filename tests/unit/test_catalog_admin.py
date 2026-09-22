"""manage_resources: master data keeps its shape, and plan settings cannot get in."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from vepathos_mcp.clients.core_models import CoreDepotSaved, CoreVehicleSaved
from vepathos_mcp.tools.catalog_admin import (
    ManageResourcesInput,
    core_body,
    depot_result,
    vehicle_result,
)


def test_create_sends_the_vehicle_and_omits_what_the_user_did_not_say() -> None:
    inp = ManageResourcesInput.model_validate(
        {"resource": "vehicle", "action": "create", "name": " Sprinter ", "max_weight_kg": 1500}
    )
    assert core_body(inp) == {"name": "Sprinter", "max_weight_kg": 1500.0}


def test_update_sends_only_the_changed_fields() -> None:
    inp = ManageResourcesInput.model_validate(
        {"resource": "vehicle", "action": "update", "resource_id": "12", "max_volume_m3": 12}
    )
    assert core_body(inp) == {"max_volume_m3": 12.0}


@pytest.mark.parametrize(
    "arguments",
    [
        {"resource": "vehicle", "action": "create"},  # no name
        {"resource": "vehicle", "action": "create", "name": "x", "resource_id": "12"},
        {"resource": "vehicle", "action": "update", "resource_id": "12"},  # nothing changes
        {"resource": "vehicle", "action": "update", "name": "x"},  # no resource_id
        {"resource": "vehicle", "action": "delete", "resource_id": "12"},
        {"resource": "vehicle", "action": "create", "name": "x", "max_weight_kg": 0},
        {"resource": "fleet", "action": "create", "name": "x"},  # only vehicle and depot exist
        {"action": "create", "name": "x"},  # resource is required
    ],
)
def test_the_shape_must_follow_the_action(arguments: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        ManageResourcesInput.model_validate(arguments)


@pytest.mark.parametrize(
    "arguments",
    [
        # "Use 25 vehicles" is a plan setting: no field can carry it into the account.
        {"resource": "vehicle", "action": "create", "name": "Sprinter", "count": 25},
        {"resource": "vehicle", "action": "update", "resource_id": "12", "available": False},
        {"resource": "vehicle", "action": "create", "vehicles": [{"name": "a"}, {"name": "b"}]},
    ],
)
def test_plan_settings_and_batches_have_no_way_in(arguments: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        ManageResourcesInput.model_validate(arguments)


@pytest.mark.parametrize(
    "arguments",
    [
        # A field of the other resource is refused, not ignored: dropping it would save a row
        # the user never described.
        {"resource": "vehicle", "action": "create", "name": "x", "latitude": -34.6, "longitude": -58.3},
        {"resource": "depot", "action": "create", "name": "x", "latitude": 1, "longitude": 2,
         "max_weight_kg": 1500},
    ],
)
def test_each_field_belongs_to_one_resource(arguments: dict[str, object]) -> None:
    with pytest.raises(ValidationError, match="belong to a"):
        ManageResourcesInput.model_validate(arguments)


def test_a_depot_moves_with_both_coordinates_or_neither() -> None:
    with pytest.raises(ValidationError, match="travel together"):
        ManageResourcesInput.model_validate(
            {"resource": "depot", "action": "update", "resource_id": "3", "latitude": -34.6}
        )
    renamed = ManageResourcesInput.model_validate(
        {"resource": "depot", "action": "update", "resource_id": "3", "name": "Barracas Sur"}
    )
    assert core_body(renamed) == {"name": "Barracas Sur"}
    created = ManageResourcesInput.model_validate(
        {"resource": "depot", "action": "create", "name": "Barracas", "latitude": -34.6441,
         "longitude": -58.3816}
    )
    assert core_body(created) == {"name": "Barracas", "latitude": -34.6441, "longitude": -58.3816}


def test_a_depot_takes_coordinates_not_an_address() -> None:
    with pytest.raises(ValidationError):
        ManageResourcesInput.model_validate(
            {"resource": "depot", "action": "create", "name": "x", "address": "San Martín 700"}
        )


def test_a_new_depot_cannot_be_saved_without_where_it_is() -> None:
    with pytest.raises(ValidationError, match="latitude and longitude"):
        ManageResourcesInput.model_validate({"resource": "depot", "action": "create", "name": "Barracas"})


def test_the_saved_vehicle_comes_back_ready_for_an_optimization() -> None:
    saved = CoreVehicleSaved.model_validate(
        {
            "vehicle": {"vehicle_id": "veh 12/á", "name": "Sprinter", "max_weight_kg": 1500},
            "outcome": "already_existed",
            "account_url": "https://vepathos.com/dashboard/vehicles",
        }
    )
    out = vehicle_result(saved, "create")
    assert out.resource == "vehicle" and out.depot is None
    assert out.vehicle is not None and out.vehicle.vehicle_id == "veh-12"
    assert out.outcome == "already_existed" and out.account_url


def test_an_outcome_this_build_does_not_know_falls_back_to_what_the_call_did() -> None:
    saved = CoreDepotSaved.model_validate(
        {"depot": {"depot_id": "3", "name": "B", "latitude": 1, "longitude": 2}, "outcome": "merged"}
    )
    assert depot_result(saved, "update").outcome == "updated"
    assert depot_result(saved, "create").outcome == "created"
    assert depot_result(saved, "create").vehicle is None

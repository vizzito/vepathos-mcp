"""list_fleet: shaping the account catalog into something optimize_routes accepts."""

from __future__ import annotations

from vepathos_mcp.clients.core_models import CoreCatalog
from vepathos_mcp.schemas.inputs import OptimizeInput
from vepathos_mcp.tools.fleet import to_catalog, vehicle_id_for_optimize


def catalog(**payload: object) -> CoreCatalog:
    return CoreCatalog.model_validate(payload)


def test_fleet_vehicles_keep_capacities_and_counts() -> None:
    out = to_catalog(
        catalog(
            fleets=[
                {
                    "fleet_id": "7",
                    "name": "Reparto AM",
                    "total_units": 3,
                    "vehicles": [
                        {"vehicle_id": "v1", "name": "Sprinter", "count": 2, "max_weight_kg": 1200},
                        {"vehicle_id": "v2", "name": "KIA", "count": 1, "max_volume_m3": 6},
                    ],
                }
            ]
        )
    )
    assert out.empty is None
    assert out.fleets is not None
    fleet = out.fleets[0]
    assert fleet.name == "Reparto AM" and fleet.total_units == 3
    assert [(v.vehicle_id, v.count, v.max_weight_kg) for v in fleet.vehicles] == [
        ("v1", 2, 1200.0),
        ("v2", 1, None),
    ]


def test_catalog_ids_are_reshaped_to_what_optimize_accepts() -> None:
    taken: set[str] = set()
    # A RouteHub UUID is longer than optimize's 32-character vehicle_id.
    uuid = "3f1c9a0e-5b7d-4c2a-9e8f-6b1d2c3a4b5c"
    assert vehicle_id_for_optimize(uuid, taken) == uuid[:32].strip("-")
    assert vehicle_id_for_optimize("van #1 (grande)", set()) == "van--1--grande"
    assert vehicle_id_for_optimize("", set()) == "vehicle"


def test_ids_that_collide_after_reshaping_stay_distinct() -> None:
    taken: set[str] = set()
    first = vehicle_id_for_optimize("a" * 40, taken)
    second = vehicle_id_for_optimize("a" * 40, taken)
    assert first != second
    assert len(second) <= 32


def test_shaped_vehicles_validate_as_optimize_input() -> None:
    out = to_catalog(
        catalog(
            fleets=[
                {
                    "fleet_id": "f1",
                    "vehicles": [
                        {
                            "vehicle_id": "3f1c9a0e-5b7d-4c2a-9e8f-6b1d2c3a4b5c",
                            "count": 2,
                            "max_weight_kg": 900,
                        }
                    ],
                }
            ]
        )
    )
    assert out.fleets is not None
    vehicles = [
        {"vehicle_id": v.vehicle_id, "count": v.count, "max_weight_kg": v.max_weight_kg}
        for v in out.fleets[0].vehicles
    ]
    parsed = OptimizeInput.model_validate(
        {
            "depot": {"latitude": -37.32, "longitude": -59.13},
            "vehicles": vehicles,
            "stops": [{"stop_id": "S1", "latitude": -37.3, "longitude": -59.1, "weight_kg": 10}],
        }
    )
    assert parsed.vehicles[0].max_weight_kg == 900


def test_an_account_with_no_fleet_says_so() -> None:
    out = to_catalog(catalog())
    assert out.empty is True
    assert out.fleets == [] and out.vehicles is None


def test_saved_depots_come_with_coordinates_and_are_omitted_when_there_are_none() -> None:
    out = to_catalog(
        catalog(depots=[{"depot_id": "3", "name": "Barracas", "latitude": -34.6441, "longitude": -58.3816}])
    )
    assert out.depots is not None and out.depots[0].latitude == -34.6441
    # empty speaks about vehicles: an account with a depot and no vehicle still has no fleet to plan with.
    assert out.empty is True
    assert "depots" not in to_catalog(catalog()).model_dump(exclude_none=True)
    # A Core from before catalog master data sends no depots at all.
    assert catalog(fleets=[], vehicles=[]).depots == []

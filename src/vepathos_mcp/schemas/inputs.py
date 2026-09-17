"""Tool input schemas, designed for language models.

Field names carry their units, every field has a short description, unknown fields are rejected
(so a misspelled or unsupported constraint is reported instead of silently ignored), and the
cross-field rules below make capacity and time-window semantics explicit.
"""

from __future__ import annotations

import json
from datetime import date as _date
from typing import Any, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator
from pydantic_core import PydanticCustomError

from vepathos_mcp.errors.codes import DomainError, ErrorCode

HHMM_PATTERN = r"^([01]\d|2[0-3]):[0-5]\d$"
VEHICLE_ID_PATTERN = r"^[A-Za-z0-9_.-]{1,32}$"
STOP_ID_PATTERN = r"^[^\x00-\x1f\x7f]{1,64}$"
OPTIMIZATION_ID_PATTERN = r"^[A-Za-z0-9_-]{8,64}$"
GEOCODE_ID_PATTERN = r"^[A-Za-z0-9_.-]{8,200}$"
MAX_GEOCODE_STOPS = 500
IDEMPOTENCY_KEY_PATTERN = r"^[A-Za-z0-9_.:-]{8,128}$"

MAX_STOPS = 25_000
MAX_VEHICLE_TYPES = 50
MAX_ISSUES = 10


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Depot(StrictModel):
    """Where every route starts."""

    latitude: float = Field(ge=-90, le=90, description="Depot latitude in decimal degrees (WGS84).")
    longitude: float = Field(ge=-180, le=180, description="Depot longitude in decimal degrees (WGS84).")


class Vehicle(StrictModel):
    """A vehicle type and how many identical units are available."""

    vehicle_id: str = Field(
        pattern=VEHICLE_ID_PATTERN,
        description="Your id for this vehicle or vehicle type (letters, digits, '_', '-', '.'; max 32). "
        "Echoed on every route it drives.",
        examples=["van-small"],
    )
    count: int = Field(1, ge=1, le=500, description="Number of identical vehicles of this type available.")
    min_stops: int | None = Field(
        None,
        ge=0,
        le=10_000,
        description="Minimum stops one vehicle should serve. Omitted: 50% of max_stops. It must stay at "
        "least 10% under max_stops; a higher value is lowered to floor(max_stops x 0.9), with a warning.",
    )
    max_stops: int | None = Field(
        None, ge=1, le=10_000, description="Maximum number of stops one vehicle may serve on its route."
    )
    max_weight_kg: float | None = Field(
        None,
        gt=0,
        le=1_000_000,
        description="Payload capacity per vehicle in kilograms. Setting it on any vehicle enforces weight "
        "capacity (every vehicle and stop then needs weight) unless use_weight=false.",
    )
    max_volume_m3: float | None = Field(
        None,
        gt=0,
        le=10_000,
        description="Cargo volume capacity per vehicle in cubic meters. Setting it on any vehicle enforces "
        "volume capacity (every vehicle and stop then needs volume) unless use_volume=false.",
    )


class TimeWindow(StrictModel):
    """Allowed arrival interval at a stop, in the schedule's local time."""

    start: str = Field(pattern=HHMM_PATTERN, description="Earliest arrival, local time HH:MM (24h).")
    end: str = Field(pattern=HHMM_PATTERN, description="Latest arrival, local time HH:MM (24h).")

    @model_validator(mode="after")
    def _start_before_end(self) -> TimeWindow:
        if self.start >= self.end:
            raise PydanticCustomError("time_window_order", "time_window.start must be earlier than end")
        return self


class Stop(StrictModel):
    """A delivery location. Coordinates are required; addresses are not geocoded."""

    stop_id: str = Field(
        pattern=STOP_ID_PATTERN,
        description="Your id for this stop or order (max 64 characters). Unique. Echoed in results.",
        examples=["ORD-10045"],
    )
    latitude: float = Field(ge=-90, le=90, description="Stop latitude in decimal degrees (WGS84).")
    longitude: float = Field(ge=-180, le=180, description="Stop longitude in decimal degrees (WGS84).")
    weight_kg: float | None = Field(
        None, ge=0, le=100_000, description="Total weight delivered at this stop, kg."
    )
    volume_m3: float | None = Field(
        None, ge=0, le=1_000, description="Total volume delivered at this stop, cubic meters."
    )
    time_window: TimeWindow | None = Field(None, description="Delivery time window for this stop.")


class Schedule(StrictModel):
    """Date, departure time and time zone for the plan."""

    date: str | None = Field(
        None,
        pattern=r"^\d{4}-\d{2}-\d{2}$",
        description="Delivery date YYYY-MM-DD in time_zone. Defaults to today.",
    )
    route_start_time: str | None = Field(
        None,
        pattern=HHMM_PATTERN,
        description="Time routes leave the depot, local HH:MM. Required when any stop has a time_window.",
    )
    time_zone: str = Field(
        "UTC", max_length=64, description="IANA time zone for dates and times, e.g. America/New_York."
    )
    service_time_minutes: float | None = Field(
        None, ge=0, le=240, description="Minutes spent at each stop (unloading, hand-off)."
    )
    max_route_minutes: float | None = Field(
        None,
        ge=30,
        le=24 * 60,
        description="Maximum journey length per route in minutes (travel + service). "
        "Activates the engine time cap when set.",
    )

    @field_validator("date")
    @classmethod
    def _valid_calendar_date(cls, value: str | None) -> str | None:
        if value is not None:
            try:
                _date.fromisoformat(value)
            except ValueError as exc:
                raise PydanticCustomError("invalid_date", "date is not a valid calendar date") from exc
        return value

    @field_validator("time_zone")
    @classmethod
    def _valid_time_zone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise PydanticCustomError(
                "invalid_time_zone", "time_zone must be an IANA time zone name"
            ) from exc
        return value


class OptimizeInput(StrictModel):
    """Arguments of optimize_delivery_routes."""

    depot: Depot
    vehicles: list[Vehicle] = Field(
        min_length=1, max_length=MAX_VEHICLE_TYPES, description="Available fleet (1-50 vehicle types)."
    )
    stops: list[Stop] = Field(
        min_length=1, max_length=MAX_STOPS, description="Stops to assign and sequence (at least 1)."
    )
    schedule: Schedule | None = Field(None, description="Optional date, departure time and time zone.")
    use_weight: bool | None = Field(
        None,
        description="Optimize by weight capacity. Omit to follow the data; "
        "false keeps weights for reference.",
    )
    use_volume: bool | None = Field(
        None,
        description="Optimize by volume capacity. Omit to follow the data; "
        "false keeps volumes for reference.",
    )
    use_time_windows: bool | None = Field(
        None,
        description="Respect stop time windows. Omit to follow the data; false keeps windows for reference.",
    )
    idempotency_key: str | None = Field(
        None,
        pattern=IDEMPOTENCY_KEY_PATTERN,
        description="Optional key to deduplicate retries. By default identical arguments are deduplicated.",
    )
    confirmed: bool = Field(
        False,
        description="Set true only after the user has seen what will be sent and how many stops it "
        "charges, and agreed. While false the call optimizes nothing and charges nothing: it returns "
        "a preflight of this exact request for you to show them.",
    )

    @model_validator(mode="after")
    def _cross_field_rules(self) -> OptimizeInput:
        seen_vehicles: set[str] = set()
        for vehicle in self.vehicles:
            key = vehicle.vehicle_id.lower()
            if key in seen_vehicles:
                raise PydanticCustomError(
                    "duplicate_vehicle_id",
                    "vehicle_id values must be unique (case-insensitive); duplicate: {vehicle_id}",
                    {"vehicle_id": vehicle.vehicle_id},
                )
            seen_vehicles.add(key)

        seen_stops: set[str] = set()
        for stop in self.stops:
            if stop.stop_id in seen_stops:
                raise PydanticCustomError(
                    "duplicate_stop_id",
                    "stop_id values must be unique; duplicate: {stop_id}",
                    {"stop_id": stop.stop_id},
                )
            seen_stops.add(stop.stop_id)

        for flag, on, vehicle_field in (
            ("use_weight", self.uses_weight, "max_weight_kg"),
            ("use_volume", self.uses_volume, "max_volume_m3"),
        ):
            if on and not any(getattr(v, vehicle_field) is not None for v in self.vehicles):
                raise PydanticCustomError(
                    "constraint_without_capacity",
                    "{flag} is true but no vehicle has {vehicle_field}",
                    {"flag": flag, "vehicle_field": vehicle_field},
                )
        if self.uses_weight:
            _require_complete("max_weight_kg", "weight_kg", self.vehicles, self.stops)
        if self.uses_volume:
            _require_complete("max_volume_m3", "volume_m3", self.vehicles, self.stops)

        if self.uses_time_windows and (self.schedule is None or self.schedule.route_start_time is None):
            raise PydanticCustomError(
                "route_start_time_required",
                "schedule.route_start_time is required when any stop has a time_window",
            )
        for vehicle in self.vehicles:
            if (
                vehicle.min_stops is not None
                and vehicle.max_stops is not None
                and vehicle.min_stops > vehicle.max_stops
            ):
                raise PydanticCustomError(
                    "min_stops_gt_max",
                    "min_stops must be less than or equal to max_stops for vehicle_id {vehicle_id}",
                    {"vehicle_id": vehicle.vehicle_id},
                )
        return self

    # The flag decides what the engine applies; an omitted flag follows the data. Data kept with a flag
    # off still reaches Core for reference but does not shape the routes (Core `constraints`).
    @property
    def uses_weight(self) -> bool:
        if self.use_weight is not None:
            return self.use_weight
        return any(v.max_weight_kg is not None for v in self.vehicles)

    @property
    def uses_volume(self) -> bool:
        if self.use_volume is not None:
            return self.use_volume
        return any(v.max_volume_m3 is not None for v in self.vehicles)

    @property
    def uses_time_windows(self) -> bool:
        has_windows = any(s.time_window is not None for s in self.stops)
        if self.use_time_windows is not None:
            return self.use_time_windows and has_windows
        return has_windows

    @property
    def vehicles_available(self) -> int:
        return sum(v.count for v in self.vehicles)


def _require_complete(
    vehicle_field: str, stop_field: str, vehicles: list[Vehicle], stops: list[Stop]
) -> None:
    if not any(getattr(v, vehicle_field) is not None for v in vehicles):
        # No vehicle declares this capacity: stop values (if any) are informational only.
        return
    missing_vehicles = sum(1 for v in vehicles if getattr(v, vehicle_field) is None)
    missing_stops = sum(1 for s in stops if getattr(s, stop_field) is None)
    if missing_vehicles or missing_stops:
        raise PydanticCustomError(
            "capacity_incomplete",
            "{vehicle_field} is set on some vehicles, so the constraint is enforced: every vehicle needs "
            "{vehicle_field} ({missing_vehicles} missing) and every stop needs {stop_field} "
            "({missing_stops} missing)",
            {
                "vehicle_field": vehicle_field,
                "stop_field": stop_field,
                "missing_vehicles": missing_vehicles,
                "missing_stops": missing_stops,
            },
        )


class GetResultInput(StrictModel):
    """Arguments of get_optimization_result."""

    optimization_id: str = Field(
        pattern=OPTIMIZATION_ID_PATTERN,
        description="The optimization_id returned by optimize_delivery_routes.",
    )
    detail: Literal["summary", "stops", "unassigned"] = Field(
        "summary",
        description="summary: totals and per-route metrics. stops: ordered stops with arrival times. "
        "unassigned: ids of stops that could not be routed.",
    )
    route_id: str | None = Field(
        None, max_length=64, description="Only for detail=stops: return the stop sequence of this route only."
    )
    offset: int = Field(0, ge=0, le=1_000_000, description="Pagination offset (routes or stops).")
    limit: int | None = Field(
        None,
        ge=1,
        le=1000,
        description="Page size. Defaults: 25 routes for summary, 200 items for stops and unassigned.",
    )


def _format_path(loc: tuple[int | str, ...]) -> str:
    path = ""
    for part in loc:
        if isinstance(part, int):
            path += f"[{part}]"
        else:
            path += f".{part}" if path else str(part)
    return path or "(root)"


def validation_error_to_domain(exc: ValidationError) -> DomainError:
    """Summarize a Pydantic error without echoing the (possibly huge) input."""

    errors = exc.errors(include_input=False, include_url=False, include_context=False)
    for err in errors:
        loc = tuple(err.get("loc", ()))
        if loc == ("stops",) and err.get("type") in {"too_short", "missing"}:
            return DomainError(
                ErrorCode.NO_STOPS,
                "The request has no stops.",
                suggestion="Provide at least one stop with stop_id, latitude and longitude.",
            )
        if loc == ("vehicles",) and err.get("type") in {"too_short", "missing"}:
            return DomainError(
                ErrorCode.NO_VEHICLES,
                "The request has no vehicles.",
                suggestion="Provide at least one vehicle with vehicle_id and count.",
            )

    issues = [
        {"path": _format_path(tuple(err.get("loc", ()))), "message": str(err.get("msg", "invalid value"))}
        for err in errors[:MAX_ISSUES]
    ]
    coordinate_errors = [
        i for i in issues if i["path"].endswith(".latitude") or i["path"].endswith(".longitude")
    ]
    if coordinate_errors and len(coordinate_errors) == len(issues):
        return DomainError(
            ErrorCode.INVALID_COORDINATES,
            f"{len(errors)} coordinate value(s) are invalid.",
            suggestion="Use decimal degrees: latitude between -90 and 90, longitude between -180 and 180.",
            details={"issues": issues, "issue_count": len(errors)},
        )
    lines = "; ".join(f"{i['path']}: {i['message']}" for i in issues[:5])
    return DomainError(
        ErrorCode.INVALID_INPUT,
        f"The request has {len(errors)} invalid field(s).",
        suggestion=f"Fix these fields and try again — {lines}",
        details={"issues": issues, "issue_count": len(errors)},
    )


def parse_optimize_input(arguments: dict[str, Any] | None) -> OptimizeInput:
    try:
        return OptimizeInput.model_validate(
            coerce_json_fields(arguments or {}, "depot", "vehicles", "stops", "schedule")
        )
    except ValidationError as exc:
        raise validation_error_to_domain(exc) from None


class AddressStop(StrictModel):
    """A delivery location given as a street address. Vepathos Smart Import geocodes it."""

    stop_id: str = Field(
        pattern=STOP_ID_PATTERN,
        description="Your id for this stop (max 64 characters). Unique. Echoed in results.",
        examples=["ORD-10045"],
    )
    address: str = Field(
        min_length=1,
        max_length=300,
        description="Street address to geocode. Not a coordinate.",
    )
    city: str | None = Field(None, max_length=120, description="City, when known.")
    region: str | None = Field(None, max_length=120, description="State, province or region.")
    postcode: str | None = Field(None, max_length=32)
    country: str | None = Field(None, max_length=64, description="Country name or ISO code, when known.")


class GeocodeInput(StrictModel):
    """Arguments of geocode_addresses."""

    addresses: list[AddressStop] = Field(
        min_length=1,
        max_length=MAX_GEOCODE_STOPS,
        description="Stops to geocode (1-500). Uses the account Smart Import quota, not route stops.",
    )
    depot: Depot | None = Field(
        None,
        description="Depot coordinates. Helps pick the map region. Required if city is omitted.",
    )
    city: str | None = Field(
        None, max_length=120, description="City used to pick the map region when depot is omitted."
    )
    country: str | None = Field(None, max_length=64)
    timezone: str | None = Field(
        None,
        max_length=64,
        description="IANA time zone, e.g. America/Argentina/Buenos_Aires.",
    )

    @model_validator(mode="after")
    def _region_hint(self) -> GeocodeInput:
        if self.depot is None and not (self.city and self.city.strip()):
            raise PydanticCustomError(
                "geocode_region",
                "Provide depot coordinates or city so Smart Import knows which region to search",
            )
        ids = [s.stop_id for s in self.addresses]
        if len(ids) != len(set(ids)):
            raise PydanticCustomError("geocode_ids", "addresses[].stop_id values must be unique")
        return self


class GetGeocodeInput(StrictModel):
    """Arguments of get_geocode_result."""

    geocode_id: str = Field(
        pattern=GEOCODE_ID_PATTERN,
        description="The geocode_id returned by geocode_addresses.",
    )


class ListFleetInput(StrictModel):
    """Arguments of list_fleet. It takes none: the catalog belongs to the connected account."""


class GetAccountInput(StrictModel):
    """Arguments of get_account. It takes none: the account comes from the connection itself."""


def coerce_json_fields(arguments: dict[str, Any], *keys: str) -> dict[str, Any]:
    """Inspector `--tool-arg key=[...]` leaves arrays/objects as JSON strings."""

    out = dict(arguments)
    for key in keys:
        raw = out.get(key)
        if not isinstance(raw, str):
            continue
        text = raw.strip()
        if not text or text[0] not in "[{":
            continue
        try:
            out[key] = json.loads(text)
        except json.JSONDecodeError:
            continue
    return out


def parse_geocode_input(arguments: dict[str, Any] | None) -> GeocodeInput:
    try:
        return GeocodeInput.model_validate(coerce_json_fields(arguments or {}, "addresses", "depot"))
    except ValidationError as exc:
        raise validation_error_to_domain(exc) from None


def parse_get_geocode_input(arguments: dict[str, Any] | None) -> GetGeocodeInput:
    try:
        return GetGeocodeInput.model_validate(arguments or {})
    except ValidationError as exc:
        raise validation_error_to_domain(exc) from None


def parse_get_account_input(arguments: dict[str, Any] | None) -> GetAccountInput:
    try:
        return GetAccountInput.model_validate(arguments or {})
    except ValidationError as exc:
        raise validation_error_to_domain(exc) from None


def parse_list_fleet_input(arguments: dict[str, Any] | None) -> ListFleetInput:
    try:
        return ListFleetInput.model_validate(arguments or {})
    except ValidationError as exc:
        raise validation_error_to_domain(exc) from None


def parse_get_result_input(arguments: dict[str, Any] | None) -> GetResultInput:
    try:
        return GetResultInput.model_validate(arguments or {})
    except ValidationError as exc:
        raise validation_error_to_domain(exc) from None

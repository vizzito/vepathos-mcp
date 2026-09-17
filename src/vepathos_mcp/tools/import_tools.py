"""Import / dataset tools: file-by-reference → Smart Import → optimize by id."""

from __future__ import annotations

import base64
import logging
from typing import Annotated, Any

from mcp.server.mcpserver.context import Context
from mcp_types import CallToolResult
from pydantic import Field, ValidationError

from vepathos_mcp.clients.core_models import CoreDataset
from vepathos_mcp.clients.public_fetch import PublicFetchError, fetch_public_https
from vepathos_mcp.errors.codes import DomainError, ErrorCode
from vepathos_mcp.schemas.inputs import (
    StrictModel,
    coerce_json_fields,
    validation_error_to_domain,
)
from vepathos_mcp.schemas.outputs import OptimizeResult, OutputModel, Preflight
from vepathos_mcp.telemetry.logging import log_event
from vepathos_mcp.tools.rendering import success_result
from vepathos_mcp.tools.results import POLL_AFTER_SECONDS
from vepathos_mcp.tools.runtime import RequestIdentity, ToolDeps, instrumented

IMPORT_FILE_TOOL = "import_delivery_file"
IMPORT_TEXT_TOOL = "import_delivery_text"
GET_IMPORT_TOOL = "get_import_result"
UPDATE_MAPPING_TOOL = "update_import_mapping"
LIST_DATASETS_TOOL = "list_datasets"
OPTIMIZE_DATASET_TOOL = "optimize_dataset"

MAX_IMPORT_BYTES = 8 * 1024 * 1024


class FileParam(StrictModel):
    """ChatGPT Apps SDK file reference (_meta openai/fileParams)."""

    download_url: str | None = Field(None, max_length=2048)
    file_id: str | None = Field(None, max_length=200)
    mime_type: str | None = Field(None, max_length=120)
    file_name: str | None = Field(None, max_length=200)


class ImportFileInput(StrictModel):
    file: FileParam | None = Field(
        None,
        description="ChatGPT attachment. Prefer this over pasting stops. "
        "Absent/expired downloads return INVALID_INPUT asking to re-attach.",
    )
    url: str | None = Field(
        None,
        max_length=2048,
        description="Public https URL of the file (Claude/console). SSRF-blocked.",
    )
    filename: str | None = Field(None, max_length=200)
    timezone: str | None = Field(None, max_length=64)
    depot_country: str | None = Field(None, max_length=64)


class ImportTextInput(StrictModel):
    text: str = Field(min_length=1, max_length=2_000_000, description="Pasted delivery list.")
    filename: str | None = Field(None, max_length=200)
    timezone: str | None = Field(None, max_length=64)


class GetImportInput(StrictModel):
    import_id: str = Field(min_length=8, max_length=200)


class UpdateMappingInput(StrictModel):
    import_id: str = Field(min_length=8, max_length=200)
    mapping: dict[str, str | None] = Field(
        description="Source column → vepathos field (or null to ignore).",
    )


class ListDatasetsInput(StrictModel):
    limit: int = Field(20, ge=1, le=100)


class DatasetVehicle(StrictModel):
    vehicle_id: str = Field(pattern=r"^[A-Za-z0-9_.-]{1,32}$")
    count: int = Field(1, ge=1, le=500)
    min_stops: int | None = Field(
        None,
        ge=0,
        le=10_000,
        description="Minimum stops per vehicle. Default 1 when omitted. "
        "Keep min <= floor(max x 0.8) unless the user asks for a tight band.",
    )
    max_stops: int | None = Field(None, ge=1, le=10_000)
    max_weight_kg: float | None = Field(None, gt=0)
    max_volume_m3: float | None = Field(None, gt=0)


class DatasetDepot(StrictModel):
    latitude: float | None = Field(None, ge=-90, le=90)
    longitude: float | None = Field(None, ge=-180, le=180)
    address: str | None = Field(
        None,
        max_length=300,
        description="Depot as an address; the server geocodes it and shows what it found.",
    )


class OptimizeDatasetInput(StrictModel):
    dataset_id: str = Field(min_length=8, max_length=64)
    depot: DatasetDepot
    vehicles: list[DatasetVehicle] = Field(min_length=1, max_length=50)
    exclude_stop_ids: list[str] | None = Field(None, max_length=25_000)
    use_weight: bool = False
    use_volume: bool = False
    use_time_windows: bool = False
    route_start_time: str | None = Field(None, pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    time_zone: str = Field("UTC", max_length=64)
    service_time_minutes: float | None = Field(None, ge=0, le=240)
    max_route_minutes: float | None = Field(
        None,
        ge=30,
        le=24 * 60,
        description="Maximum journey length per route in minutes. Activates the engine time cap.",
    )
    date: str | None = Field(None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    confirmed: bool = False
    idempotency_key: str | None = Field(None, pattern=r"^[A-Za-z0-9_.:-]{8,128}$")


class ImportCreated(OutputModel):
    import_id: str | None = None
    dataset_id: str | None = None
    status: str | None = None
    poll_after_seconds: int | None = None
    expires_at: str | None = None
    error: dict[str, Any] | None = None


class ImportResultView(OutputModel):
    import_id: str | None = None
    dataset_id: str | None = None
    status: str | None = None
    summary: dict[str, Any] | None = None
    expires_at: str | None = None
    next_optimize_charged: bool | None = Field(
        None,
        description="True: the next optimize_dataset on this dataset charges its stops. False: it is a "
        "free replan.",
    )
    free_replans_remaining: int | None = Field(
        None, description="Free variants left after the dataset's charged run (0 until that run)."
    )
    poll_after_seconds: int | None = None
    progress: dict[str, Any] | None = None
    error: dict[str, Any] | None = None


class DatasetListItem(OutputModel):
    dataset_id: str
    filename: str | None = None
    source: str | None = None
    status: str | None = None
    stops: int | None = Field(None, description="Stops stored in the dataset.")
    expires_at: str | None = Field(None, description="After this the dataset must be imported again.")
    next_optimize_charged: bool | None = Field(
        None,
        description="True: the next optimize_dataset charges the dataset's stops. False: it is a free "
        "replan. Says nothing about whether the dataset was optimized before; last_run does.",
    )
    free_replans_remaining: int | None = Field(
        None, description="Free variants left after the dataset's charged run (0 until that run)."
    )
    last_run: dict[str, Any] | None = Field(
        None,
        description="The latest optimization of this dataset and what it ran with (optimization_id, "
        "status, depot, vehicles, schedule). Null when never optimized. Offer to repeat or vary it "
        "instead of asking for the depot, fleet and departure again, and confirm them with the user.",
    )


class DatasetListResult(OutputModel):
    datasets: list[DatasetListItem] = Field(default_factory=list)
    error: dict[str, Any] | None = None


def _file_from_meta(arguments: dict[str, Any]) -> FileParam | None:
    """Recover ChatGPT fileParams when the model omitted the file argument."""

    meta = arguments.get("_meta")
    if not isinstance(meta, dict):
        return None
    params = meta.get("openai/fileParams") or meta.get("openai/file_params")
    if isinstance(params, dict):
        try:
            return FileParam.model_validate(params)
        except ValidationError:
            return None
    if isinstance(params, list) and params:
        first = params[0]
        if isinstance(first, dict):
            try:
                return FileParam.model_validate(first)
            except ValidationError:
                return None
    return None


_DOWNLOAD_FAILURES: dict[str, tuple[str, str]] = {
    "invalid_url": (
        "The attached file's download URL is not a public https URL.",
        "Attach the file again and call import_delivery_file.",
    ),
    "blocked_host": (
        "The attached file's download URL points to a private or internal address.",
        "Attach the file again from the chat; only public https downloads are accepted.",
    ),
    "redirect": (
        "The file download redirected, and redirects are not followed.",
        "Attach the file again and call import_delivery_file.",
    ),
    "too_large": (
        f"File is too large (max {MAX_IMPORT_BYTES} bytes).",
        "Attach a smaller file or split the dataset.",
    ),
    "network": (
        "Could not download the attached file.",
        "Attach the file again and call import_delivery_file.",
    ),
}


async def _download_bytes(deps: ToolDeps, url: str, *, max_bytes: int) -> bytes:
    try:
        data, _content_type = await fetch_public_https(url, max_bytes=max_bytes)
    except PublicFetchError as exc:
        if exc.reason == "http_status":
            raise DomainError(
                ErrorCode.INVALID_INPUT,
                f"The file download failed (HTTP {exc.status_code}).",
                suggestion="The download URL may have expired. Attach the file again.",
            ) from None
        message, suggestion = _DOWNLOAD_FAILURES[exc.reason]
        if exc.reason == "too_large":
            message = f"File is too large (max {max_bytes} bytes)."
        raise DomainError(
            ErrorCode.INVALID_INPUT, message, suggestion=suggestion, details={"reason": exc.reason}
        ) from None
    if not data:
        raise DomainError(
            ErrorCode.INVALID_INPUT,
            "The attached file is empty.",
            suggestion="Attach the delivery file again.",
        )
    return data


def make_import_file_tool(deps: ToolDeps) -> Any:
    async def import_delivery_file(ctx: Context) -> Annotated[CallToolResult, ImportCreated]:
        async def handle(identity: RequestIdentity, arguments: dict[str, Any]) -> CallToolResult:
            try:
                inp = ImportFileInput.model_validate(coerce_json_fields(arguments or {}, "file"))
            except ValidationError as exc:
                raise validation_error_to_domain(exc) from None

            file_ref = inp.file or _file_from_meta(arguments)
            body: dict[str, Any] = {}
            if inp.timezone:
                body["timezone"] = inp.timezone
            if inp.depot_country:
                body["depot_country"] = inp.depot_country

            if file_ref and file_ref.download_url:
                data = await _download_bytes(deps, file_ref.download_url, max_bytes=MAX_IMPORT_BYTES)
                body["content_base64"] = base64.b64encode(data).decode("ascii")
                body["filename"] = (inp.filename or file_ref.file_name or "attachment.bin")[:200]
                if file_ref.mime_type:
                    body["mime_type"] = file_ref.mime_type
            elif inp.url:
                body["url"] = inp.url
                if inp.filename:
                    body["filename"] = inp.filename
            else:
                raise DomainError(
                    ErrorCode.INVALID_INPUT,
                    "No delivery file arrived with this call.",
                    suggestion="Attach the file again (ChatGPT fileParams) or pass url. "
                    "About 1 in 10 fileParams calls arrive without the parameter.",
                )

            deps.rate_limiter.check(identity.subject, "calls")
            created = await deps.core.create_import(identity.call, body)
            return success_result(
                ImportCreated(
                    import_id=created.get("import_id"),
                    dataset_id=created.get("dataset_id"),
                    status=created.get("status"),
                    poll_after_seconds=POLL_AFTER_SECONDS,
                    expires_at=created.get("expires_at"),
                )
            )

        return await instrumented(IMPORT_FILE_TOOL, ctx, deps, handle)

    return import_delivery_file


def make_import_text_tool(deps: ToolDeps) -> Any:
    async def import_delivery_text(ctx: Context) -> Annotated[CallToolResult, ImportCreated]:
        async def handle(identity: RequestIdentity, arguments: dict[str, Any]) -> CallToolResult:
            try:
                inp = ImportTextInput.model_validate(arguments or {})
            except ValidationError as exc:
                raise validation_error_to_domain(exc) from None
            deps.rate_limiter.check(identity.subject, "calls")
            body: dict[str, Any] = {"text": inp.text}
            if inp.filename:
                body["filename"] = inp.filename
            if inp.timezone:
                body["timezone"] = inp.timezone
            created = await deps.core.create_import(identity.call, body)
            return success_result(
                ImportCreated(
                    import_id=created.get("import_id"),
                    dataset_id=created.get("dataset_id"),
                    status=created.get("status"),
                    poll_after_seconds=POLL_AFTER_SECONDS,
                    expires_at=created.get("expires_at"),
                )
            )

        return await instrumented(IMPORT_TEXT_TOOL, ctx, deps, handle)

    return import_delivery_text


def make_get_import_tool(deps: ToolDeps) -> Any:
    async def get_import_result(ctx: Context) -> Annotated[CallToolResult, ImportResultView]:
        async def handle(identity: RequestIdentity, arguments: dict[str, Any]) -> CallToolResult:
            try:
                inp = GetImportInput.model_validate(arguments or {})
            except ValidationError as exc:
                raise validation_error_to_domain(exc) from None
            deps.rate_limiter.check(identity.subject, "calls")
            result = await deps.core.get_import(identity.call, inp.import_id)
            status = result.get("status")
            terminal = status in {"completed", "failed", "expired", "needs_mapping"}
            return success_result(
                ImportResultView(
                    import_id=result.get("import_id") or inp.import_id,
                    dataset_id=result.get("dataset_id"),
                    status=status,
                    summary=result.get("summary"),
                    expires_at=result.get("expires_at"),
                    next_optimize_charged=result.get("next_optimize_charged"),
                    free_replans_remaining=result.get("free_replans_remaining"),
                    poll_after_seconds=None if terminal else POLL_AFTER_SECONDS,
                    progress=result.get("progress"),
                )
            )

        return await instrumented(GET_IMPORT_TOOL, ctx, deps, handle)

    return get_import_result


def make_update_mapping_tool(deps: ToolDeps) -> Any:
    async def update_import_mapping(ctx: Context) -> Annotated[CallToolResult, ImportResultView]:
        async def handle(identity: RequestIdentity, arguments: dict[str, Any]) -> CallToolResult:
            try:
                inp = UpdateMappingInput.model_validate(coerce_json_fields(arguments or {}, "mapping"))
            except ValidationError as exc:
                raise validation_error_to_domain(exc) from None
            deps.rate_limiter.check(identity.subject, "calls")
            result = await deps.core.update_import_mapping(
                identity.call, inp.import_id, {"mapping": inp.mapping}
            )
            return success_result(
                ImportResultView(
                    import_id=result.get("import_id") or inp.import_id,
                    dataset_id=result.get("dataset_id"),
                    status=result.get("status"),
                    summary=result.get("summary"),
                    expires_at=result.get("expires_at"),
                    poll_after_seconds=POLL_AFTER_SECONDS,
                )
            )

        return await instrumented(UPDATE_MAPPING_TOOL, ctx, deps, handle)

    return update_import_mapping


def make_list_datasets_tool(deps: ToolDeps) -> Any:
    async def list_datasets(ctx: Context) -> Annotated[CallToolResult, DatasetListResult]:
        async def handle(identity: RequestIdentity, arguments: dict[str, Any]) -> CallToolResult:
            try:
                inp = ListDatasetsInput.model_validate(arguments or {})
            except ValidationError as exc:
                raise validation_error_to_domain(exc) from None
            deps.rate_limiter.check(identity.subject, "calls")
            result = await deps.core.list_datasets(identity.call, limit=inp.limit)
            items = [
                DatasetListItem.model_validate(row)
                for row in (result.get("datasets") or [])
                if isinstance(row, dict)
            ]
            return success_result(DatasetListResult(datasets=items))

        return await instrumented(LIST_DATASETS_TOOL, ctx, deps, handle)

    return list_datasets


def dataset_constraints(inp: OptimizeDatasetInput, dataset: CoreDataset) -> list[str]:
    """What Core will enforce. Windows follow the stored stops, not use_time_windows: Core enforces
    every window a dataset carries."""

    constraints = []
    if inp.use_weight and any(v.max_weight_kg is not None for v in inp.vehicles):
        constraints.append("weight_capacity")
    if inp.use_volume and any(v.max_volume_m3 is not None for v in inp.vehicles):
        constraints.append("volume_capacity")
    if dataset.with_time_window:
        constraints.append("time_windows")
    return constraints


def dataset_warnings(inp: OptimizeDatasetInput, dataset: CoreDataset) -> list[dict[str, Any]]:
    """Problems Core would reject the run for, or the user should hear about before paying for it."""

    warnings: list[dict[str, Any]] = []
    constraints = dataset_constraints(inp, dataset)
    for used, key, counted, code in (
        ("weight_capacity", "weight_kg", dataset.with_weight, "stops_without_weight"),
        ("volume_capacity", "volume_m3", dataset.with_volume, "stops_without_volume"),
    ):
        missing = dataset.stops - counted if counted is not None else 0
        if used in constraints and missing > 0:
            warnings.append(
                {
                    "code": code,
                    "count": missing,
                    "message": f"{missing} stop(s) have no {key}; Core rejects this capacity "
                    "unless every stop has one.",
                }
            )
    if dataset.with_time_window and not inp.route_start_time:
        warnings.append(
            {
                "code": "route_start_time_required",
                "count": dataset.with_time_window,
                "message": f"{dataset.with_time_window} stop(s) carry a time window, so route_start_time "
                "is required.",
            }
        )
    if dataset.needs_confirmation:
        warnings.append(
            {
                "code": "import_needs_confirmation",
                "message": "The import flagged rows or columns to review; confirm them with the user first.",
            }
        )
    return warnings


async def dataset_preflight(
    deps: ToolDeps,
    identity: RequestIdentity,
    inp: OptimizeDatasetInput,
    depot_lat: float,
    depot_lng: float,
) -> Preflight:
    """Stops and charge of a dataset run, from Core's counts. A free replan charges 0 stops but still
    has to fit the plan's limits."""

    from vepathos_mcp.tools.optimize import CONFIRM_WITH, plan_check

    dataset = await deps.core.get_dataset(identity.call, inp.dataset_id)
    excluded = len(set(inp.exclude_stop_ids or []))
    stops = max(0, dataset.stops - excluded)
    # A Core that omits next_optimize_charged is treated as billing the run.
    free_replan = dataset.next_optimize_charged is False
    charges = 0 if free_replan else stops
    constraints = dataset_constraints(inp, dataset)
    if free_replan:
        billing_note = " This is a free replan of the dataset: no stops are charged, plan limits still apply."
    elif dataset.next_optimize_charged:
        billing_note = (
            " This run charges its stops; after a dataset's charged run, the plan allows some free variants."
        )
    else:
        billing_note = ""
    # Totals cover the whole dataset, so they are only exact when nothing is excluded.
    whole = not excluded
    return Preflight(
        stops=stops,
        charges_stops=charges,
        total_weight_kg=dataset.total_weight_kg if whole and "weight_capacity" in constraints else None,
        total_volume_m3=dataset.total_volume_m3 if whole and "volume_capacity" in constraints else None,
        stops_with_time_window=dataset.with_time_window or 0,
        depot={"latitude": depot_lat, "longitude": depot_lng},
        vehicle_types=len(inp.vehicles),
        vehicle_units=sum(v.count for v in inp.vehicles),
        constraints_enforced=constraints,
        objective="minimize_distance",
        schedule_date=inp.date,
        route_start_time=inp.route_start_time,
        time_zone=inp.time_zone,
        service_time_minutes=inp.service_time_minutes,
        stops_identity=f"dataset:{inp.dataset_id}",
        plan=await plan_check(deps, identity, stops=stops, charges_stops=charges, constraints=constraints),
        warnings=dataset_warnings(inp, dataset) or None,
        confirm_with=CONFIRM_WITH + billing_note,
    )


def make_optimize_dataset_tool(deps: ToolDeps) -> Any:
    from vepathos_mcp.schemas.mapping import request_fingerprint
    from vepathos_mcp.schemas.outputs import FullTrialApplied
    from vepathos_mcp.telemetry import metrics
    from vepathos_mcp.tools.results import failure_error, fetch_result_view
    from vepathos_mcp.tools.runtime import wait_for_terminal

    async def optimize_dataset(ctx: Context) -> Annotated[CallToolResult, OptimizeResult]:
        async def handle(identity: RequestIdentity, arguments: dict[str, Any]) -> CallToolResult:
            try:
                inp = OptimizeDatasetInput.model_validate(
                    coerce_json_fields(arguments or {}, "depot", "vehicles", "exclude_stop_ids")
                )
            except ValidationError as exc:
                raise validation_error_to_domain(exc) from None

            if inp.depot.latitude is None or inp.depot.longitude is None:
                if not inp.depot.address:
                    raise DomainError(
                        ErrorCode.INVALID_INPUT,
                        "Depot needs latitude/longitude or an address.",
                        suggestion="Pass depot coordinates, or an address to geocode.",
                    )
                # Geocode a single depot address through the existing tool path.
                address = inp.depot.address
                geo_body = {
                    "stops": [{"id": "depot", "address": address}],
                    "city": address.split(",")[-1].strip() if "," in address else address,
                }
                geo_job = await deps.core.create_geocode(
                    identity.call, geo_body, f"depot-{inp.dataset_id[:24]}"
                )
                geo = await deps.core.get_geocode(identity.call, geo_job.job_id)
                # Brief poll
                if not geo.is_terminal and deps.settings.optimize_inline_wait_seconds > 0:
                    import anyio

                    for _ in range(5):
                        await anyio.sleep(1.5)
                        geo = await deps.core.get_geocode(identity.call, geo_job.job_id)
                        if geo.is_terminal:
                            break
                pin = geo.stops[0] if geo.stops else None
                if pin is None or pin.lat is None or pin.lng is None:
                    raise DomainError(
                        ErrorCode.INVALID_INPUT,
                        "Could not geocode the depot address.",
                        suggestion="Pass depot latitude and longitude explicitly.",
                        details={"matched_address": getattr(pin, "matched_address", None)},
                    )
                depot_lat, depot_lng = pin.lat, pin.lng
                log_event(
                    "depot_geocoded",
                    logging.INFO,
                    matched_address=getattr(pin, "matched_address", None),
                    lat=depot_lat,
                    lng=depot_lng,
                )
            else:
                depot_lat, depot_lng = inp.depot.latitude, inp.depot.longitude

            body: dict[str, Any] = {
                "dataset_id": inp.dataset_id,
                "depot": {"lat": depot_lat, "lng": depot_lng},
                "vehicles": [
                    {
                        k: v
                        for k, v in {
                            "id": veh.vehicle_id,
                            "count": veh.count,
                            "min_stops": veh.min_stops,
                            "max_stops": veh.max_stops,
                            "max_weight_kg": veh.max_weight_kg if inp.use_weight else None,
                            "max_volume_m3": veh.max_volume_m3 if inp.use_volume else None,
                        }.items()
                        if v is not None
                    }
                    for veh in inp.vehicles
                ],
                "schedule": {
                    k: v
                    for k, v in {
                        "date": inp.date,
                        "route_start_time": inp.route_start_time,
                        "time_zone": inp.time_zone,
                        "service_time_minutes": inp.service_time_minutes,
                        "max_route_minutes": inp.max_route_minutes,
                    }.items()
                    if v is not None
                },
            }
            if inp.exclude_stop_ids:
                body["exclude_stop_ids"] = inp.exclude_stop_ids

            # The preflight reads the dataset's counts from Core instead of expanding its stops here.
            if deps.settings.confirm_before_optimize and not inp.confirmed:
                deps.rate_limiter.check(identity.subject, "calls")
                return success_result(
                    OptimizeResult(
                        preflight=await dataset_preflight(deps, identity, inp, depot_lat, depot_lng)
                    )
                )

            deps.rate_limiter.check(identity.subject, "optimize")
            idempotency_key = inp.idempotency_key or request_fingerprint(
                {**body, "dataset_id": inp.dataset_id}
            )
            created = await deps.core.create_job(identity.call, body, idempotency_key)
            if not created.idempotent_replay:
                metrics.OPTIMIZATION_STOPS.observe(created.submitted_stops)

            output = OptimizeResult(
                quota_charged=created.billing.quota_charged if created.billing else None,
                free_replans_remaining=(created.billing.free_replans_remaining if created.billing else None),
                optimization_id=created.job_id,
                status=created.status,
                idempotent_replay=created.idempotent_replay,
                submitted_stops=created.submitted_stops,
                vehicles_available=created.vehicles_available,
                schedule_date=created.schedule_date,
                expires_at=created.expires_at,
                stops_remaining_this_period=(
                    created.billing.stops_remaining_this_period if created.billing else None
                ),
                full_trial_applied=(
                    FullTrialApplied(
                        max_stops=created.full_trial_applied.max_stops,
                        features=created.full_trial_applied.features,
                        quota_charged=bool(created.billing and created.billing.quota_charged),
                    )
                    if created.full_trial_applied
                    else None
                ),
            )
            if deps.settings.optimize_inline_wait_seconds <= 0 and created.status != "completed":
                output.poll_after_seconds = POLL_AFTER_SECONDS
                return success_result(output)

            status = await deps.core.get_job(identity.call, created.job_id)
            if not status.is_terminal and deps.settings.optimize_inline_wait_seconds > 0:
                status = await wait_for_terminal(
                    ctx, deps, identity, status, deps.settings.optimize_inline_wait_seconds
                )
            if status.status == "failed":
                raise failure_error(status)
            if status.status == "completed":
                output.status = "completed"
                output.result = await fetch_result_view(deps, identity, created.job_id, detail="summary")
            else:
                output.status = status.status
                output.poll_after_seconds = POLL_AFTER_SECONDS
            return success_result(output)

        return await instrumented(OPTIMIZE_DATASET_TOOL, ctx, deps, handle)

    return optimize_dataset

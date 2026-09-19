"""Import tools: file-by-reference → Smart Import → the stops load into a plan → optimize by id."""

from __future__ import annotations

import base64
import logging
from typing import Annotated, Any

from mcp.server.mcpserver.context import Context
from mcp_types import CallToolResult
from pydantic import Field, ValidationError, model_validator

from vepathos_mcp.clients.core_models import CoreDataset, CorePlan
from vepathos_mcp.clients.google_drive import normalize_public_download_url
from vepathos_mcp.clients.public_fetch import PublicFetchError, fetch_public_https
from vepathos_mcp.errors.codes import DomainError, ErrorCode
from vepathos_mcp.schemas.inputs import (
    DEPOT_NAME_MAX,
    PLAN_ID_PATTERN,
    StrictModel,
    coerce_json_fields,
    validation_error_to_domain,
)
from vepathos_mcp.schemas.outputs import (
    ACCOUNT_URL_DESCRIPTION,
    DepotResolved,
    FreeRetryFields,
    OptimizeResult,
    OutputModel,
    PlanPlacementFields,
    Preflight,
)
from vepathos_mcp.schemas.preflight_checks import stop_band_warnings
from vepathos_mcp.telemetry.logging import log_event
from vepathos_mcp.tools.plans import free_retry_fields, placement_fields
from vepathos_mcp.tools.rendering import success_result
from vepathos_mcp.tools.results import POLL_AFTER_SECONDS
from vepathos_mcp.tools.runtime import RequestIdentity, ToolDeps, instrumented

IMPORT_FILE_TOOL = "import_delivery_file"
IMPORT_TEXT_TOOL = "import_delivery_text"
GET_IMPORT_TOOL = "get_import_result"
UPDATE_MAPPING_TOOL = "update_import_mapping"
LIST_DATASETS_TOOL = "list_datasets"
OPTIMIZE_PLAN_TOOL = "optimize_plan"

MAX_IMPORT_BYTES = 8 * 1024 * 1024

# Smart Import rejects anything outside this set with 415 (mapped upstream to a user error).
_SI_SUFFIXES = frozenset({".csv", ".tsv", ".txt", ".xlsx", ".xlsm", ".xls", ".json"})
_MIME_TO_SI_SUFFIX: dict[str, str] = {
    "text/csv": ".csv",
    "text/tab-separated-values": ".tsv",
    "text/plain": ".txt",
    "application/json": ".json",
    "application/vnd.ms-excel": ".xls",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ".xlsx",
    "application/vnd.ms-excel.sheet.macroenabled.12": ".xlsm",
}


def smart_import_filename(name: str | None, mime_type: str | None = None) -> str:
    """Filename SI will accept. ChatGPT often omits an extension; never fall back to `.bin`."""

    raw = (name or "").strip().replace("\\", "/").rsplit("/", 1)[-1] or "delivery"
    raw = raw[:200]
    lower = raw.lower()
    for suffix in _SI_SUFFIXES:
        if lower.endswith(suffix):
            return raw
    mime = (mime_type or "").split(";", 1)[0].strip().lower()
    ext = _MIME_TO_SI_SUFFIX.get(mime) or ".txt"
    stem = raw.rsplit(".", 1)[0].strip() or "delivery"
    return f"{stem}{ext}"[:200]


IMPORT_PLAN_ID_DESCRIPTION = (
    "Load the stops into this existing plan, replacing its stops and keeping its depot, fleet and "
    "settings. Omit to create a new plan named after the file."
)


class FileParam(StrictModel):
    """ChatGPT Apps SDK file reference (_meta openai/fileParams)."""

    download_url: str | None = Field(None, max_length=2048)
    file_id: str | None = Field(None, max_length=200)
    mime_type: str | None = Field(None, max_length=120)
    file_name: str | None = Field(None, max_length=200)


class ImportFileInput(StrictModel):
    file: FileParam | None = Field(
        None,
        description="The attachment, filled in by hosts that hand files to tools (ChatGPT file params); "
        "never build it by hand. Other hosts pass url.",
    )
    url: str | None = Field(
        None,
        max_length=2048,
        description="Public https URL of the file, or a Google Drive, Sheets or Docs share link. For "
        "hosts without attachment parameters (Claude, Gemini, Cursor, a console).",
    )
    filename: str | None = Field(None, max_length=200)
    timezone: str | None = Field(None, max_length=64)
    depot_country: str | None = Field(None, max_length=64)
    plan_id: str | None = Field(None, pattern=PLAN_ID_PATTERN, description=IMPORT_PLAN_ID_DESCRIPTION)


class ImportTextInput(StrictModel):
    text: str = Field(min_length=1, max_length=2_000_000, description="Pasted delivery list.")
    filename: str | None = Field(None, max_length=200)
    timezone: str | None = Field(None, max_length=64)
    plan_id: str | None = Field(None, pattern=PLAN_ID_PATTERN, description=IMPORT_PLAN_ID_DESCRIPTION)


class GetImportInput(StrictModel):
    import_id: str = Field(min_length=8, max_length=200)


class ColumnMapping(StrictModel):
    """A column whose values need more than a rename: another unit, or a date or number format."""

    field: str = Field(min_length=1, max_length=64, description="The vepathos field, e.g. weight_kg.")
    unit: str | None = Field(
        None,
        max_length=32,
        description="Unit the column is in: kg, g, lb, oz, t; cm, mm, m, in, ft; m3, cm3, l, ml, ft3; "
        "min, s, h; cents, units. It is converted to the field's unit.",
    )
    format: str | None = Field(
        None,
        max_length=64,
        description="Date pattern (dd/mm/yyyy hh:mm, MM/DD/YYYY hh:mm AM/PM, iso) or number format "
        "(decimal comma, decimal point).",
    )


class UpdateMappingInput(StrictModel):
    import_id: str = Field(min_length=8, max_length=200)
    mapping: dict[str, str | ColumnMapping | None] = Field(
        description="Source column → vepathos field, null to ignore the column, or {field, unit, format} "
        "when its values are in another unit or format.",
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
        description="Minimum stops per vehicle. Omitted: 50% of max_stops. It must stay at least 10% under "
        "max_stops; a higher value is lowered to floor(max_stops x 0.9), with a warning.",
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
        description="Depot as an address; the server geocodes it and shows what it found "
        "(depot_resolved). An uncertain match is refused until the user confirms its coordinates.",
    )
    city: str | None = Field(None, max_length=120, description="City of the depot address, when known.")
    country: str | None = Field(
        None, max_length=64, description="Country name or ISO code of the depot address, when known."
    )


class OptimizePlanInput(StrictModel):
    plan_id: str | None = Field(
        None, pattern=PLAN_ID_PATTERN, description="The plan to optimize (list_plans, get_import_result)."
    )
    dataset_id: str | None = Field(
        None, min_length=8, max_length=64, description="An import's stops, instead of plan_id."
    )
    depot: DatasetDepot
    vehicles: list[DatasetVehicle] = Field(min_length=1, max_length=50)
    exclude_stop_ids: list[str] | None = Field(
        None,
        max_length=25_000,
        description="Stops to leave out of this run only; the plan keeps them.",
    )
    use_weight: bool | None = Field(
        None,
        description="Optimize by weight. Omit to follow the vehicles' max_weight_kg; "
        "false keeps weights for reference.",
    )
    use_volume: bool | None = Field(
        None,
        description="Optimize by volume. Omit to follow the vehicles' max_volume_m3; "
        "false keeps volumes for reference.",
    )
    max_load_ratio: float | None = Field(
        None,
        ge=0.5,
        le=1,
        description="Highest share of each vehicle's weight/volume capacity to fill. "
        "Default 0.95 (5% margin); 1 only when the user asks to fill vehicles completely.",
    )
    use_time_windows: bool | None = Field(
        None,
        description="Respect the stops' time windows. Omit to follow the data; "
        "false keeps them for reference.",
    )
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
    depot_name: str | None = Field(
        None, min_length=1, max_length=DEPOT_NAME_MAX, description="Depot name shown in the plan."
    )

    @model_validator(mode="after")
    def _one_source(self) -> OptimizePlanInput:
        if (self.plan_id is None) == (self.dataset_id is None):
            raise ValueError("send exactly one of plan_id or dataset_id")
        return self


class ImportCreated(OutputModel):
    import_id: str | None = None
    dataset_id: str | None = None
    plan_id: str | None = Field(
        None, description="The plan the stops load into; null until they load (get_import_result)."
    )
    account_url: str | None = Field(None, description=ACCOUNT_URL_DESCRIPTION)
    status: str | None = None
    poll_after_seconds: int | None = None
    expires_at: str | None = None
    error: dict[str, Any] | None = None


class ImportResultView(FreeRetryFields, PlanPlacementFields):
    import_id: str | None = None
    dataset_id: str | None = None
    status: str | None = None
    summary: dict[str, Any] | None = None
    expires_at: str | None = Field(None, description="When dataset_id expires. The plan stays.")
    poll_after_seconds: int | None = None
    progress: dict[str, Any] | None = None
    error: dict[str, Any] | None = None


class DatasetListItem(FreeRetryFields, PlanPlacementFields):
    dataset_id: str
    filename: str | None = None
    source: str | None = None
    status: str | None = None
    stops: int | None = Field(None, description="Stops stored in the dataset.")
    needs_confirmation: bool | None = None
    expires_at: str | None = Field(
        None, description="After this dataset_id is gone; its plan (plan_id) stays."
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
        "Use a public Google Drive share link, a direct https file URL, or attach the file.",
    ),
    "not_a_file": (
        "The link returned a web page instead of the file, so it is not shared publicly.",
        "Ask the user to share it as 'Anyone with the link' (Google Drive: Share, General access), or to "
        "attach the file.",
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
                suggestion="The link may have expired or is not public. Share it as 'Anyone with the "
                "link', or attach the file again.",
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


def import_created(created: dict[str, Any]) -> ImportCreated:
    return ImportCreated(
        import_id=created.get("import_id"),
        dataset_id=created.get("dataset_id"),
        plan_id=created.get("plan_id"),
        account_url=created.get("account_url"),
        status=created.get("status"),
        poll_after_seconds=POLL_AFTER_SECONDS,
        expires_at=created.get("expires_at"),
    )


def import_view(result: dict[str, Any], import_id: str, *, poll: bool) -> ImportResultView:
    """An import as the model sees it: the plan its stops loaded into and what optimizing it costs."""

    return ImportResultView(
        import_id=result.get("import_id") or import_id,
        dataset_id=result.get("dataset_id"),
        status=result.get("status"),
        summary=result.get("summary"),
        expires_at=result.get("expires_at"),
        poll_after_seconds=POLL_AFTER_SECONDS if poll else None,
        progress=result.get("progress"),
        **placement_fields(result),
        **free_retry_fields(result),
    )


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
            if inp.plan_id:
                body["plan_id"] = inp.plan_id

            if file_ref and file_ref.download_url:
                data = await _download_bytes(deps, file_ref.download_url, max_bytes=MAX_IMPORT_BYTES)
                body["content_base64"] = base64.b64encode(data).decode("ascii")
                body["filename"] = smart_import_filename(
                    inp.filename or file_ref.file_name, file_ref.mime_type
                )
                if file_ref.mime_type:
                    body["mime_type"] = file_ref.mime_type
            elif inp.url:
                # Core applies the same Drive rewrite; normalize here so logs/errors match.
                body["url"] = normalize_public_download_url(inp.url)
                body["filename"] = smart_import_filename(inp.filename)
            else:
                raise DomainError(
                    ErrorCode.INVALID_INPUT,
                    "No delivery file arrived with this call.",
                    suggestion="If the host passes attachments to tools (ChatGPT), attach the file again: "
                    "about 1 in 10 calls arrive without it. Otherwise pass url (a public https or Google "
                    "Drive link), or send the file's text to import_delivery_text.",
                )

            deps.rate_limiter.check(identity.subject, "calls")
            created = await deps.core.create_import(identity.call, body)
            return success_result(import_created(created))

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
            if inp.plan_id:
                body["plan_id"] = inp.plan_id
            created = await deps.core.create_import(identity.call, body)
            return success_result(import_created(created))

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
            terminal = result.get("status") in {"completed", "failed", "expired", "needs_mapping"}
            return success_result(import_view(result, inp.import_id, poll=not terminal))

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
            mapping = {
                column: value.model_dump(exclude_none=True) if isinstance(value, ColumnMapping) else value
                for column, value in inp.mapping.items()
            }
            result = await deps.core.update_import_mapping(identity.call, inp.import_id, {"mapping": mapping})
            return success_result(import_view(result, inp.import_id, poll=True))

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
                DatasetListItem.model_validate({**row, **placement_fields(row)})
                for row in (result.get("datasets") or [])
                if isinstance(row, dict)
            ]
            return success_result(DatasetListResult(datasets=items))

        return await instrumented(LIST_DATASETS_TOOL, ctx, deps, handle)

    return list_datasets


StoredStops = CorePlan | CoreDataset


def dataset_constraints(inp: OptimizePlanInput, dataset: StoredStops) -> list[str]:
    """What the engine will apply. A flag decides; an omitted flag follows the data (a vehicle capacity,
    the windows the stored stops carry), the same rule Core applies to `constraints`."""

    def applied(flag: bool | None, declared: bool) -> bool:
        return declared if flag is None else flag

    constraints = []
    if applied(inp.use_weight, any(v.max_weight_kg is not None for v in inp.vehicles)):
        constraints.append("weight_capacity")
    if applied(inp.use_volume, any(v.max_volume_m3 is not None for v in inp.vehicles)):
        constraints.append("volume_capacity")
    if dataset.with_time_window and applied(inp.use_time_windows, True):
        constraints.append("time_windows")
    return constraints


def dataset_warnings(inp: OptimizePlanInput, dataset: StoredStops) -> list[dict[str, Any]]:
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
    if "time_windows" in constraints and not inp.route_start_time:
        warnings.append(
            {
                "code": "route_start_time_required",
                "count": dataset.with_time_window,
                "message": f"{dataset.with_time_window} stop(s) carry a time window, so route_start_time "
                "is required.",
            }
        )
    warnings.extend(stop_band_warnings(inp.vehicles, dataset.stops - len(set(inp.exclude_stop_ids or []))))
    if isinstance(dataset, CoreDataset) and dataset.needs_confirmation:
        warnings.append(
            {
                "code": "import_needs_confirmation",
                "message": "The import flagged rows or columns to review; confirm them with the user first.",
            }
        )
    if isinstance(dataset, CorePlan):
        warnings.extend(plan_warnings(dataset))
    return warnings


def plan_warnings(plan: CorePlan) -> list[dict[str, Any]]:
    """A plan Core would refuse to run as it stands."""

    warnings: list[dict[str, Any]] = []
    if plan.stops == 0:
        warnings.append({"code": "plan_has_no_stops", "message": "The plan has no stops to optimize."})
    if plan.stops_not_optimizable:
        warnings.append(
            {
                "code": "plan_stops_not_optimizable",
                "count": plan.stops_not_optimizable,
                "message": f"{plan.stops_not_optimizable} stop(s) have ids an agent cannot send, so Core "
                "rejects the run. The user can fix them in the plan (account_url).",
            }
        )
    if plan.optimizing:
        warnings.append(
            {
                "code": "plan_optimizing",
                "message": "The plan is optimizing now; a new run is refused until that one finishes.",
            }
        )
    return warnings


CHARGED_BECAUSE_NOTES = {
    "stops_changed": " Another try does not apply: stops were added or moved since the charged run "
    "(excluding those stops can open another try).",
    "allowance_used": " The plan's other tries for this window were already used.",
    "window_closed": " The plan's 24 h window for another try has closed.",
}


def billing_note(state: StoredStops) -> str:
    """What the preflight adds about the charge: another try, or why stops are charged."""

    if state.next_optimize_charged is False:
        return (
            " This is another try of the plan (otro intento): no monthly stops are charged; "
            "plan limits still apply. Tell the user the window end if free_retry_window_ends_at is set."
        )
    if state.next_optimize_charged is None:
        return ""
    note = " This run charges its stops to the monthly allowance."
    note += CHARGED_BECAUSE_NOTES.get(state.charged_because or "", "")
    if state.free_retries_allowed:
        note += (
            " Once it completes, rerunning this plan within 24 h with the same stops or fewer is "
            "another try (no stops charged)."
        )
    return note


async def dataset_preflight(
    deps: ToolDeps,
    identity: RequestIdentity,
    inp: OptimizePlanInput,
    depot_lat: float,
    depot_lng: float,
) -> Preflight:
    """Stops and charge of a plan or dataset run, from Core's counts. A free retry charges 0 stops but
    still has to fit the plan's limits."""

    from vepathos_mcp.tools.optimize import CONFIRM_WITH, plan_check

    source: StoredStops
    plan_id: str | None
    if inp.plan_id is not None:
        source = await deps.core.get_plan(identity.call, inp.plan_id)
        stops_identity, plan_id = f"plan:{inp.plan_id}", inp.plan_id
    else:
        source = await deps.core.get_dataset(identity.call, inp.dataset_id or "")
        stops_identity, plan_id = f"dataset:{inp.dataset_id}", source.plan_id
    excluded = len(set(inp.exclude_stop_ids or []))
    stops = max(0, source.stops - excluded)
    # A Core that omits next_optimize_charged is treated as billing the run.
    free_retry = source.next_optimize_charged is False
    charges = 0 if free_retry else stops
    constraints = dataset_constraints(inp, source)
    # Totals cover every stored stop, so they are only exact when nothing is excluded.
    whole = not excluded
    return Preflight(
        stops=stops,
        charges_stops=charges,
        total_weight_kg=source.total_weight_kg if whole and "weight_capacity" in constraints else None,
        total_volume_m3=source.total_volume_m3 if whole and "volume_capacity" in constraints else None,
        stops_with_time_window=source.with_time_window or 0,
        depot={"latitude": depot_lat, "longitude": depot_lng},
        vehicle_types=len(inp.vehicles),
        vehicle_units=sum(v.count for v in inp.vehicles),
        constraints_enforced=constraints,
        objective="minimize_distance",
        schedule_date=inp.date,
        route_start_time=inp.route_start_time,
        time_zone=inp.time_zone,
        service_time_minutes=inp.service_time_minutes,
        stops_identity=stops_identity,
        plan_id=plan_id,
        charged_because=None if free_retry else source.charged_because,
        free_retry_window_ends_at=source.free_retry_window_ends_at,
        plan=await plan_check(deps, identity, stops=stops, charges_stops=charges, constraints=constraints),
        warnings=dataset_warnings(inp, source) or None,
        confirm_with=CONFIRM_WITH + billing_note(source),
    )


def depot_region(depot: DatasetDepot) -> dict[str, str]:
    """The region hint Smart Import searches in. Without an explicit city, "street, city, country" reads
    its city from the segment before the last one: the last one is usually the country."""

    address = depot.address or ""
    parts = [part.strip() for part in address.split(",") if part.strip()]
    city = (depot.city or "").strip()
    country = (depot.country or "").strip()
    if not city:
        if len(parts) >= 3:
            city, country = parts[-2], country or parts[-1]
        else:
            city = parts[-1] if parts else address
    return {"city": city, **({"country": country} if country else {})}


async def resolve_depot_address(
    deps: ToolDeps, identity: RequestIdentity, inp: OptimizePlanInput
) -> DepotResolved:
    """Geocodes the depot through the geocode path. Every route starts there, so a match the geocoder is
    not sure of is handed back for the user to confirm instead of being optimized (and charged) on."""

    import anyio

    address = inp.depot.address
    if not address:
        raise DomainError(
            ErrorCode.INVALID_INPUT,
            "Depot needs latitude/longitude or an address.",
            suggestion="Pass depot coordinates, or an address to geocode.",
        )
    geo_body = {"stops": [{"id": "depot", "address": address}], **depot_region(inp.depot)}
    geo_job = await deps.core.create_geocode(
        identity.call, geo_body, f"depot-{(inp.plan_id or inp.dataset_id or '')[:24]}"
    )
    geo = await deps.core.get_geocode(identity.call, geo_job.job_id)
    if not geo.is_terminal and deps.settings.optimize_inline_wait_seconds > 0:
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
    resolved = DepotResolved(matched_address=pin.matched_address, latitude=pin.lat, longitude=pin.lng)
    # No address or coordinates in logs (docs/privacy-mcp.md): the band says how good the match was.
    log_event("depot_geocoded", logging.INFO, band=pin.band)
    if pin.band is not None and pin.band != "valid":
        raise DomainError(
            ErrorCode.INVALID_INPUT,
            "The depot address matched with low confidence, so nothing was optimized or charged.",
            suggestion="Tell the user the matched address. After their yes, call again with depot "
            "latitude and longitude from depot_resolved; otherwise ask for a more precise address.",
            details={"depot_resolved": resolved.model_dump()},
        )
    return resolved


def make_optimize_plan_tool(deps: ToolDeps) -> Any:
    from vepathos_mcp.schemas.mapping import request_fingerprint
    from vepathos_mcp.tools.optimize import submit_optimization

    async def optimize_plan(ctx: Context) -> Annotated[CallToolResult, OptimizeResult]:
        async def handle(identity: RequestIdentity, arguments: dict[str, Any]) -> CallToolResult:
            try:
                inp = OptimizePlanInput.model_validate(
                    coerce_json_fields(arguments or {}, "depot", "vehicles", "exclude_stop_ids")
                )
            except ValidationError as exc:
                raise validation_error_to_domain(exc) from None
            depot_resolved: DepotResolved | None = None
            if inp.depot.latitude is None or inp.depot.longitude is None:
                depot_resolved = await resolve_depot_address(deps, identity, inp)
                depot_lat, depot_lng = depot_resolved.latitude, depot_resolved.longitude
            else:
                depot_lat, depot_lng = inp.depot.latitude, inp.depot.longitude

            # Exactly one stop source; Core expands it and writes the run into that plan.
            source = {"plan_id": inp.plan_id} if inp.plan_id else {"dataset_id": inp.dataset_id}
            body: dict[str, Any] = {
                **source,
                **({"depot_name": inp.depot_name} if inp.depot_name else {}),
                "depot": {"lat": depot_lat, "lng": depot_lng},
                "vehicles": [
                    {
                        k: v
                        for k, v in {
                            "id": veh.vehicle_id,
                            "count": veh.count,
                            "min_stops": veh.min_stops,
                            "max_stops": veh.max_stops,
                            "max_weight_kg": veh.max_weight_kg,
                            "max_volume_m3": veh.max_volume_m3,
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
            flags = {
                k: v
                for k, v in {
                    "weight": inp.use_weight,
                    "volume": inp.use_volume,
                    "time_windows": inp.use_time_windows,
                    "max_load_ratio": inp.max_load_ratio,
                }.items()
                if v is not None
            }
            if flags:
                body["constraints"] = flags

            # The preflight reads the plan's or dataset's counts from Core instead of expanding stops here.
            if deps.settings.confirm_before_optimize and not inp.confirmed:
                deps.rate_limiter.check(identity.subject, "calls")
                preflight = await dataset_preflight(deps, identity, inp, depot_lat, depot_lng)
                preflight.depot_resolved = depot_resolved
                return success_result(OptimizeResult(preflight=preflight))

            deps.rate_limiter.check(identity.subject, "optimize")
            fingerprinted = dict(body)
            if inp.plan_id is not None and inp.idempotency_key is None:
                # Core keys idempotency on the request as sent, before it expands the plan's stops, so
                # identical arguments after the plan changed would replay the old run. The plan's revision
                # keeps a retry of this call deduplicated and a run of the changed plan a new run.
                plan = await deps.core.get_plan(identity.call, inp.plan_id)
                fingerprinted["plan_revision"] = (
                    plan.revision if plan.revision is not None else plan.updated_at
                )
            idempotency_key = inp.idempotency_key or request_fingerprint(fingerprinted)
            return await submit_optimization(
                ctx,
                deps,
                identity,
                body,
                idempotency_key,
                vehicles_available=sum(v.count for v in inp.vehicles),
                schedule_date=inp.date,
                depot_resolved=depot_resolved,
            )

        return await instrumented(OPTIMIZE_PLAN_TOOL, ctx, deps, handle)

    return optimize_plan

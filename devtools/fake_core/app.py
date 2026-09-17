"""A TEST DOUBLE of the Vepathos Core MCP channel (docs/core-channel-contract.md).

It is NOT the Vepathos optimizer and implements no routing algorithm: routes are a naive split of
the stops in input order, only so that the MCP adapter can be exercised end to end (CI, demos, MCP
Inspector) without the real stack. Plan rules are a tiny, configurable imitation.

Run:  python -m devtools.fake_core            (listens on 127.0.0.1:3900)
"""

from __future__ import annotations

import hashlib
import hmac
import json
import math
import os
import re
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

API_KEY = re.compile(r"^vpt_(?:mcp_)?[0-9a-f]{24}:vpt_sk_(test|live)_[0-9a-f]{48}$")


@dataclass
class FakePlan:
    name: str = "free"
    max_stops_per_request: int | None = 150
    monthly_stops: int | None = 2000
    features: frozenset[str] = frozenset()
    max_concurrent: int = 1
    # Free variants after a dataset's billed run (Core: PlanLimits.freeReplansPerRun).
    free_replans: int = 5


@dataclass
class FakeJob:
    job_id: str
    account: str
    body_hash: str
    body: dict[str, Any]
    created_at: float
    run_seconds: float
    fail: bool = False


@dataclass
class FakeCoreState:
    service_key: str = "dev-service-key"
    plan: FakePlan = field(default_factory=FakePlan)
    jobs: dict[str, FakeJob] = field(default_factory=dict)
    used_stops: dict[str, int] = field(default_factory=dict)
    trial_used: set[str] = field(default_factory=set)
    geocode_jobs: dict[str, dict[str, Any]] = field(default_factory=dict)
    imports: dict[str, dict[str, Any]] = field(default_factory=dict)
    datasets: dict[str, dict[str, Any]] = field(default_factory=dict)
    run_seconds: float = 3.0
    company_name: str | None = None
    fleets: list[dict[str, Any]] = field(default_factory=list)
    vehicles: list[dict[str, Any]] = field(default_factory=list)
    clock: Any = time.time


def _error(status: int, code: str, message: str, details: dict[str, Any] | None = None) -> JSONResponse:
    body: dict[str, Any] = {"error": {"code": code, "message": message, "retryable": status >= 500}}
    if details:
        body["error"]["details"] = details
    return JSONResponse(body, status_code=status)


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, UTC).isoformat().replace("+00:00", "Z")


def _replan_state(dataset: dict[str, Any], plan_free_replans: int) -> dict[str, Any]:
    """Same rule as Core: nothing is free until the dataset's first billed optimization."""

    free = max(0, plan_free_replans - dataset["replan_count"]) if dataset["billed"] else 0
    return {"next_optimize_charged": free == 0, "free_replans_remaining": free}


def _run_record(body: dict[str, Any], *, dataset_id: str | None, excluded: int | None) -> dict[str, Any]:
    """Same shape as Core's run record: everything the job used except the stops."""

    schedule = body.get("schedule") or {}
    run: dict[str, Any] = {
        "depot": body.get("depot"),
        "vehicles": body.get("vehicles", []),
        "schedule": {"time_zone": "UTC", **schedule},
        "objective": body.get("objective", "minimize_distance"),
        "submitted_stops": len(body.get("stops") or []),
    }
    if dataset_id is not None:
        run["dataset_id"] = dataset_id
        run["excluded_stops"] = excluded or 0
    return run


def create_fake_core(state: FakeCoreState | None = None) -> Starlette:
    state = state or FakeCoreState()

    def authenticate(request: Request) -> str | JSONResponse:
        if not hmac.compare_digest(request.headers.get("x-vepathos-mcp-service-key", ""), state.service_key):
            return _error(401, "SERVICE_UNAUTHORIZED", "Service key missing or invalid.")
        auth = request.headers.get("authorization", "")
        if not auth.startswith("Bearer "):
            return _error(401, "AUTHENTICATION_REQUIRED", "User credential required.")
        credential = auth[7:]
        if API_KEY.match(credential):
            return "acct_" + hashlib.sha256(credential.split(":")[0].encode()).hexdigest()[:12]
        parts = credential.split(".")
        if len(parts) == 3:  # fake: trust the JWT's sub without verifying (test double only)
            try:
                padded = parts[1] + "=" * (-len(parts[1]) % 4)
                import base64

                sub = json.loads(base64.urlsafe_b64decode(padded)).get("sub")
            except (ValueError, json.JSONDecodeError):
                sub = None
            if sub:
                return f"acct_{sub}"
        return _error(401, "INVALID_CREDENTIALS", "Credential invalid.")

    async def health(request: Request) -> JSONResponse:
        if request.headers.get("x-vepathos-mcp-service-key") != state.service_key:
            return _error(401, "SERVICE_UNAUTHORIZED", "Service key missing or invalid.")
        return JSONResponse({"status": "ok"})

    async def get_account(request: Request) -> JSONResponse:
        account = authenticate(request)
        if isinstance(account, JSONResponse):
            return account
        plan = state.plan
        used = state.used_stops.get(account, 0)
        now = state.clock()
        return JSONResponse(
            {
                # A real Core resolves the owner; the double derives a plausible one from the account.
                "account": {
                    "account_id": account,
                    "email": f"{account}@example.test",
                    "company_name": state.company_name,
                },
                "plan": {
                    "id": plan.name,
                    "name": plan.name.title(),
                    "max_stops_per_request": plan.max_stops_per_request,
                    "unlimited_stops_per_request": plan.max_stops_per_request is None,
                    "features": sorted(plan.features),
                    "max_active_optimizations": plan.max_concurrent,
                },
                "usage": {
                    "stops_limit": plan.monthly_stops,
                    "stops_used": used,
                    "stops_remaining": (
                        None if plan.monthly_stops is None else max(0, plan.monthly_stops - used)
                    ),
                    "period_start": _iso(now - 86400),
                    "period_end": _iso(now + 86400),
                },
                "full_trial_available": account not in state.trial_used,
            }
        )

    async def get_catalog(request: Request) -> JSONResponse:
        account = authenticate(request)
        if isinstance(account, JSONResponse):
            return account
        return JSONResponse({"fleets": state.fleets, "vehicles": state.vehicles})

    def job_status(job: FakeJob) -> dict[str, Any]:
        elapsed = state.clock() - job.created_at
        if elapsed >= job.run_seconds:
            status = "failed" if job.fail else "completed"
            progress = None
        elif elapsed < job.run_seconds * 0.15:
            status, progress = "queued", {"percent": 0, "stage": "queued"}
        else:
            pct = int(100 * elapsed / job.run_seconds)
            stage = "assigning_stops" if pct < 50 else "sequencing_routes"
            status, progress = "running", {"percent": pct, "stage": stage}
        payload: dict[str, Any] = {
            "job_id": job.job_id,
            "status": status,
            "submitted_stops": len(job.body["stops"]),
            "created_at": _iso(job.created_at),
            "expires_at": _iso(job.created_at + 86400),
            "billing": {
                "mode": job.body.get("_billing", "plan"),
                "quota_charged": job.body.get("_billing", "plan") == "plan",
            },
        }
        if progress:
            payload["progress"] = progress
        if status in {"completed", "failed"}:
            payload["completed_at"] = _iso(job.created_at + job.run_seconds)
        if status == "failed":
            payload["failure"] = {
                "code": "OPTIMIZATION_FAILED",
                "message": "The routing engine could not finish.",
            }
        return payload

    async def create_job(request: Request) -> JSONResponse:
        account = authenticate(request)
        if isinstance(account, JSONResponse):
            return account
        key = request.headers.get("idempotency-key", "")
        if not re.match(r"^[A-Za-z0-9_.:-]{8,128}$", key):
            return _error(400, "INVALID_INPUT", "Idempotency-Key header required.")
        raw = await request.body()
        try:
            body = json.loads(raw)
        except json.JSONDecodeError:
            return _error(400, "INVALID_INPUT", "Body is not JSON.")
        body_hash = hashlib.sha256(raw).hexdigest()
        job_id = "mcp_" + hashlib.sha256(f"{account}:{key}".encode()).hexdigest()[:32]

        existing = state.jobs.get(job_id)
        if existing:
            if existing.body_hash != body_hash:
                return _error(409, "IDEMPOTENCY_CONFLICT", "Idempotency-Key reused with a different body.")
            created = job_status(existing) | {
                "idempotent_replay": True,
                "vehicles_available": _fleet(existing.body),
            }
            return JSONResponse(created, status_code=202)

        stops = body.get("stops") or []
        billing = "plan"
        dataset: dict[str, Any] | None = None
        dataset_id: str | None = None
        excluded: int | None = None
        if not stops and body.get("dataset_id"):
            ds = state.datasets.get(str(body["dataset_id"]))
            if ds is None or ds["account"] != account:
                return _error(404, "DATASET_NOT_FOUND", "Unknown dataset.")
            dataset = ds
            dataset_id = str(body["dataset_id"])
            stops = list(ds["stops"])
            exclude = set(body.get("exclude_stop_ids") or [])
            if exclude:
                stops = [s for s in stops if s["id"] not in exclude]
            excluded = len(ds["stops"]) - len(stops)
            body = {**body, "stops": stops}
            body.pop("dataset_id", None)
            body.pop("exclude_stop_ids", None)
            # The first optimization of a dataset is billed; replans are free only after it.
            if ds["billed"] and ds["replan_count"] < state.plan.free_replans:
                billing = "mcp_dataset_replan"
        features = sorted(
            {"weight_capacity" for v in body.get("vehicles", []) if "max_weight_kg" in v}
            | {"volume_capacity" for v in body.get("vehicles", []) if "max_volume_m3" in v}
            | {"time_windows" for s in stops if "time_window" in s}
        )
        plan = state.plan
        too_many = plan.max_stops_per_request is not None and len(stops) > plan.max_stops_per_request
        missing = [f for f in features if f not in plan.features]
        if too_many or missing:
            # A free replan waives the quota only: plan limits still apply and it never uses the trial.
            trial_ok = (
                billing == "plan"
                and account not in state.trial_used
                and len(stops) <= 2000
                and (len(stops) > 500 or bool(missing))
            )
            if trial_ok:
                billing = "mcp_full_trial"
            else:
                reason = "STOP_LIMIT_EXCEEDED" if too_many else "FEATURE_NOT_AVAILABLE"
                details: dict[str, Any] = {
                    "reason": reason,
                    "requested": {"stops": len(stops), "features": missing},
                    "current_limit": {"stops_per_request": plan.max_stops_per_request},
                    "eligible_plans": [{"id": "growth", "name": "Growth"}],
                    "upgrade_url": "http://localhost:3000/dashboard/billing?upgrade=growth&source=mcp",
                }
                if account not in state.trial_used and len(stops) > 2000:
                    details["full_trial"] = {"available": True, "max_stops": 2000}
                return _error(
                    403,
                    "PLAN_UPGRADE_REQUIRED",
                    "Your current Vepathos plan cannot run this request.",
                    details,
                )

        used = state.used_stops.get(account, 0)
        if billing == "plan" and plan.monthly_stops is not None and used + len(stops) > plan.monthly_stops:
            return _error(
                429,
                "QUOTA_EXCEEDED",
                "Monthly stop quota exceeded.",
                {
                    "stops_remaining": plan.monthly_stops - used,
                    "requested": len(stops),
                    "period_ends_at": "2026-10-01T00:00:00Z",
                },
            )
        active = [
            j.job_id
            for j in state.jobs.values()
            if j.account == account and state.clock() - j.created_at < j.run_seconds
        ]
        if len(active) >= plan.max_concurrent:
            response = _error(
                429,
                "CONCURRENT_OPTIMIZATION_LIMIT",
                "Too many optimizations running.",
                {"limit": plan.max_concurrent, "active_job_ids": active},
            )
            response.headers["Retry-After"] = "5"
            return response

        body["_billing"] = billing
        body["_run"] = _run_record(body, dataset_id=dataset_id, excluded=excluded)
        job = FakeJob(
            job_id,
            account,
            body_hash,
            body,
            state.clock(),
            state.run_seconds,
            fail=any(str(s.get("id", "")).startswith("FAIL") for s in stops),
        )
        state.jobs[job_id] = job
        if billing == "plan":
            state.used_stops[account] = used + len(stops)
        elif billing == "mcp_full_trial":
            state.trial_used.add(account)
        if dataset is not None:
            if billing == "mcp_dataset_replan":
                dataset["replan_count"] += 1
            elif billing == "plan":
                dataset["billed"] = True
            dataset["last_run"] = {
                "optimization_id": job_id,
                "status": "running",
                "created_at": _iso(state.clock()),
                **body["_run"],
            }
        created = job_status(job) | {
            "idempotent_replay": False,
            "vehicles_available": _fleet(body),
            "schedule_date": (body.get("schedule") or {}).get("date"),
        }
        remaining = (
            None if plan.monthly_stops is None else plan.monthly_stops - state.used_stops.get(account, 0)
        )
        created["billing"]["stops_remaining_this_period"] = remaining
        if dataset is not None:
            replans = _replan_state(dataset, plan.free_replans)
            created["billing"]["free_replans_remaining"] = replans["free_replans_remaining"]
        if billing == "mcp_full_trial":
            created["full_trial_applied"] = {
                "max_stops": 2000,
                "features": ["weight_capacity", "volume_capacity", "time_windows"],
            }
        return JSONResponse(created, status_code=202)

    def find_job(request: Request) -> FakeJob | JSONResponse:
        account = authenticate(request)
        if isinstance(account, JSONResponse):
            return account
        job = state.jobs.get(request.path_params["job_id"])
        if job is None or job.account != account:
            return _error(404, "OPTIMIZATION_NOT_FOUND", "Not found.")
        return job

    async def get_job(request: Request) -> JSONResponse:
        job = find_job(request)
        return job if isinstance(job, JSONResponse) else JSONResponse(job_status(job))

    async def get_result(request: Request) -> JSONResponse:
        job = find_job(request)
        if isinstance(job, JSONResponse):
            return job
        status = job_status(job)
        if status["status"] != "completed":
            return JSONResponse(status)
        status["request"] = job.body.get("_run")
        view = request.query_params.get("view", "summary")
        offset = int(request.query_params.get("offset", "0"))
        routes = _naive_routes(job.body)
        if view == "summary":
            limit = int(request.query_params.get("limit", "25"))
            page_items = routes[offset : offset + limit]
            assigned = sum(len(r["stop_ids"]) for r in routes)
            payload = status | {
                "summary": {
                    "stops_submitted": len(job.body["stops"]),
                    "stops_assigned": assigned,
                    "stops_unassigned": len(job.body["stops"]) - assigned,
                    "vehicles_available": _fleet(job.body),
                    "vehicles_used": len(routes),
                    "total_distance_km": round(sum(r["distance_km"] for r in routes), 2),
                    "total_duration_minutes": round(sum(r["duration_minutes"] for r in routes), 1),
                    "charged_stops": assigned if job.body.get("_billing") == "plan" else 0,
                },
                "routes": [
                    {k: v for k, v in r.items() if k != "stop_ids"} | {"stops": len(r["stop_ids"])}
                    for r in page_items
                ],
                "page": _page(offset, limit, len(routes)),
            }
            return JSONResponse(payload)
        if view == "stops":
            limit = int(request.query_params.get("limit", "200"))
            route_id = request.query_params.get("route_id")
            visits = [
                {
                    "route_id": r["route_id"],
                    "sequence": i + 1,
                    "stop_id": sid,
                    "arrival_time": _clock(job.body, i),
                }
                for r in routes
                if route_id in (None, r["route_id"])
                for i, sid in enumerate(r["stop_ids"])
            ]
            return JSONResponse(
                status | {"stops": visits[offset : offset + limit], "page": _page(offset, limit, len(visits))}
            )
        limit = int(request.query_params.get("limit", "200"))
        return JSONResponse(status | {"unassigned_stop_ids": [], "page": _page(offset, limit, 0)})

    async def create_geocode(request: Request) -> JSONResponse:
        account = authenticate(request)
        if isinstance(account, JSONResponse):
            return account
        body = await request.json()
        stops = body.get("stops") or []
        if not stops:
            return _error(422, "INVALID_INPUT", "stops is required.")
        if not body.get("depot") and not (body.get("city") or "").strip():
            return _error(422, "INVALID_INPUT", "Send a depot or a city.")
        digest = hashlib.sha256(f"{account}:{json.dumps(body, sort_keys=True)}".encode())
        job_id = "mcpg_" + digest.hexdigest()[:24]
        state.geocode_jobs[job_id] = {"account": account, "stops": stops}
        return JSONResponse(
            {"job_id": job_id, "status": "queued", "submitted_stops": len(stops), "poll_after_ms": 200},
            status_code=202,
        )

    async def get_geocode(request: Request) -> JSONResponse:
        account = authenticate(request)
        if isinstance(account, JSONResponse):
            return account
        job_id = request.path_params["job_id"]
        job = state.geocode_jobs.get(job_id)
        if job is None or job["account"] != account:
            return _error(404, "GEOCODE_NOT_FOUND", "Unknown geocode job.")
        mapped = []
        for stop in job["stops"]:
            digest = hashlib.sha256(str(stop.get("address", "")).encode()).digest()
            mapped.append(
                {
                    "id": stop.get("id"),
                    "lat": -34.6 + digest[0] / 2550.0,
                    "lng": -58.4 + digest[1] / 2550.0,
                    "band": "valid",
                    "confidence": 0.8,
                    "matched_address": stop.get("address"),
                }
            )
        return JSONResponse(
            {
                "job_id": job_id,
                "status": "completed",
                "submitted_stops": len(mapped),
                "resolved_stops": len(mapped),
                "stops": mapped,
            }
        )

    async def create_import(request: Request) -> JSONResponse:
        account = authenticate(request)
        if isinstance(account, JSONResponse):
            return account
        body = await request.json()
        if not any(k in body for k in ("content_base64", "text", "url")):
            return _error(
                422,
                "INVALID_INPUT",
                "Provide content_base64, text, or url. The delivery file did not arrive.",
            )
        fingerprint = f"{account}:{json.dumps(body, sort_keys=True)}"
        import_id = "mcpi_" + hashlib.sha256(fingerprint.encode()).hexdigest()[:24]
        dataset_id = "mcp_ds_" + hashlib.sha256(import_id.encode()).hexdigest()[:24]
        stops = [{"id": f"S{i}", "lat": -34.6 + i * 0.001, "lng": -58.4 + i * 0.001} for i in range(1, 6)]
        state.datasets[dataset_id] = {
            "account": account,
            "import_id": import_id,
            "filename": body.get("filename") or "upload.bin",
            "stops": stops,
            "billed": False,
            "replan_count": 0,
            "expires_at": _iso(state.clock() + 86400),
        }
        state.imports[import_id] = {"account": account, "dataset_id": dataset_id}
        return JSONResponse(
            {
                "import_id": import_id,
                "dataset_id": dataset_id,
                "status": "completed",
                "poll_after_ms": 200,
                "expires_at": state.datasets[dataset_id]["expires_at"],
            },
            status_code=202,
        )

    async def get_import(request: Request) -> JSONResponse:
        account = authenticate(request)
        if isinstance(account, JSONResponse):
            return account
        import_id = request.path_params["import_id"]
        row = state.imports.get(import_id)
        if row is None or row["account"] != account:
            return _error(404, "IMPORT_NOT_FOUND", "Unknown import.")
        ds = state.datasets[row["dataset_id"]]
        return JSONResponse(
            {
                "import_id": import_id,
                "dataset_id": row["dataset_id"],
                "status": "completed",
                "filename": ds["filename"],
                "expires_at": ds["expires_at"],
                **_replan_state(ds, state.plan.free_replans),
                "summary": {
                    "rows_read": len(ds["stops"]),
                    "stops": len(ds["stops"]),
                    "packages": len(ds["stops"]),
                    "coordinates_found": len(ds["stops"]),
                    "coordinates_geocoded": 0,
                    "rows_to_review": [],
                    "suggested_mapping": {},
                    "unmapped_columns": [],
                    "sample_rows": [],
                    "units": {"weight": "kg", "volume": "m3"},
                    "total_weight_kg": None,
                    "total_volume_m3": None,
                    "time_windows": {"count": 0, "dates": [], "cross_midnight": 0},
                    "needs_confirmation": False,
                },
            }
        )

    async def put_import_mapping(request: Request) -> JSONResponse:
        account = authenticate(request)
        if isinstance(account, JSONResponse):
            return account
        import_id = request.path_params["import_id"]
        row = state.imports.get(import_id)
        if row is None or row["account"] != account:
            return _error(404, "IMPORT_NOT_FOUND", "Unknown import.")
        return await get_import(request)

    async def list_datasets(request: Request) -> JSONResponse:
        account = authenticate(request)
        if isinstance(account, JSONResponse):
            return account
        items = [
            {
                "dataset_id": did,
                "filename": ds["filename"],
                "source": "file",
                "status": "ready",
                "stops": len(ds["stops"]),
                "expires_at": ds["expires_at"],
                **_replan_state(ds, state.plan.free_replans),
                "last_run": ds.get("last_run"),
            }
            for did, ds in state.datasets.items()
            if ds["account"] == account
        ]
        return JSONResponse({"datasets": items})

    async def get_dataset(request: Request) -> JSONResponse:
        account = authenticate(request)
        if isinstance(account, JSONResponse):
            return account
        dataset_id = request.path_params["dataset_id"]
        ds = state.datasets.get(dataset_id)
        if ds is None or ds["account"] != account:
            return _error(404, "DATASET_NOT_FOUND", "Unknown dataset.")
        stops = ds["stops"]
        return JSONResponse(
            {
                "dataset_id": dataset_id,
                "filename": ds["filename"],
                "source": "file",
                "status": "ready",
                "stops": len(stops),
                "with_weight": sum(1 for s in stops if "weight_kg" in s),
                "with_volume": sum(1 for s in stops if "volume_m3" in s),
                "with_time_window": sum(1 for s in stops if "time_window" in s),
                "total_weight_kg": None,
                "total_volume_m3": None,
                "needs_confirmation": False,
                "expires_at": ds["expires_at"],
                **_replan_state(ds, state.plan.free_replans),
                "last_run": ds.get("last_run"),
            }
        )

    return Starlette(
        routes=[
            Route("/api/mcp/v1/health", health, methods=["GET"]),
            Route("/api/mcp/v1/account", get_account, methods=["GET"]),
            Route("/api/mcp/v1/catalog", get_catalog, methods=["GET"]),
            Route("/api/mcp/v1/optimization/jobs", create_job, methods=["POST"]),
            Route("/api/mcp/v1/optimization/jobs/{job_id}", get_job, methods=["GET"]),
            Route("/api/mcp/v1/optimization/jobs/{job_id}/result", get_result, methods=["GET"]),
            Route("/api/mcp/v1/geocode", create_geocode, methods=["POST"]),
            Route("/api/mcp/v1/geocode/{job_id}", get_geocode, methods=["GET"]),
            Route("/api/mcp/v1/imports", create_import, methods=["POST"]),
            Route("/api/mcp/v1/imports/{import_id}", get_import, methods=["GET"]),
            Route("/api/mcp/v1/imports/{import_id}", put_import_mapping, methods=["PUT"]),
            Route("/api/mcp/v1/datasets", list_datasets, methods=["GET"]),
            Route("/api/mcp/v1/datasets/{dataset_id}", get_dataset, methods=["GET"]),
        ]
    )


def _fleet(body: dict[str, Any]) -> int:
    return sum(int(v.get("count", 1)) for v in body.get("vehicles", []))


def _page(offset: int, limit: int, total: int) -> dict[str, Any]:
    nxt = offset + limit
    return {"offset": offset, "limit": limit, "total": total, "next_offset": nxt if nxt < total else None}


def _clock(body: dict[str, Any], index: int) -> str | None:
    start = (body.get("schedule") or {}).get("route_start_time")
    if not start:
        return None
    base = datetime.strptime(start, "%H:%M")
    return (base + timedelta(minutes=8 * (index + 1))).strftime("%H:%M")


def _naive_routes(body: dict[str, Any]) -> list[dict[str, Any]]:
    """Split stops in input order across vehicles. Deliberately naive: this is not an optimizer."""

    stops = body["stops"]
    units = [v["id"] for v in body["vehicles"] for _ in range(int(v.get("count", 1)))]
    per_route = max(1, math.ceil(len(stops) / max(1, len(units))))
    routes = []
    for index, start in enumerate(range(0, len(stops), per_route)):
        chunk = stops[start : start + per_route]
        routes.append(
            {
                "route_id": f"r{index + 1}",
                "vehicle_id": units[min(index, len(units) - 1)],
                "stop_ids": [s["id"] for s in chunk],
                "distance_km": round(2.5 * len(chunk), 2),
                "duration_minutes": round(9.0 * len(chunk), 1),
            }
        )
    return routes


def main() -> None:
    import uvicorn

    state = FakeCoreState(service_key=os.environ.get("FAKE_CORE_SERVICE_KEY", "dev-service-key"))
    uvicorn.run(
        create_fake_core(state),
        host=os.environ.get("FAKE_CORE_HOST", "127.0.0.1"),
        port=int(os.environ.get("FAKE_CORE_PORT", "3900")),
    )


if __name__ == "__main__":
    main()

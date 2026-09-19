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
    # Free retries after a route plan's billed run (Core: PlanLimits.freeReplansPerRun; channel plans 0).
    free_retries: int = 1
    # Library size (Core: maxSavedPlans). None: unlimited.
    max_saved_plans: int | None = 3


FREE_RETRY_WINDOW_SECONDS = 24 * 3600
ACCOUNT_URL = "http://localhost:3000/dashboard"


@dataclass
class FakeJob:
    job_id: str
    account: str
    body_hash: str
    body: dict[str, Any]
    created_at: float
    run_seconds: float
    fail: bool = False
    plan_id: str | None = None
    # plan | plan_free_retry | mcp_full_trial
    billing: str = "plan"
    stop_keys: frozenset[str] = frozenset()
    # The billed job whose 24 h window a free retry belongs to.
    cycle: str | None = None

    def done(self, now: float) -> bool:
        return now - self.created_at >= self.run_seconds

    def succeeded(self, now: float) -> bool:
        return self.done(now) and not self.fail


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
    # Route plans (OptimizationPlan), keyed by plan_id. `state.plan` is the account's subscription.
    plans: dict[str, dict[str, Any]] = field(default_factory=dict)
    run_seconds: float = 3.0
    company_name: str | None = None
    fleets: list[dict[str, Any]] = field(default_factory=list)
    vehicles: list[dict[str, Any]] = field(default_factory=list)
    # Standing rules, keyed by automation_id, and the stores an account has connected.
    automations: dict[str, dict[str, Any]] = field(default_factory=dict)
    stores: list[dict[str, Any]] = field(default_factory=list)
    max_enabled_automations: int = 1
    clock: Any = time.time


def _error(status: int, code: str, message: str, details: dict[str, Any] | None = None) -> JSONResponse:
    body: dict[str, Any] = {"error": {"code": code, "message": message, "retryable": status >= 500}}
    if details:
        body["error"]["details"] = details
    return JSONResponse(body, status_code=status)


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, UTC).isoformat().replace("+00:00", "Z")


def _stop_key(stop: dict[str, Any]) -> str:
    """Same identity as Core's free-retry rule: id and coordinates rounded to 5 decimals."""

    return f"{stop.get('id')} {float(stop.get('lat', 0)):.5f} {float(stop.get('lng', 0)):.5f}"


def _account_url(plan_id: str, history_id: str | None = None) -> str:
    url = f"{ACCOUNT_URL}?tab=plans&plan={plan_id}"
    return f"{url}&history={history_id}" if history_id else url


def _total(stops: list[dict[str, Any]], key: str) -> float | None:
    values = [float(stop[key]) for stop in stops if stop.get(key) is not None]
    return round(sum(values), 3) if values else None


def _history_id(job_id: str) -> str:
    return "hist_" + job_id.removeprefix("mcp_")[:24]


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

    def free_retry_state(plan_id: str, stops: list[dict[str, Any]] | None) -> dict[str, Any]:
        """Core's shared rule (billing/free-retry-policy.ts): after a plan's completed billed run, a run
        within 24 h with the same stops or fewer is free, up to the account plan's allowance."""

        allowed = state.plan.free_retries
        now = state.clock()

        def charged(blocker: str, **extra: Any) -> dict[str, Any]:
            return {
                "next_optimize_charged": True,
                "free_retries_allowed": allowed,
                "free_retries_remaining": 0,
                "free_retry_window_ends_at": None,
                "charged_because": blocker,
                **extra,
            }

        if allowed <= 0:
            return charged("no_allowance")
        billed = [
            j
            for j in state.jobs.values()
            if j.plan_id == plan_id and j.billing == "plan" and j.succeeded(now)
        ]
        if not billed:
            return charged("no_billed_run")
        cycle = max(billed, key=lambda j: j.created_at + j.run_seconds)
        completed_at = cycle.created_at + cycle.run_seconds
        if now - completed_at > FREE_RETRY_WINDOW_SECONDS:
            return charged("window_closed")
        window_ends = _iso(completed_at + FREE_RETRY_WINDOW_SECONDS)
        used = sum(1 for j in state.jobs.values() if j.cycle == cycle.job_id and j.succeeded(now))
        remaining = max(0, allowed - used)
        if remaining == 0:
            return charged("allowance_used", free_retry_window_ends_at=window_ends)
        if stops is not None and not {_stop_key(stop) for stop in stops} <= cycle.stop_keys:
            return charged(
                "stops_changed", free_retries_remaining=remaining, free_retry_window_ends_at=window_ends
            )
        return {
            "next_optimize_charged": False,
            "free_retries_allowed": allowed,
            "free_retries_remaining": remaining,
            "free_retry_window_ends_at": window_ends,
            "_cycle": cycle.job_id,
        }

    def public_retry(retry: dict[str, Any]) -> dict[str, Any]:
        return {k: v for k, v in retry.items() if not k.startswith("_")}

    def find_plan(account: str, plan_id: Any) -> dict[str, Any] | None:
        plan = state.plans.get(str(plan_id)) if isinstance(plan_id, str) else None
        if plan is None or plan["account"] != account or plan["deleted"]:
            return None
        return plan

    def plan_optimizing(plan_id: str) -> bool:
        now = state.clock()
        return any(j.plan_id == plan_id and not j.done(now) for j in state.jobs.values())

    def create_plan(account: str, name: str, stops: list[dict[str, Any]]) -> dict[str, Any]:
        """A new plan, replacing the oldest unprotected library plan when the library is full."""

        cap = state.plan.max_saved_plans
        replaced: dict[str, str] | None = None
        temporary = False
        if cap is not None:
            library = sorted(
                (
                    (pid, p)
                    for pid, p in state.plans.items()
                    if p["account"] == account and not p["deleted"] and not p["temporary"]
                ),
                key=lambda item: item[1]["created_at"],
            )
            if len(library) >= cap:
                victim = next(((pid, p) for pid, p in library if not (p["kept"] or p["favorite"])), None)
                if victim is None:
                    temporary = True
                else:
                    victim[1]["deleted"] = True
                    replaced = {"id": victim[0], "displayName": victim[1]["name"]}
        now = state.clock()
        plan_id = "cmf" + hashlib.sha256(f"{account}:{len(state.plans)}:{now}".encode()).hexdigest()[:22]
        state.plans[plan_id] = {
            "account": account,
            "name": name[:200],
            "created_by": "agent",
            "stops": list(stops),
            "depot": None,
            "kept": False,
            "favorite": False,
            "temporary": temporary,
            "deleted": False,
            "created_at": now,
            "updated_at": now,
            "revision": 1,
            "last_agent_run": None,
            "last_job_id": None,
        }
        return {"plan_id": plan_id, "replaced": replaced, "temporary": temporary}

    def load_into_plan(
        account: str, plan_id: str | None, stops: list[dict[str, Any]], name: str
    ) -> dict[str, Any]:
        plan = find_plan(account, plan_id) if plan_id else None
        if plan is None:
            return create_plan(account, name, stops)
        plan["stops"] = list(stops)
        plan["updated_at"] = state.clock()
        plan["revision"] += 1
        return {"plan_id": plan_id, "replaced": None, "temporary": plan["temporary"]}

    def plan_view(plan_id: str, *, detail: bool) -> dict[str, Any]:
        plan = state.plans[plan_id]
        stops = plan["stops"]
        now = state.clock()
        last = state.jobs.get(plan["last_job_id"] or "")
        settled = last is not None and last.succeeded(now)
        view: dict[str, Any] = {
            "plan_id": plan_id,
            "name": plan["name"],
            "created_by": plan["created_by"],
            "temporary": plan["temporary"],
            "kept": plan["kept"] or plan["favorite"],
            "favorite": plan["favorite"],
            "revision": plan["revision"],
            "stops": len(stops),
            "stops_not_optimizable": 0,
            "total_weight_kg": _total(stops, "weight_kg"),
            "total_volume_m3": _total(stops, "volume_m3"),
            "with_weight": sum(1 for stop in stops if "weight_kg" in stop),
            "with_volume": sum(1 for stop in stops if "volume_m3" in stop),
            "with_time_window": sum(1 for stop in stops if "time_window" in stop),
            "depot": plan["depot"],
            "optimizing": plan_optimizing(plan_id),
            "last_run_history_id": _history_id(last.job_id) if settled and last else None,
            "last_run_at": _iso(last.created_at + last.run_seconds) if settled and last else None,
            "account_url": _account_url(plan_id, _history_id(last.job_id) if settled and last else None),
            "created_at": _iso(plan["created_at"]),
            "updated_at": _iso(plan["updated_at"]),
            **public_retry(free_retry_state(plan_id, stops)),
        }
        if detail:
            view["last_agent_run"] = plan["last_agent_run"]
        return view

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
            # A settled plan job is kept with its plan and never expires.
            "expires_at": None if status == "completed" and job.plan_id else _iso(job.created_at + 86400),
            "billing": {
                "mode": job.billing,
                "quota_charged": job.billing == "plan",
            },
        }
        if job.plan_id:
            payload["plan_id"] = job.plan_id
            if status == "completed":
                payload["history_id"] = _history_id(job.job_id)
            payload["account_url"] = _account_url(job.plan_id, payload.get("history_id"))
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
        # Core keys idempotency on the request as sent, before a plan or dataset expands its stops.
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
            if existing.plan_id and existing.plan_id in state.plans:
                created["plan_name"] = state.plans[existing.plan_id]["name"]
            return JSONResponse(created, status_code=202)

        # Exactly one stop source; every run lives in a plan (jobs/route.ts).
        sources = [k for k in ("plan_id", "dataset_id") if k in body]
        if len(sources) > 1 or (sources and body.get("stops") is not None):
            return _error(422, "INVALID_INPUT", "Send exactly one of stops, dataset_id or plan_id.")
        exclude = set(body.get("exclude_stop_ids") or [])
        if "exclude_stop_ids" in body and not sources:
            return _error(422, "INVALID_INPUT", "exclude_stop_ids only applies with plan_id or dataset_id.")
        today = datetime.fromtimestamp(state.clock(), UTC).date().isoformat()
        target_plan_id: str | None
        dataset: dict[str, Any] | None = None
        dataset_id: str | None = None
        excluded: int | None = None
        new_plan_name = str(body.get("plan_name") or f"Optimization {today}")
        if "plan_id" in body:
            plan = find_plan(account, body["plan_id"])
            if plan is None:
                return _error(404, "PLAN_NOT_FOUND", "No plan with this id exists for this account.")
            target_plan_id = str(body["plan_id"])
            stored = plan["stops"]
            if not stored:
                return _error(422, "INVALID_INPUT", "The plan has no stops.")
        elif "dataset_id" in body:
            dataset = state.datasets.get(str(body["dataset_id"]))
            if dataset is None or dataset["account"] != account:
                return _error(404, "DATASET_NOT_FOUND", "Unknown dataset.")
            dataset_id = str(body["dataset_id"])
            target_plan_id = dataset["plan_id"] if find_plan(account, dataset["plan_id"]) else None
            new_plan_name = dataset["filename"].rsplit(".", 1)[0]
            stored = dataset["stops"]
        else:
            target_plan_id = None
            stored = body.get("stops") or []
        stops = [s for s in stored if s["id"] not in exclude]
        if sources:
            if not stops:
                return _error(422, "INVALID_INPUT", "No stops left after exclude_stop_ids.")
            excluded = len(stored) - len(stops)
        depot_name = str(body.get("depot_name") or "Depot")
        body = {
            k: v
            for k, v in body.items()
            if k not in {"plan_id", "dataset_id", "exclude_stop_ids", "plan_name", "depot_name"}
        }
        body["stops"] = stops

        if target_plan_id is not None and plan_optimizing(target_plan_id):
            busy = _error(409, "PLAN_BUSY", "This plan is already optimizing. Wait for that run to finish.")
            busy.headers["Retry-After"] = "30"
            return busy

        retry = free_retry_state(target_plan_id, stops) if target_plan_id is not None else None
        billing = "plan_free_retry" if retry is not None and not retry["next_optimize_charged"] else "plan"
        # Same rule as Core's resolveConstraints: an explicit flag decides, an omitted one follows the data.
        flags = body.get("constraints") or {}
        declared = {
            "weight_capacity": any("max_weight_kg" in v for v in body.get("vehicles", [])),
            "volume_capacity": any("max_volume_m3" in v for v in body.get("vehicles", [])),
            "time_windows": any("time_window" in s for s in stops),
        }
        flag_names = {
            "weight_capacity": "weight",
            "volume_capacity": "volume",
            "time_windows": "time_windows",
        }
        features = sorted(
            name
            for name, present in declared.items()
            if (flags.get(flag_names[name]) if flags.get(flag_names[name]) is not None else present)
            and (name != "time_windows" or present)
        )
        plan = state.plan
        too_many = plan.max_stops_per_request is not None and len(stops) > plan.max_stops_per_request
        missing = [f for f in features if f not in plan.features]
        if too_many or missing:
            # A free retry waives the quota only: plan limits still apply and it never uses the trial.
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
            quota: dict[str, Any] = {
                "stops_remaining": plan.monthly_stops - used,
                "requested": len(stops),
                "period_ends_at": "2026-10-01T00:00:00Z",
            }
            if retry is not None:
                quota["free_retry"] = public_retry(retry)
            return _error(429, "QUOTA_EXCEEDED", "Monthly stop quota exceeded.", quota)
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

        # The run is written into its plan (created when needed) before the engine starts.
        placement: dict[str, Any] = {"replaced": None, "temporary": False}
        if target_plan_id is None:
            placement = create_plan(account, new_plan_name, stops)
            target_plan_id = placement["plan_id"]
            if dataset is not None:
                dataset["plan_id"] = target_plan_id
        route_plan = state.plans[target_plan_id]
        # The launch freezes the stops that run; the plan keeps all of its own (exclusions are per run).
        route_plan["stops"] = list(stored)
        route_plan["depot"] = {"name": depot_name, **(body.get("depot") or {})}
        route_plan["updated_at"] = state.clock()
        route_plan["revision"] += 1
        route_plan["last_job_id"] = job_id

        body["_billing"] = billing
        body["_run"] = _run_record(body, dataset_id=dataset_id, excluded=excluded)
        route_plan["last_agent_run"] = body["_run"]
        job = FakeJob(
            job_id,
            account,
            body_hash,
            body,
            state.clock(),
            state.run_seconds,
            fail=any(str(s.get("id", "")).startswith("FAIL") for s in stops),
            plan_id=target_plan_id,
            billing=billing,
            stop_keys=frozenset(_stop_key(stop) for stop in stops),
            cycle=retry.get("_cycle") if retry is not None and billing == "plan_free_retry" else None,
        )
        state.jobs[job_id] = job
        if billing == "plan":
            state.used_stops[account] = used + len(stops)
        elif billing == "mcp_full_trial":
            state.trial_used.add(account)
        if dataset is not None:
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
            "plan_name": route_plan["name"],
        }
        if placement["replaced"]:
            created["plan_replaced"] = placement["replaced"]
        if placement["temporary"]:
            created["plan_temporary"] = True
        remaining = (
            None if plan.monthly_stops is None else plan.monthly_stops - state.used_stops.get(account, 0)
        )
        created["billing"]["stops_remaining_this_period"] = remaining
        # Retries of this plan that stay free after this run completes.
        created["billing"]["free_retries_remaining"] = (
            max(0, int(retry["free_retries_remaining"]) - 1)
            if billing == "plan_free_retry" and retry is not None
            else 0
            if billing == "mcp_full_trial"
            else plan.free_retries
        )
        if dataset_id is not None:
            created["billing"]["dataset_id"] = dataset_id
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
        if job.plan_id:
            status["history_id"] = _history_id(job.job_id)
            status["account_url"] = _account_url(job.plan_id, status["history_id"])
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
                    "charged_stops": assigned if job.billing == "plan" else 0,
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
                    # "(unsure)" in an address stands for a match the geocoder would flag for review.
                    "band": "review" if "(unsure)" in str(stop.get("address", "")) else "valid",
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
        if "plan_id" in body and find_plan(account, body["plan_id"]) is None:
            return _error(404, "PLAN_NOT_FOUND", "No plan with this id exists for this account.")
        fingerprint = f"{account}:{len(state.imports)}:{json.dumps(body, sort_keys=True)}"
        import_id = "mcpi_" + hashlib.sha256(fingerprint.encode()).hexdigest()[:24]
        dataset_id = "mcp_ds_" + hashlib.sha256(import_id.encode()).hexdigest()[:24]
        stops = [{"id": f"S{i}", "lat": -34.6 + i * 0.001, "lng": -58.4 + i * 0.001} for i in range(1, 6)]
        filename = body.get("filename") or "upload.bin"
        # The double imports at once, so the stops load into their plan right away (datasets.ts).
        placement = load_into_plan(account, body.get("plan_id"), stops, filename.rsplit(".", 1)[0])
        state.datasets[dataset_id] = {
            "account": account,
            "import_id": import_id,
            "filename": filename,
            "stops": stops,
            "plan_id": placement["plan_id"],
            "placement": placement,
            "expires_at": _iso(state.clock() + 86400),
        }
        state.imports[import_id] = {"account": account, "dataset_id": dataset_id}
        return JSONResponse(
            {
                "import_id": import_id,
                "dataset_id": dataset_id,
                "plan_id": placement["plan_id"],
                "account_url": _account_url(placement["plan_id"]),
                "status": "completed",
                "poll_after_ms": 200,
                "expires_at": state.datasets[dataset_id]["expires_at"],
            },
            status_code=202,
        )

    def dataset_plan_fields(account: str, ds: dict[str, Any]) -> dict[str, Any]:
        """plan_id, account_url, what the next run of that plan costs and any library rotation."""

        fields: dict[str, Any] = {"plan_id": ds["plan_id"]}
        if find_plan(account, ds["plan_id"]) is not None:
            fields["account_url"] = _account_url(ds["plan_id"])
            fields.update(public_retry(free_retry_state(ds["plan_id"], ds["stops"])))
        else:
            fields["next_optimize_charged"] = True
        # Only when true, like Core.
        if ds["placement"]["replaced"]:
            fields["plan_replaced"] = ds["placement"]["replaced"]
        if ds["placement"]["temporary"]:
            fields["plan_temporary"] = True
        return fields

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
                **dataset_plan_fields(account, ds),
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
        # Same shapes as Core's route: a field name, null, or {field, unit?, format?}.
        mapping = (await request.json()).get("mapping") or {}
        for column, value in mapping.items():
            spec_ok = isinstance(value, dict) and isinstance(value.get("field"), str)
            if value is not None and not isinstance(value, str) and not spec_ok:
                return _error(
                    422,
                    "INVALID_INPUT",
                    f"mapping.{column} must be a field, null or {{field, unit, format}}.",
                )
        row["mapping"] = mapping
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
                "needs_confirmation": False,
                "expires_at": ds["expires_at"],
                **dataset_plan_fields(account, ds),
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
                **dataset_plan_fields(account, ds),
                "last_run": ds.get("last_run"),
            }
        )

    async def list_plans(request: Request) -> JSONResponse:
        account = authenticate(request)
        if isinstance(account, JSONResponse):
            return account
        try:
            limit = max(1, min(50, int(request.query_params.get("limit", "20"))))
        except ValueError:
            limit = 20
        query = (request.query_params.get("query") or "").strip().lower()
        owned = [
            (pid, p)
            for pid, p in state.plans.items()
            if p["account"] == account and not p["deleted"] and query in p["name"].lower()
        ]
        owned.sort(key=lambda item: (not item[1]["favorite"], -item[1]["updated_at"]))
        library = [p for _, p in owned if not p["temporary"]]
        cap = state.plan.max_saved_plans
        return JSONResponse(
            {
                "plans": [plan_view(pid, detail=False) for pid, _ in owned[:limit]],
                "library": {
                    "plans_in_library": len(library),
                    "max_plans": cap,
                    "kept_plans": sum(1 for p in library if p["kept"] or p["favorite"]),
                    "max_kept_plans": None if cap is None else max(0, cap - 1),
                },
            }
        )

    async def get_plan(request: Request) -> JSONResponse:
        account = authenticate(request)
        if isinstance(account, JSONResponse):
            return account
        plan_id = request.path_params["plan_id"]
        if find_plan(account, plan_id) is None:
            return _error(404, "PLAN_NOT_FOUND", "No plan with this id exists for this account.")
        return JSONResponse(plan_view(plan_id, detail=True))

    async def list_automations(request: Request) -> JSONResponse:
        account = authenticate(request)
        if isinstance(account, JSONResponse):
            return account
        rows = [row for row in state.automations.values() if row["account"] == account]
        return JSONResponse(
            {
                "automations": [{k: v for k, v in row.items() if k != "account"} for row in rows],
                "stores": state.stores,
                "limits": {
                    "max_enabled": state.max_enabled_automations,
                    "enabled": len([row for row in rows if row.get("enabled")]),
                    "max_runs_per_day": 4,
                    "max_stops_per_day": 500,
                },
                "account_url": "https://vepathos.test/dashboard/automations",
            }
        )

    async def create_automation(request: Request) -> JSONResponse:
        account = authenticate(request)
        if isinstance(account, JSONResponse):
            return account
        body = await request.json()
        if state.max_enabled_automations < 1:
            return JSONResponse(
                {
                    "error": {
                        "code": "AUTOMATION_NOT_INCLUDED",
                        "message": "Not included.",
                        "retryable": False,
                    }
                },
                status_code=403,
            )
        owned = body.get("ownedPlan") or {}
        template = state.plans.get(owned.get("templatePlanId") or "")
        if owned.get("templatePlanId") and not template:
            return JSONResponse(
                {"error": {"code": "PLAN_NOT_FOUND", "message": "No such plan.", "retryable": False}},
                status_code=404,
            )
        # The same operation_id is the same rule: the real channel derives its ids from it.
        operation = owned.get("operationId") or ""
        for existing_id, row in state.automations.items():
            if row["account"] == account and row.get("operation") == operation:
                return JSONResponse(
                    {
                        "automation": {k: v for k, v in row.items() if k not in {"account", "operation"}},
                        "missing": row.get("missing", []),
                        "enabled": False,
                        "account_url": f"https://vepathos.test/dashboard/automations/{existing_id}",
                    }
                )
        automation_id = f"auto_{len(state.automations) + 1}"
        once = body["windowFromMin"] == body["windowToMin"]

        def hhmm(minutes: int) -> str:
            return f"{minutes // 60:02d}:{minutes % 60:02d}"

        # A template whose depot is not a catalog one cannot be run from (a run refuses an ad-hoc depot):
        # the rule is still written, and what it lacks is said out loud instead of being discovered by the
        # scheduler three failures later. A plan an agent made always has an ad-hoc depot.
        depot = (template or {}).get("depot")
        missing = [] if depot and depot.get("kind") == "catalog" else ["depot"]
        row = {
            "account": account,
            "operation": operation,
            "automation_id": automation_id,
            "name": body["name"],
            "mode": body["mode"],
            "status": "draft",
            "enabled": False,
            "timezone": body["timezone"],
            "days": body["windowDays"],
            "looks_at": hhmm(body["windowFromMin"]) if once else None,
            "window_from": None if once else hhmm(body["windowFromMin"]),
            "window_to": None if once else hhmm(body["windowToMin"]),
            "every_minutes": None if once else body["everyMinutes"],
            "min_orders": body["minOrders"],
            "match_tags": body["matchTags"],
            "max_units": body["maxUnits"],
            "stops_per_vehicle": body["stopsPerVehicle"],
            "plan_name": body["name"],
            "plan_owned": True,
            "plan_missing": False,
            "depot_name": (depot or {}).get("name"),
            "run_count": 0,
            "account_url": f"https://vepathos.test/dashboard/automations/{automation_id}",
            "missing": missing,
        }
        state.automations[automation_id] = row
        return JSONResponse(
            {
                "automation": {k: v for k, v in row.items() if k not in {"account", "operation", "missing"}},
                "missing": missing,
                "enabled": False,
                "account_url": row["account_url"],
            },
            status_code=201,
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
            Route("/api/mcp/v1/plans", list_plans, methods=["GET"]),
            Route("/api/mcp/v1/plans/{plan_id}", get_plan, methods=["GET"]),
            Route("/api/mcp/v1/automations", list_automations, methods=["GET"]),
            Route("/api/mcp/v1/automations", create_automation, methods=["POST"]),
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

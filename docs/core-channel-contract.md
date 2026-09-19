# Core MCP channel contract — `/api/mcp/v1`

The HTTP contract between `vepathos-mcp` (client) and the MCP channel in Vepathos Core
(`api.vepathos.com`, server). Both sides test against this document. Breaking changes require a new
path version (`/api/mcp/v2`).

All bodies are JSON (`Content-Type: application/json`). Timestamps are ISO-8601 UTC.
Distances are kilometres, durations minutes, weights kilograms, volumes cubic metres, clock times
`HH:MM` (24 h) in the job time zone.

## Headers

| Header | Required | Description |
|---|---|---|
| `X-Vepathos-MCP-Service-Key` | yes | Authenticates the `vepathos-mcp` service. Grants no account access by itself. |
| `Authorization` | yes | `Bearer <user credential>`: an OAuth access token issued by Vepathos for `https://mcp.vepathos.com/mcp`, or a developer credential `<client_id>:<client_secret>` with scope `mcp:optimize`. The account is derived **only** from this credential. |
| `Idempotency-Key` | yes on `POST /optimization/jobs` | 8–128 chars, `[A-Za-z0-9_.:-]`. Same key + same body returns the existing job; same key + different body → `409 IDEMPOTENCY_CONFLICT`. Rejected requests do not store the key. |
| `X-Vepathos-MCP-Client` | no | Normalized client label for analytics only (`claude`, `claude-code`, `cursor`, `vscode`, `codex`, `chatgpt`, `other`). Never used for authorization. |
| `traceparent` / `tracestate` | no | W3C trace context, propagated to logs. |

The Core rejects any account/user/company identifier supplied in headers or body.

## `GET /api/mcp/v1/health`

Reachability probe for `/ready` in `vepathos-mcp`. Requires only `X-Vepathos-MCP-Service-Key`,
touches no account, database or optimizer, and returns `200 {"status": "ok"}`.

## `GET /api/mcp/v1/account`

Who the caller's credential belongs to, and what its plan allows. Read-only: it starts no job,
charges no stop quota and is not rate limited beyond the generic call limit. The account is derived
from `Authorization` like every other call — there is no account parameter.

`vepathos-mcp` publishes this as the `get_account` tool so an agent can tell the user which account
a client is connected to (connecting with a second identity is the usual cause of "my plan is
bigger than this"), and it uses the label to name the account in plan and quota errors.

```json
{
  "account": {
    "account_id": "acct_7f3c…",
    "email": "m***@stormtech.com"
  },
  "plan": {
    "id": "free",
    "name": "Free",
    "max_stops_per_request": 150,
    "unlimited_stops_per_request": false,
    "max_fleet_units": 50,
    "max_stops_per_route": null,
    "features": ["time_windows"],
    "max_active_optimizations": 1
  },
  "usage": {
    "stops_limit": 2000,
    "stops_used": 236,
    "stops_remaining": 1764,
    "period_start": "2026-09-01T00:00:00Z",
    "period_end": "2026-10-01T00:00:00Z"
  },
  "full_trial_available": true
}
```

`null` in a limit means unlimited; `unlimited_stops_per_request` states it explicitly so an absent
field and an unlimited plan stay distinguishable. `features` uses the same names the entitlement
check rejects requests with (`time_windows`, `weight_capacity`, `volume_capacity`,
`minimize_duration`), so this route can never advertise a constraint an optimization would refuse.

**`email` is already masked by Core** (`m***@domain`): the full address never crosses this channel.
`vepathos-mcp` masks again anyway — masking is idempotent — so a Core that sent a full address
would still not expose it to an agent. `company_name` is optional and currently unused: Core has no
organization name today, and a personal display name must not be presented as one. Every field is
optional: a Core that omits `usage`, say, yields a result without it rather than an error. An
unauthorized credential returns the usual `401 AUTHENTICATION_REQUIRED` / `INVALID_CREDENTIALS`.

## `GET /api/mcp/v1/catalog`

The account's own fleets and vehicles, so an optimization can use the real fleet. Read-only, no
stop quota: `GET /fleets` and `GET /vehicles` are not billable triggers. Scoped to the caller's
company by the RouteHub tenant guard.

```json
{
  "fleets": [
    {
      "fleet_id": "7",
      "name": "Reparto AM",
      "total_units": 3,
      "vehicles": [
        { "vehicle_id": "v1", "name": "Sprinter 1", "count": 2, "max_weight_kg": 1200, "max_volume_m3": null }
      ]
    }
  ],
  "vehicles": [
    { "vehicle_id": "v9", "name": "KIA", "count": null, "max_weight_kg": 800, "max_volume_m3": 6 }
  ]
}
```

Core converts every capacity to **kilograms and cubic metres** (the channel's units) and drops a
value whose unit it does not recognise: a wrong capacity plans a route that cannot be loaded, which
is worse than an unconstrained one. `vehicles[]` is the whole account catalog, `fleets[]` groups it
as the user does; both are present because a vehicle can belong to no fleet.

`vehicle_id` is the catalog id and may not fit `optimize_delivery_routes` (max 32 chars, letters,
digits, `_`, `-`, `.`): `vepathos-mcp` reshapes it before publishing, keeping it unique per fleet.
When neither resource answers, Core returns `503 BACKEND_UNAVAILABLE`; when only one fails, the
other is still returned.

## Plans

Every run lives in an `OptimizationPlan`, the same object the dashboard uses
([architecture-mcp-plans.md](architecture-mcp-plans.md)). Imports load their stops into a plan; a job
runs a plan's stops (`plan_id`), an import's copy (`dataset_id`, into the plan it loaded) or inline
`stops` (a new plan). One billing rule covers dashboard and MCP (api-doc `src/server/billing/`): a plan's
completed billed run opens a 24 h window in which `PlanLimits.freeReplansPerRun` reruns (1 on every
account plan, 0 on channel plans) are free when every stop was in the billed run (same `id` and
coordinates rounded to 5 decimals); depot, fleet and settings may change. A free retry waives the quota
only. Views that describe a plan's next run carry:

```json
{
  "next_optimize_charged": false,
  "free_retries_allowed": 1,
  "free_retries_remaining": 1,
  "free_retry_window_ends_at": "2026-09-17T12:00:00.000Z",
  "charged_because": "stops_changed"
}
```

`charged_because` is present only when charged: `no_allowance`, `no_billed_run`, `window_closed`,
`allowance_used`, `stops_changed`.

Library: Free keeps 3 plans, kept + favorite ≤ size − 1. When a new plan does not fit, Core replaces the
oldest unprotected plan and reports `plan_replaced: {id, displayName}`; when every slot is protected the
plan is created outside the library and the response carries `plan_temporary: true`.
`account_url` opens a plan (and a run, with `&history=`) in the dashboard; it requires login.

## `GET /api/mcp/v1/plans`

Query `limit` (1–50, default 20), `query` (name contains, case-insensitive). Favorites first, then
newest. Never the stops.

```json
{
  "plans": [
    {
      "plan_id": "cmf…", "name": "zona-1", "created_by": "agent", "temporary": false, "kept": false,
      "favorite": false, "revision": 7, "stops": 10931, "stops_not_optimizable": 0,
      "total_weight_kg": 21862.5, "total_volume_m3": null, "with_weight": 10931,
      "with_volume": 0, "with_time_window": 0,
      "depot": { "name": "Depot", "lat": -34.6, "lng": -58.4 }, "optimizing": false,
      "last_run_history_id": "cmf…", "last_run_at": "…",
      "account_url": "https://…/dashboard?tab=plans&plan=…&history=…",
      "created_at": "…", "updated_at": "…",
      "next_optimize_charged": true, "free_retries_allowed": 1, "free_retries_remaining": 0,
      "free_retry_window_ends_at": null, "charged_because": "no_billed_run"
    }
  ],
  "library": { "plans_in_library": 3, "max_plans": 3, "kept_plans": 2, "max_kept_plans": 2 }
}
```

`max_plans` / `max_kept_plans` are null when unlimited. `revision` changes whenever the plan's stops or
settings change (a run starting included). `vepathos-mcp` publishes this as `list_plans` and converts
`depot` to `latitude` / `longitude`.

## `GET /api/mcp/v1/automations`

The account's standing rules, the stores one could be pointed at, and how many may be switched on.
Read-only; charges nothing. A rule that looks once a day reports `looks_at` and leaves the window
fields null; one that looks inside a window reports `window_from` / `window_to` / `every_minutes`.

```json
{
  "automations": [
    {
      "automation_id": "cmf…", "name": "Reparto de la mañana", "mode": "auto", "status": "enabled",
      "enabled": true, "timezone": "America/Argentina/Buenos_Aires", "days": [1, 2, 3, 4, 5],
      "looks_at": "08:00", "window_from": null, "window_to": null, "every_minutes": null,
      "min_orders": 5, "match_tags": ["ml:flex"], "fill_by": null,
      "max_units": 3, "stops_per_vehicle": 25,
      "plan_name": "Reparto de la mañana", "plan_owned": true, "plan_missing": false,
      "depot_name": "Centro", "last_decision": "fired", "last_reason": null,
      "last_decided_at": "…", "run_count": 12, "next_look_at": "…",
      "account_url": "https://…/dashboard/automations/cmf…"
    }
  ],
  "stores": [
    { "integration_account_id": "cmf…", "kind": "mercadolibre", "name": "Mi tienda", "last_sync_at": "…" }
  ],
  "limits": { "max_enabled": 1, "enabled": 1, "max_runs_per_day": 4, "max_stops_per_day": 500 },
  "account_url": "https://…/dashboard/automations"
}
```

**No plan id.** A rule keeps a plan of its own and that id is deliberately not on the wire: an agent
holding it could pass it to `optimize_plan`, spending the rule's stops by hand and moving the
revision its next batch checks against. `plan_missing: true` means the plan a legacy rule hung off
was deleted, so it cannot run until its owner fixes that.

## `POST /api/mcp/v1/automations`

Writes a rule, **always switched off**. The body is the account channel's `createAutomationSchema`
with `ownedPlan` instead of `planId`: the rule gets a plan of its own, copied from `templatePlanId`
(depot, vehicles and optimizer settings; `copyStops` for a route that repeats), and `source` binds a
connected store in the same transaction, so a half-made pair cannot be left behind.
`ownedPlan.operationId` makes it idempotent: the same one returns the rule already written, with
`200` instead of `201`.

```json
{
  "automation": { "…": "the view above, enabled: false" },
  "missing": ["depot"],
  "enabled": false,
  "account_url": "https://…/dashboard/automations/cmf…"
}
```

`missing` is what a run would still lack, computed by Core from the workspace it just wrote —
`depot` when the copied plan's depot is not one from the account's catalog, which a run refuses.
`403 AUTOMATION_NOT_INCLUDED` when the account's plan has no automations at all, checked before
anything is written.

## `GET /api/mcp/v1/plans/{plan_id}`

One plan (the view above) plus `last_agent_run`: the run record an agent last ran it with (depot,
vehicles, schedule, objective, constraints, `submitted_stops`, optional `dataset_id` / `excluded_stops`),
or null. The free-retry fields are computed against the plan's current stops. Unknown, deleted or
other-account id → `404 PLAN_NOT_FOUND`.

The adapter also reads this before submitting `plan_id` jobs: Core keys idempotency on the body as
sent, before it expands the plan's stops, so the adapter adds the plan's `revision` to its fingerprint
(see [tools.md](tools.md#optimize_plan)).

## `POST /api/mcp/v1/optimization/jobs`

Create an asynchronous optimization job.

### Request

```json
{
  "depot": { "lat": -34.6037, "lng": -58.3816 },
  "vehicles": [
    { "id": "van", "count": 5, "max_stops": 40, "max_weight_kg": 900, "max_volume_m3": 8.0 }
  ],
  "stops": [
    {
      "id": "ORD-1001",
      "lat": -34.6090,
      "lng": -58.3920,
      "weight_kg": 12.5,
      "volume_m3": 0.04,
      "time_window": { "start": "09:00", "end": "12:00" }
    }
  ],
  "schedule": {
    "date": "2026-09-14",
    "route_start_time": "08:00",
    "time_zone": "America/Argentina/Buenos_Aires",
    "service_time_minutes": 3
  },
  "objective": "minimize_distance"
}
```

| Field | Rules |
|---|---|
| `depot` | Required. `lat` ∈ [-90, 90], `lng` ∈ [-180, 180]. One depot per job; routes start there. |
| `vehicles` | 1–50 entries. `id` 1–32 chars `[A-Za-z0-9_.-]`, unique case-insensitively. `count` 1–500 (default 1). `max_stops` ≥ 1. `max_weight_kg` > 0, `max_volume_m3` > 0. |
| `stops` | ≥ 1 entry. `id` 1–64 printable chars, unique. `weight_kg` ≥ 0, `volume_m3` ≥ 0, `time_window.start` < `end`. |
| Weight capacity | If any vehicle sets `max_weight_kg`, weight capacity is enforced: every vehicle must set `max_weight_kg` and every stop `weight_kg`. |
| Volume capacity | Same rule with `max_volume_m3` / `volume_m3`. |
| Time windows | If any stop sets `time_window`, windows are enforced and `schedule.route_start_time` is required. |
| `schedule` | Optional. `date` defaults to today in `time_zone` (default `UTC`). `service_time_minutes` 0–240. |
| `objective` | Optional, `minimize_distance` (default). `minimize_duration` only if enabled by the capability matrix and the plan. |
| Coordinates | Stops farther from the depot than the engine's preprocessing radius are rejected with `INVALID_COORDINATES` (never silently dropped). |

Unknown fields are rejected.

### Response `202 Accepted`

```json
{
  "job_id": "mcp_3f1c9a0e5b7d4c2a9e8f6b1d2c3a4b5c",
  "status": "queued",
  "idempotent_replay": false,
  "submitted_stops": 120,
  "vehicles_available": 5,
  "schedule_date": "2026-09-14",
  "created_at": "2026-09-13T18:20:00Z",
  "expires_at": "2026-09-14T18:20:00Z",
  "plan_id": "cmf…",
  "plan_name": "Optimization 2026-09-13",
  "account_url": "https://…/dashboard?tab=plans&plan=cmf…",
  "plan_replaced": { "id": "cmf…", "displayName": "Lunes" },
  "billing": {
    "mode": "plan",
    "quota_charged": true,
    "stops_remaining_this_period": 1880,
    "free_retries_remaining": 1
  }
}
```

- `billing.mode` is `plan`, `plan_free_retry` or `mcp_full_trial` (`mcp_dataset_replan` only on jobs
  from before plans). `stops_remaining_this_period` is `null` for unlimited plans. It already nets out
  in-flight reservations. `free_retries_remaining`: reruns of the plan that stay free after this run
  completes. Dataset jobs also carry `billing.dataset_id`.
- `plan_replaced` / `plan_temporary` appear only when creating the plan rotated the library.
- When the first-use trial was applied, the response includes
  `"full_trial_applied": { "max_stops": 2000, "features": ["weight_capacity", "volume_capacity", "time_windows"] }`
  and `billing.quota_charged = false`.
- `idempotent_replay: true` means an existing job was returned for the same `Idempotency-Key`.

## `GET /api/mcp/v1/optimization/jobs/{job_id}`

Lightweight status (used for polling).

```json
{
  "job_id": "mcp_3f1c…",
  "status": "running",
  "progress": { "percent": 42, "stage": "sequencing_routes" },
  "submitted_stops": 120,
  "created_at": "2026-09-13T18:20:00Z",
  "expires_at": "2026-09-14T18:20:00Z",
  "plan_id": "cmf…",
  "account_url": "https://…/dashboard?tab=plans&plan=cmf…",
  "billing": { "mode": "plan", "quota_charged": true, "stops_remaining_this_period": 1880 }
}
```

- `status`: `queued` | `running` | `completed` | `failed`.
- A plan job that settled (the first read that sees it complete, or the `mcp-plan-settlement` cron)
  adds `history_id` and `completed_at`, its `account_url` carries `&history=`, has `expires_at: null` and
  never answers `410`: its results live in the plan's history. A pending `/result` also carries
  `plan_id` and `account_url`.
- `stage`: `queued` | `assigning_stops` | `preparing_map_data` | `sequencing_routes` | `finalizing`.
- A failed job includes `"failure": { "code": "OPTIMIZATION_FAILED", "message": "…" }`. A failed job
  is not charged.
- `completed_at` is present for terminal jobs.

## `GET /api/mcp/v1/optimization/jobs/{job_id}/result`

Query: `view=summary|stops|unassigned` (default `summary`), `route_id` (optional, `stops` view),
`offset` (default 0), `limit` (summary: routes, default 25, max 200; stops/unassigned: default 200,
max 1000).

While the job is not completed, it returns the status payload above with no result data.

### `view=summary`

```json
{
  "job_id": "mcp_3f1c…",
  "status": "completed",
  "plan_id": "cmf…",
  "history_id": "cmf…",
  "account_url": "https://…/dashboard?tab=plans&plan=cmf…&history=cmf…",
  "expires_at": null,
  "request": {
    "depot": { "lat": -34.6037, "lng": -58.3816 },
    "vehicles": [{ "id": "van", "count": 5, "max_stops": 40 }],
    "schedule": { "date": "2026-09-14", "time_zone": "America/Argentina/Buenos_Aires", "route_start_time": "08:00" },
    "objective": "minimize_distance",
    "submitted_stops": 120
  },
  "summary": {
    "stops_submitted": 120,
    "stops_assigned": 118,
    "stops_unassigned": 2,
    "vehicles_available": 5,
    "vehicles_used": 4,
    "total_distance_km": 184.3,
    "total_duration_minutes": 1210.5,
    "time_windows": { "stops_with_window": 60, "met": 58, "violated": 2 },
    "charged_stops": 118
  },
  "routes": [
    { "route_id": "r1", "vehicle_id": "van", "stops": 31, "distance_km": 47.2,
      "duration_minutes": 301.0, "weight_kg": 820.4, "volume_m3": 6.1 }
  ],
  "page": { "offset": 0, "limit": 25, "total": 4, "next_offset": null }
}
```

Metrics the engine does not produce are omitted, never invented.

`request` (every completed view) is the run record: everything the job used except the stops — depot,
vehicles, schedule, objective, and `dataset_id` / `excluded_stops` for dataset jobs. It is stored when the
job is created and never changes, so a later profile edit does not rewrite a past plan. `null` for jobs
created before run records existed.

**`duration_minutes` vs `arrival_time`.** They measure different things and can both be
correct. `duration_minutes` (and `total_duration_minutes`) is the route's working time:
driving plus `service_time_minutes` at every stop (and return to depot when the engine
includes it). `arrival_time` is the driver's clock at each stop (`HH:MM`, anchored on
`schedule.route_start_time`). The span from first to last arrival is travel between those
stops, not the working duration — e.g. seven stops with 10 minutes of service each can
show arrivals ~26 minutes apart and `duration_minutes` ≈ 96 (travel + 70 minutes of
service). Do not treat `last_arrival − first_arrival` as `duration_minutes`.

### `view=stops`

```json
{
  "job_id": "mcp_3f1c…",
  "status": "completed",
  "stops": [
    { "route_id": "r1", "sequence": 1, "stop_id": "ORD-1001", "arrival_time": "08:14" }
  ],
  "page": { "offset": 0, "limit": 200, "total": 118, "next_offset": null }
}
```

Coordinates are not echoed (the caller already has them). `arrival_time` is local clock
time at the stop; see the note above for how it relates to `duration_minutes`.

### `view=unassigned`

```json
{
  "job_id": "mcp_3f1c…",
  "status": "completed",
  "unassigned_stop_ids": ["ORD-1077", "ORD-1102"],
  "page": { "offset": 0, "limit": 200, "total": 2, "next_offset": null }
}
```

## `POST /api/mcp/v1/imports`

Upload a delivery file for Smart Import and keep it as an MCP dataset (24 h TTL). Unlike geocode,
the SI job is **not** deleted when complete — Core materializes normalized stops and loads them into a
plan: the one named by `plan_id` (its stops replaced; depot, fleet and settings kept) or a new plan
named after the file.

Body (one of):

| Field | Description |
|---|---|
| `content_base64` + `filename` | File bytes from the adapter (ChatGPT `fileParams` downloaded server-side). |
| `text` + optional `filename` | Pasted delivery list (`import_delivery_text`). |
| `url` + optional `filename` | Public `https` download; private/metadata hosts rejected (SSRF). |

Optional: `timezone`, `depot_country`, `mime_type`, `plan_id` (unknown → `404 PLAN_NOT_FOUND`). Max size
`MCP_IMPORT_MAX_BYTES` (8 MiB).

### Response `202 Accepted`

```json
{
  "import_id": "si-job.hmac",
  "dataset_id": "mcp_ds_…",
  "plan_id": null,
  "status": "running",
  "poll_after_ms": 1500,
  "expires_at": "2026-09-17T12:00:00Z"
}
```

`status` may be `running`, `needs_mapping`, or `completed`. `plan_id` is null until the stops load
(the named plan when one was sent); `account_url` is present once `plan_id` is known. Missing file / bad URL / too large → `422 INVALID_INPUT`.

## `GET /api/mcp/v1/imports/{import_id}`

Poll. When ready: `dataset_id`, `expires_at`, `summary` (counts, mapping, sample, units,
`needs_confirmation`), `plan_id`, `account_url`, the free-retry fields, and `plan_replaced` /
`plan_temporary` when loading the stops rotated the library (only when true; `summary` no longer
carries them). **Never returns all rows.** While the named
plan is optimizing the stops wait (status stays `running`) and load when it finishes.

## `PUT /api/mcp/v1/imports/{import_id}`

Body `{ "mapping": { "SourceCol": "vepathos_field", … } }` — correct Smart Import column mapping
without re-upload. Returns updated summary + status.

## `GET /api/mcp/v1/datasets`

List datasets for the account (MCP imports; web-app datasets when exposed). Query `limit` (1–100).

```json
{
  "datasets": [
    {
      "dataset_id": "mcp_ds_…",
      "plan_id": "cmf…",
      "account_url": "https://…/dashboard?tab=plans&plan=cmf…",
      "filename": "orders.xlsx",
      "source": "file",
      "status": "ready",
      "stops": 8200,
      "needs_confirmation": false,
      "expires_at": "…",
      "created_at": "…",
      "next_optimize_charged": true,
      "free_retries_allowed": 1,
      "free_retries_remaining": 0,
      "free_retry_window_ends_at": null,
      "charged_because": "no_billed_run",
      "plan_replaced": { "id": "cmf…", "displayName": "Lunes" },
      "last_run": null
    }
  ]
}
```

## `GET /api/mcp/v1/datasets/{dataset_id}`

One dataset's counts, for a preflight to state the stops and the charge. **Never returns the stops.**

```json
{
  "dataset_id": "mcp_ds_…",
  "plan_id": "cmf…",
  "account_url": "https://…/dashboard?tab=plans&plan=cmf…",
  "filename": "orders.xlsx",
  "source": "file",
  "status": "ready",
  "stops": 8200,
  "with_weight": 8200,
  "with_volume": 0,
  "with_time_window": 1200,
  "total_weight_kg": 10450.5,
  "total_volume_m3": null,
  "needs_confirmation": false,
  "created_at": "…",
  "expires_at": "…",
  "next_optimize_charged": false,
  "free_retries_allowed": 1,
  "free_retries_remaining": 1,
  "free_retry_window_ends_at": "2026-09-17T12:00:00.000Z",
  "last_run": {
    "optimization_id": "mcp_3f1c…",
    "status": "completed",
    "created_at": "…",
    "depot": { "lat": 59.778, "lng": 14.94091 },
    "vehicles": [{ "id": "1", "count": 137, "min_stops": 65, "max_stops": 99 }],
    "schedule": { "date": "2026-09-16", "time_zone": "Europe/Stockholm", "route_start_time": "08:05" },
    "objective": "minimize_distance",
    "submitted_stops": 10931,
    "dataset_id": "mcp_ds_…",
    "excluded_stops": 0
  }
}
```

`next_optimize_charged` is about the **next** run of the dataset's plan with the dataset's stops: `true`
when the plan has no billed run in the last 24 h, its free retry is used or the stops changed, `false`
when the next run is the free retry (the list view assumes the same stops). It says nothing about
whether the dataset was optimized before; `last_run` does (latest run of the dataset, any status, `null`
when never optimized). The list view carries the same `last_run`; both views carry `plan_replaced` /
`plan_temporary` from the import, only when true.

`with_*` count the stops carrying that value: a capacity the request enforces needs it on every stop,
and stored time windows are always enforced. Unknown, expired or other-account id →
`404 DATASET_NOT_FOUND`.

## `POST /api/mcp/v1/optimization/jobs` — `plan_id` / `dataset_id`

Instead of inline `stops[]`, the body may carry `plan_id` (the plan's stops) or `dataset_id` (the
import's copy, run in the plan it loaded), with optional `exclude_stop_ids`. Exactly one source: sending
two, or `exclude_stop_ids` with inline stops, is `422 INVALID_INPUT`. Optional `plan_name` (inline
stops: the new plan's name, default "Optimization YYYY-MM-DD") and `depot_name` (default "Depot").
Core expands the stops before entitlement checks and writes the run's depot, fleet and schedule to the
plan draft. The launch freezes the stops that run; `exclude_stop_ids` applies to that run only and the
plan keeps all its stops (a dataset run restores the import's full stop list in the plan).

- Billing follows the plan rule above: a free retry is `billing.mode = plan_free_retry` and reserves
  no quota; anything else is billed like an inline job (or the one-time trial).
- A free retry waives the quota only. Plan limits (stops per request, features, fleet size, stops per
  route) are always enforced, so a retry the plan cannot run is `403 PLAN_UPGRADE_REQUIRED`.
- A quota rejection of a plan run carries `details.free_retry` with the fields above.
- `404 PLAN_NOT_FOUND` / `DATASET_NOT_FOUND`; `409 PLAN_BUSY` (retryable, `Retry-After: 30`) while the
  plan is optimizing; `422 INVALID_INPUT` for a plan without stops, with stop ids an agent cannot use,
  or paused in the dashboard.
- An idempotent replay carries `plan_id`, `plan_name` and `account_url`.

`vehicles[].min_stops` defaults to 1 when omitted. `schedule.max_route_minutes` turns on
`rebalance_by_time` for that job only.

## `POST /api/mcp/v1/optimization/jobs/{job_id}/map`

Temporary public map (contract in [shared-route-maps.md](shared-route-maps.md)). For a plan job the
share is the history run's share, the same link the dashboard gives for that run.

## `POST /api/mcp/v1/geocode`

Start a Smart Import geocode job. Requires a `depot` `{lat,lng}` or `city`. `stops[]` are
`id` + `address` (optional city/region/postcode/country). Max 500 stops. **Geocode deletes the SI
job when finished** (quota only); use `/imports` to keep a dataset.

### Response `202 Accepted`

```json
{
  "job_id": "si-job-id.hmac",
  "status": "queued",
  "submitted_stops": 2,
  "poll_after_ms": 1500
}
```

## `GET /api/mcp/v1/geocode/{job_id}`

Poll. `running` while Smart Import works; `completed` includes `stops[]` with `id`, `lat`, `lng`,
`band` (`valid` | `review` | `needs_geocoding`), `confidence`, and `matched_address` (gazetteer
match text — prefer over confidence alone when reviewing pins). Usable pins are charged to the
account Smart Import quota. Unknown / other-account handle → `404 GEOCODE_NOT_FOUND`.

## Errors

Every error has the same envelope:

```json
{
  "error": {
    "code": "PLAN_UPGRADE_REQUIRED",
    "message": "Your current Vepathos plan supports up to 150 stops per optimization.",
    "retryable": false,
    "details": { }
  }
}
```

| HTTP | `code` | `details` |
|---|---|---|
| 400/422 | `INVALID_INPUT` | `issues: [{path, message}]` (first 20) |
| 422 | `INVALID_COORDINATES` | `stop_ids`, `max_distance_km` |
| 403 | `PLAN_UPGRADE_REQUIRED` | `reason` (`STOP_LIMIT_EXCEEDED` \| `FEATURE_NOT_AVAILABLE` \| `VEHICLE_LIMIT_EXCEEDED` \| `ROUTE_STOP_LIMIT_EXCEEDED`), `requested`, `current_limit`, `required_capability`, `eligible_plans: [{id, name}]`, `upgrade_url` or `contact_url`, optional `full_trial: {available, max_stops}` |
| 429 | `QUOTA_EXCEEDED` | `stops_remaining`, `requested`, `period_ends_at`, optional `upgrade_url`, `free_retry` (plan runs) |
| 429 | `CONCURRENT_OPTIMIZATION_LIMIT` | `limit`, `active_job_ids`; `Retry-After` header |
| 409 | `IDEMPOTENCY_CONFLICT` | — |
| 413 | `PAYLOAD_TOO_LARGE` | `limit_bytes`, `received_bytes` |
| 401 | `AUTHENTICATION_REQUIRED` / `INVALID_CREDENTIALS` | — (user factor missing/invalid) |
| 401 | `SERVICE_UNAUTHORIZED` | — (service key missing/invalid; an operator problem) |
| 404 | `OPTIMIZATION_NOT_FOUND` | — (unknown job or owned by another account) |
| 404 | `GEOCODE_NOT_FOUND` | — |
| 404 | `IMPORT_NOT_FOUND` / `DATASET_NOT_FOUND` | — (wrong account or expired) |
| 404 | `PLAN_NOT_FOUND` | — (unknown, deleted or other-account plan) |
| 409 | `PLAN_BUSY` | — (`Retry-After` header; the plan is optimizing) |
| 410 | `GEOCODE_EXPIRED` | — |
| 410 | `OPTIMIZATION_EXPIRED` | — (never for a settled plan job) |
| 404 | `CHANNEL_DISABLED` | — |
| 503 | `BACKEND_UNAVAILABLE` | `Retry-After` header |
| 500 | `INTERNAL_ERROR` | — |

Messages never contain stack traces, file paths, worker names or queue errors.

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
  "billing": {
    "mode": "plan",
    "quota_charged": true,
    "stops_remaining_this_period": 1880
  }
}
```

- `billing.mode` is `plan` or `mcp_full_trial`. `stops_remaining_this_period` is `null` for
  unlimited plans. It already nets out in-flight reservations.
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
  "billing": { "mode": "plan", "quota_charged": true, "stops_remaining_this_period": 1880 }
}
```

- `status`: `queued` | `running` | `completed` | `failed`.
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
  "expires_at": "2026-09-14T18:20:00Z",
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

Coordinates are not echoed (the caller already has them).

### `view=unassigned`

```json
{
  "job_id": "mcp_3f1c…",
  "status": "completed",
  "unassigned_stop_ids": ["ORD-1077", "ORD-1102"],
  "page": { "offset": 0, "limit": 200, "total": 2, "next_offset": null }
}
```

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
| 429 | `QUOTA_EXCEEDED` | `stops_remaining`, `requested`, `period_ends_at`, optional `upgrade_url` |
| 429 | `CONCURRENT_OPTIMIZATION_LIMIT` | `limit`, `active_job_ids`; `Retry-After` header |
| 409 | `IDEMPOTENCY_CONFLICT` | — |
| 413 | `PAYLOAD_TOO_LARGE` | `limit_bytes`, `received_bytes` |
| 401 | `AUTHENTICATION_REQUIRED` / `INVALID_CREDENTIALS` | — (user factor missing/invalid) |
| 401 | `SERVICE_UNAUTHORIZED` | — (service key missing/invalid; an operator problem) |
| 404 | `OPTIMIZATION_NOT_FOUND` | — (unknown job or owned by another account) |
| 410 | `OPTIMIZATION_EXPIRED` | — |
| 404 | `CHANNEL_DISABLED` | — |
| 503 | `BACKEND_UNAVAILABLE` | `Retry-After` header |
| 500 | `INTERNAL_ERROR` | — |

Messages never contain stack traces, file paths, worker names or queue errors.

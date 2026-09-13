# Tools

Vepathos MCP v1 exposes exactly two tools. There is intentionally no cancel tool: a submitted
optimization always runs to completion.

| Tool | Title | Annotations |
|---|---|---|
| `optimize_delivery_routes` | Optimize delivery routes | `readOnlyHint: false`, `destructiveHint: false`, `idempotentHint: true`, `openWorldHint: false` |
| `get_optimization_result` | Get optimization result | `readOnlyHint: true`, `destructiveHint: false`, `idempotentHint: true`, `openWorldHint: false` |

`optimize_delivery_routes` is idempotent because identical arguments (including the resolved delivery
date) map to the same optimization for the connected account. Retrying never creates a second job or a
second charge.

## `optimize_delivery_routes`

Assigns stops to vehicles and sequences each route from one depot (vehicle routing problem).

### Input

| Field | Type | Required | Notes |
|---|---|---|---|
| `depot.latitude`, `depot.longitude` | number | yes | Decimal degrees (WGS84). |
| `vehicles[]` | array (1–50) | yes | Vehicle types. |
| `vehicles[].vehicle_id` | string | yes | `[A-Za-z0-9_.-]`, max 32, unique (case-insensitive). Echoed on routes. |
| `vehicles[].count` | integer 1–500 | no (1) | Identical units available. |
| `vehicles[].max_stops` | integer | no | Maximum stops per vehicle route. |
| `vehicles[].max_weight_kg` | number > 0 | no | Setting it on any vehicle **enforces** weight capacity. Every vehicle and stop must then carry weight. |
| `vehicles[].max_volume_m3` | number > 0 | no | Same rule for volume. |
| `stops[]` | array (≥ 1) | yes | Coordinates are required; addresses are not geocoded. |
| `stops[].stop_id` | string | yes | Max 64 chars, unique. Echoed in results. |
| `stops[].latitude`, `stops[].longitude` | number | yes | Decimal degrees. |
| `stops[].weight_kg` | number ≥ 0 | conditional | Required when weight capacity is enforced. |
| `stops[].volume_m3` | number ≥ 0 | conditional | Required when volume capacity is enforced. |
| `stops[].time_window.start` / `.end` | `HH:MM` | no | Local time in `schedule.time_zone`; `start < end`. |
| `schedule.date` | `YYYY-MM-DD` | no | Defaults to today in `schedule.time_zone`. |
| `schedule.route_start_time` | `HH:MM` | conditional | Required when any stop has a time window. |
| `schedule.time_zone` | IANA name | no (`UTC`) | e.g. `America/New_York`. |
| `schedule.service_time_minutes` | number 0–240 | no | Minutes spent at each stop. |
| `idempotency_key` | string 8–128 | no | Override the automatic deduplication key. |

Unknown fields are rejected with `INVALID_INPUT`, so an unsupported constraint is never silently
ignored.

### Example

```json
{
  "depot": { "latitude": 40.7128, "longitude": -74.0060 },
  "vehicles": [ { "vehicle_id": "van", "count": 35, "max_weight_kg": 900 } ],
  "stops": [
    { "stop_id": "ORD-10045", "latitude": 40.7306, "longitude": -73.9352, "weight_kg": 18.5,
      "time_window": { "start": "09:00", "end": "12:00" } }
  ],
  "schedule": { "date": "2026-09-14", "route_start_time": "07:30", "time_zone": "America/New_York",
                "service_time_minutes": 4 }
}
```

### Output (structured content)

```json
{
  "optimization_id": "mcp_3f1c9a0e5b7d4c2a9e8f6b1d2c3a4b5c",
  "status": "running",
  "idempotent_replay": false,
  "submitted_stops": 3200,
  "vehicles_available": 35,
  "schedule_date": "2026-09-14",
  "expires_at": "2026-09-15T14:02:11Z",
  "stops_remaining_this_period": 146800,
  "progress": { "percent": 35, "stage": "assigning_stops" },
  "poll_after_seconds": 10
}
```

When the optimization finishes within the call (small problems), `status` is `completed` and `result`
contains the same payload as `get_optimization_result` with `detail=summary`.

If the account's one-time full-feature trial was used, `full_trial_applied` reports
`{max_stops, features, quota_charged: false}`.

## `get_optimization_result`

| Field | Type | Required | Notes |
|---|---|---|---|
| `optimization_id` | string | yes | From `optimize_delivery_routes`. |
| `detail` | `summary` \| `stops` \| `unassigned` | no (`summary`) | Result view. |
| `route_id` | string | no | `detail=stops` only: one route's sequence. |
| `offset` | integer ≥ 0 | no (0) | Pagination. |
| `limit` | integer 1–1000 | no | Defaults: 25 routes (summary), 200 items (stops/unassigned). |

While running, the tool waits up to about 20 seconds for completion, then returns `status` and
`progress` with `poll_after_seconds`.

### `detail=summary`

```json
{
  "optimization_id": "mcp_3f1c…",
  "status": "completed",
  "detail": "summary",
  "expires_at": "2026-09-15T14:02:11Z",
  "summary": {
    "stops_submitted": 3200, "stops_assigned": 3197, "stops_unassigned": 3,
    "vehicles_available": 35, "vehicles_used": 33,
    "total_distance_km": 2410.7, "total_duration_minutes": 15230.0,
    "time_windows": { "stops_with_window": 1200, "met": 1188, "violated": 12 },
    "charged_stops": 3197
  },
  "routes": [
    { "route_id": "r1", "vehicle_id": "van", "stops": 97, "distance_km": 71.3, "duration_minutes": 462.0, "weight_kg": 884.2 }
  ],
  "page": { "offset": 0, "limit": 25, "total": 33, "next_offset": 25 }
}
```

### `detail=stops`

Ordered `stop_id`s with estimated arrival (`HH:MM`). Coordinates are never echoed.

### `detail=unassigned`

Ids of stops that could not be routed.

## Errors

Errors are tool results with `isError: true` and a structured `error` object:

```json
{
  "error": {
    "code": "PLAN_UPGRADE_REQUIRED",
    "message": "Your current Vepathos plan allows up to 150 stops per optimization. This request contains 2,800 stops.",
    "retryable": false,
    "suggestion": "Reduce the number of stops or upgrade the Vepathos plan.",
    "details": {
      "reason": "STOP_LIMIT_EXCEEDED",
      "requested": { "stops": 2800 },
      "current_limit": { "stops_per_request": 150 },
      "eligible_plans": [ { "id": "growth", "name": "Growth" } ],
      "upgrade_url": "https://api.vepathos.com/dashboard/billing?upgrade=growth&reason=STOP_LIMIT_EXCEEDED&source=mcp"
    }
  }
}
```

| Code | Meaning | Retryable |
|---|---|---|
| `INVALID_INPUT` | A field is missing, malformed or unknown (`details.issues`). | no |
| `NO_STOPS` / `NO_VEHICLES` | Empty stop or vehicle list. | no |
| `INVALID_COORDINATES` | Coordinates out of range or too far from the depot. | no |
| `PLAN_UPGRADE_REQUIRED` | The plan cannot run this request (stops per request, feature, fleet size, stops per route). Eligible plans and upgrade URL come from Vepathos. | no |
| `QUOTA_EXCEEDED` | Not enough stops left in the current billing period. | no |
| `CONCURRENT_OPTIMIZATION_LIMIT` | Too many optimizations running; wait for `active_optimization_ids`. | yes |
| `IDEMPOTENCY_CONFLICT` | `idempotency_key` reused with different arguments. | no |
| `PAYLOAD_TOO_LARGE` | Request body too large; split the problem. | no |
| `AUTHENTICATION_REQUIRED` / `INVALID_CREDENTIALS` | Reconnect the connector. | no |
| `RATE_LIMITED` | Too many calls from this connection. | yes |
| `OPTIMIZATION_NOT_FOUND` / `OPTIMIZATION_EXPIRED` | Unknown id, or results past retention (24 h). | no |
| `OPTIMIZATION_FAILED` | The optimization ended without a result; it was not charged. | yes |
| `BACKEND_UNAVAILABLE` / `TIMEOUT` | Temporary; identical requests are safe to resend. | yes |
| `INTERNAL_ERROR` | Unexpected error. | no |

## Capability notes

The semantics below are verified against the real optimizer in the capability matrix (step 1 of the
implementation plan). Tool descriptions must stay consistent with them:

- One depot per optimization; routes start at the depot.
- Weight and volume capacities are enforced only when declared (and only if the plan or the one-time
  trial includes them).
- Time windows are optimized with tolerances; stops that cannot meet their window are reported in
  `summary.time_windows.violated` (verification pending on whether the engine instead leaves them
  unassigned).
- The objective is minimum total distance. Duration-based routing is exposed only after verification.

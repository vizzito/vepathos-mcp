# Tools

# Vepathos MCP exposes six read/plan tools, plus six import/dataset tools when
# `MCP_IMPORT_TOOLS_ENABLED=true` (off by default, so deploying the code publishes nothing new; the
# server instructions only name the tools a server publishes). There is intentionally no cancel tool:
# a submitted optimization always runs to completion.

| Tool | Title | Annotations |
|---|---|---|
| `import_delivery_file` | Import delivery file | `readOnlyHint: false` |
| `import_delivery_text` | Import pasted deliveries | `readOnlyHint: false` |
| `get_import_result` | Get import result | `readOnlyHint: true` |
| `update_import_mapping` | Update import mapping | `readOnlyHint: false` |
| `optimize_dataset` | Optimize imported dataset | `readOnlyHint: false` |
| `list_datasets` | List datasets | `readOnlyHint: true` |
| `geocode_addresses` | Geocode addresses | `readOnlyHint: false`, `destructiveHint: false`, `idempotentHint: true`, `openWorldHint: false` |
| `get_geocode_result` | Get geocode result | `readOnlyHint: true`, `destructiveHint: false`, `idempotentHint: true`, `openWorldHint: false` |
| `optimize_delivery_routes` | Optimize delivery routes | `readOnlyHint: false`, `destructiveHint: false`, `idempotentHint: true`, `openWorldHint: false` |
| `get_optimization_result` | Get optimization result | `readOnlyHint: true`, `destructiveHint: false`, `idempotentHint: true`, `openWorldHint: false` |
| `list_fleet` | List fleet | `readOnlyHint: true`, `destructiveHint: false`, `idempotentHint: true`, `openWorldHint: false` |
| `get_account` | Get connected account | `readOnlyHint: true`, `destructiveHint: false`, `idempotentHint: true`, `openWorldHint: false` |

`optimize_delivery_routes` is idempotent because identical arguments (including the resolved delivery
date) map to the same optimization for the connected account. Retrying never creates a second job or a
second charge.

Large files (hundreds+ stops): use `import_delivery_file` → `get_import_result` → `optimize_dataset`
instead of pasting `stops[]`. See [large-payloads.md](large-payloads.md).

## `import_delivery_file` / `import_delivery_text`

Upload a delivery file (ChatGPT `fileParams` or `url`) or a short pasted list. Returns `import_id`.
Poll `get_import_result` for `dataset_id`, `summary` (never all rows), and `needs_confirmation`.
`update_import_mapping` corrects columns without re-upload. `list_datasets` lists ready handles.
`optimize_dataset` shares billing/idempotency with optimize. The first run of a dataset is charged;
after it, the plan's free replans apply (Free 1, Starter 1, Growth 2, Scale 3, Enterprise 5), which waive
the quota but not the plan limits.
`get_import_result` and `list_datasets` report `next_optimize_charged` (whether the **next** run is
charged) and `free_replans_remaining`; a run reports `quota_charged` and `free_replans_remaining`.

Every optimization keeps a run record (depot, vehicles, schedule, objective, dataset). `list_datasets`
shows each dataset's `last_run` and `get_optimization_result` returns it as `request`, so a new chat can
repeat or vary a run after confirming the depot and departure with the user, instead of asking for
everything again. When the account fleet cannot cover the stops within `max_stops`, the instructions
tell the agent to offer increasing the vehicle count to cover the demand, not a "test" fleet.

With `confirmed: false`, `optimize_dataset` reads `GET /datasets/{dataset_id}` and its preflight states
the real stop count (minus `exclude_stop_ids`), `charges_stops` (0 on a free replan), the plan check,
and warnings for runs Core would reject: capacity enforced while some stops lack that value
(`stops_without_weight` / `stops_without_volume`), time windows without `route_start_time`, or an
import that still needs confirmation. Time windows stored in a dataset are always enforced.

## `list_fleet`

The vehicles and fleets the account already has, with capacity in kg and m³, shaped to drop into
`optimize_delivery_routes` as `vehicles[]`. No arguments, read-only, charges no stops. `empty: true`
means the account has no fleet loaded — then ask the user to describe it.

Catalog ids are reshaped to the `vehicle_id` pattern optimize accepts (a RouteHub UUID is longer
than the 32-character limit) and kept unique within each fleet, so the output can be passed through
unchanged. Requires `GET /api/mcp/v1/catalog` in Core.

Use it before planning a real delivery day: a fleet invented in conversation produces a geometric
plan that ignores what each vehicle carries, and route ids nobody in the operation recognises.

## `get_account`

Reports which Vepathos account the connection uses and what its plan allows: `account_label`
(company name, or an email masked to `m***@domain`), `account_id`, `plan` (name, stops per
optimization, fleet and route limits, features, concurrent optimizations), `usage` (stops used and
remaining, period dates) and `full_trial_available`. Takes no arguments — the account comes from the
credential, and cannot be chosen per call. Read-only and free: it charges no stops.

It exists because a client can be connected to a different account than the user assumes (an older
sign-in, a second company, an account created during OAuth), which otherwise only surfaces as a plan
or quota rejection. Plan and quota errors therefore also carry `details.connected_account` with the
same label, cached briefly per caller.

Requires `GET /api/mcp/v1/account` in Core; until Core serves it, the tool returns a not-found error.

## `geocode_addresses`

Turns street addresses into coordinates via Vepathos Smart Import. Requires `depot` or `city`.
Unresolved rows return `band=needs_geocoding` and null coordinates (`unresolved_stop_ids`).
Pins with `band=review` or confidence below 0.8 are listed in `review_stop_ids`.
If `needs_confirmation` is true, the assistant must ask before calling `optimize_delivery_routes`.
Charges Smart Import quota.

## `optimize_delivery_routes`

Assigns stops to vehicles and sequences each route from one depot (vehicle routing problem).

### Input

| Field | Type | Required | Notes |
|---|---|---|---|
| `depot.latitude`, `depot.longitude` | number | yes | Decimal degrees (WGS84). |
| `vehicles[]` | array (1–50) | yes | Vehicle types. |
| `vehicles[].vehicle_id` | string | yes | `[A-Za-z0-9_.-]`, max 32, unique (case-insensitive). Echoed on routes. |
| `vehicles[].count` | integer 1–500 | no (1) | Identical units available. |
| `vehicles[].min_stops` | integer | no (1) | Soft floor per route. Warns if > floor(max×0.8). |
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
| `schedule.max_route_minutes` | number 30–1440 | no | Soft journey cap; turns on `rebalance_by_time` for this job only. |
| `idempotency_key` | string 8–128 | no | Override the automatic deduplication key. |
| `confirmed` | boolean | no (`false`) | `false` returns a preflight and charges nothing. `true` submits the job. |

Unknown fields are rejected with `INVALID_INPUT`, so an unsupported constraint is never silently
ignored.

### Confirmation preflight

Optimizing spends the account's monthly stops, and any changed request is charged again, so the
tool is called twice. With `confirmed: false` (the default) nothing reaches Core and nothing is
charged: the response carries `preflight` instead of an optimization, for the agent to show the
user before spending their quota. `MCP_CONFIRM_BEFORE_OPTIMIZE=false` disables the gate for
unattended callers, which then optimize in one call.

The tool description, the server instructions and the input schema follow the flag. With it off,
`confirmed` is no longer advertised and the description asks the agent to confirm the charge before
its single call; a client that still sends `confirmed` from a cached schema is accepted, not rejected.
A text that kept promising a free first call would make the agent report a real, charged optimization
as a preview.

```json
{
  "preflight": {
    "stops": 12, "charges_stops": 12,
    "total_weight_kg": 5.28, "total_volume_m3": 0.12, "stops_with_time_window": 0,
    "depot": { "latitude": -34.6038, "longitude": -58.3807 },
    "vehicle_types": 1, "vehicle_units": 2,
    "constraints_enforced": ["weight_capacity", "volume_capacity"],
    "objective": "minimize_distance",
    "schedule_date": "2026-09-16", "route_start_time": "10:30",
    "time_zone": "America/Argentina/Buenos_Aires", "service_time_minutes": 10,
    "stops_identity": "mcp-si-9f2c…",
    "plan": { "account_label": "Stormtech", "plan_name": "Scale", "max_stops_per_request": 15000,
              "stops_remaining": 349964, "stops_remaining_after": 349952, "fits": true },
    "confirm_with": "…call again with the same arguments and confirmed=true."
  }
}
```

`plan` is absent when the account could not be read: a preflight never fails on that, it just says
less. `stops_identity` is the depot plus the stop set, so two variants of one delivery day — the
pair that gets charged twice — are recognisable as the same day. `missing_features` lists
constraints the request needs that the plan lacks.

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

`duration_minutes` / `total_duration_minutes` is driving plus service time at every stop
(and return to depot when included). It is not `last_arrival − first_arrival`.

### `detail=stops`

Ordered `stop_id`s with estimated arrival (`HH:MM`, anchored on `schedule.route_start_time`).
That clock is what the driver sees; the span between the first and last arrival is travel
between stops, not the route's `duration_minutes` (which also counts service). Coordinates
are never echoed.

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
| `PLAN_UPGRADE_REQUIRED` | The plan cannot run this request (stops per request, feature, fleet size, stops per route). Eligible plans come from Vepathos, plus `upgrade_url` when paid self-serve is on, or `contact_url` while only Free is offered. | no |
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
- The objective is minimum total distance. Duration-based routing is exposed only after
  verification, so `get_account` filters `minimize_duration` out of the plan's advertised features
  (`UNEXPOSED_FEATURES` in `tools/account.py`): the instructions tell the model to offer a
  constraint when the plan lists it, and `OptimizeInput` has no `objective` to request it with.

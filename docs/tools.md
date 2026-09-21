# Tools

This document explains how the tools **behave**: plans, the confirmation before a charge, master data,
errors. The list of tools, their inputs, annotations and the workflows that chain them are in
[tools-reference.md](tools-reference.md), which is **generated from the tool registry**
(`vepathos-mcp tools --write-docs`; a test fails when it is stale), so it cannot disagree with the
server.

Optional groups are published by a switch, off by default so that deploying the code publishes nothing
new, and the server instructions only name the tools a server publishes: `MCP_IMPORT_TOOLS_ENABLED`
(imports), `MCP_MAP_SHARES_ENABLED` (`create_optimization_map`) and `MCP_CATALOG_WRITE_TOOLS_ENABLED`
(`manage_vehicle`, `manage_depot`). There is intentionally no cancel tool: a submitted optimization
always runs to completion.

## Server instructions

`server_instructions()` in `src/vepathos_mcp/tools/descriptions.py` is the dispatcher prompt for **every
client of this MCP**, not a ChatGPT-only system prompt and not a page on the website. Native MCP
clients read it from `initialize` (`result.instructions`) once per session. The dashboard `/ai` chat
must copy that same string into the Responses API `instructions` (OpenAI does not forward MCP
instructions). A new chat, reconnect, or MCP process restart is what picks up a change; refreshing
the dashboard page is not enough if the Python MCP process is old.

One text, not a ChatGPT fork and a Claude fork. OpenAI documents that ChatGPT and Codex treat the
**first 512 characters** as self-contained, so that window is a flag-free FIRST/THEN inspect
(`get_account`, `list_fleet`, `list_plans`) before any brochure answer. The numbered loop after that
is the dispatcher Claude already followed (inspect → propose → yes → run → summarize). Rules for one
tool also live in that tool's description, because some hosts truncate or ignore `instructions`.

**Order is part of the contract.** Claude Code keeps about **2,048 characters** of the instructions,
whatever the model; ChatGPT and Codex keep 512 as self-contained; the dashboard passes all of it. So the
text runs: the 512-character lead, then who to be and how to speak (the user's language, no tool names),
then the loop in five short steps (inspect, propose with numbers and ask which fleet, run only after a
yes, report with the percent, files), and only then what elaborates it (context, account, plans,
automations, master data, when to ask). `test_what_a_cutting_host_keeps` fails when a rule the loop
needs falls past the cut, for every flag combination. `vepathos-mcp doctor` prints the length.

- ChatGPT, Claude, Gemini, Cursor and Codex: `initialize`.
- Vepathos AI (`vepathos-router-client`, `/ai`): `getMcpServerProfile()` reads `initialize`, then
  `buildAiInstructions()` prepends account chrome (email, plan, language, cards). Do not duplicate
  the dispatcher rules there.

It never carries an account, company, tenant or user id: the connection binds the account. It changes
with `MCP_CONFIRM_BEFORE_OPTIMIZE`, `MCP_IMPORT_TOOLS_ENABLED` and `MCP_MAP_SHARES_ENABLED`, so it only
names tools the server publishes. `MCP_CATALOG_WRITE_TOOLS_ENABLED` adds the master-data line.

Annotations per tool are in [tools-reference.md](tools-reference.md). Two choices worth explaining:
`optimize_delivery_routes`, `optimize_plan`, `manage_vehicle` and `manage_depot` say
`destructiveHint: true` — a run in a full plan library replaces the oldest plan (`plan_replaced`), and
an update overwrites what the account had saved — so a host that asks before destructive calls asks
for these.

The two import tools are the only ones that say `idempotentHint: false`: every call starts a new import
(and a new plan), so a client must not retry them on its own. `optimize_delivery_routes` is idempotent because identical arguments (including the resolved delivery
date) map to the same optimization for the connected account. Retrying never creates a second job or a
second charge.

Large files (hundreds+ stops): use `import_delivery_file` → `get_import_result` → `optimize_plan`
instead of pasting `stops[]`. See [large-payloads.md](large-payloads.md).

## Plans

Every optimization lives in a plan: the same `OptimizationPlan` the dashboard lists under its plans
([architecture-mcp-plans.md](architecture-mcp-plans.md)). An import loads its stops into a plan; an
inline `optimize_delivery_routes` call creates a new one (`plan_name`, default "Optimization
YYYY-MM-DD"); `optimize_plan` runs an existing one. Runs, imports and results report `plan_id` and
`account_url` (the plan in the user's account, sign-in required).

**One billing rule for dashboard and MCP.** A plan's charged run opens a 24 h window. Within it, one
rerun of that plan is free when every stop is in the charged run (same stops or fewer, compared by id
and coordinates); depot, fleet and settings may change. Channel plans get 0. A free retry waives the
quota only: plan limits still apply. The views say what the next run costs:
`next_optimize_charged`, `free_retries_allowed`, `free_retries_remaining`, `free_retry_window_ends_at`
and, when charged, `charged_because` (`no_billed_run`, `stops_changed`, `allowance_used`,
`window_closed`, `no_allowance`). A run reports `quota_charged`, `free_retry: true` when it was the free
retry, and `free_retries_remaining` (retries that stay free after it completes). Because an inline call
always creates a new plan, the free retry is reachable only through `optimize_plan`, so `optimize_plan`
and `list_plans` are published on every server and the instructions state the rule everywhere.

**Library.** Free keeps 3 plans; kept + favorite plans are capped at library size − 1. When the library
is full, a new plan replaces the oldest plan that is not kept or favorite and the response carries
`plan_replaced: {plan_id, name}` (Core sends `{id, displayName}`); the agent must tell the user which
plan was replaced. `plan_temporary: true` means every slot was protected: the plan lives outside the
library and retention drops it, which the agent also tells the user.

**Delivering results.** The user chooses: open the plan in their account (`account_url`, login) or a
temporary public link (`create_optimization_map`, 48 h, anyone with the link). The agent offers both,
never creates the public link by default, and says the link is visible to anyone who has it.

## `list_plans`

Read-only, no stops charged, published on every server. Without `plan_id`: the account's plans
(favorites first, then newest; `query` filters by name, `limit` 1–50) and `library`
(`plans_in_library`, `max_plans`, `kept_plans`, `max_kept_plans`; null = unlimited). With `plan_id`:
one plan with its stop counts (`with_weight`, `with_volume`, `with_time_window`,
`stops_not_optimizable`), `total_weight_kg` / `total_volume_m3`, `depot` (`name`, `latitude`, `longitude`), `optimizing`, the free-retry
fields and `last_agent_run` (depot, vehicles, schedule, constraints an agent last ran it with, or
absent), so a new chat can repeat or vary the last run after confirming it. Never returns stops.
`PLAN_NOT_FOUND` when the plan is gone (deleted, or replaced to make room).

## `import_delivery_file` / `import_delivery_text`

Three ways in, so every host has one: `file` for hosts that hand attachments to tools (ChatGPT
`fileParams`); `url` for the rest (Claude, Gemini, Cursor, a console): a public https link, or a Google
Drive, Sheets or Docs share link as the user copied it; and `import_delivery_text` for rows the user
pasted or the text of a CSV or JSON file when the host has neither (a few hundred rows). A link that
answers with a web page instead of the file is reported as not shared publicly, and the download has a
90 s total deadline. Returns `import_id` and
`plan_id` (null until the stops load). Optional `plan_id` loads the stops into that plan, replacing its
stops and keeping its depot, fleet and settings (`PLAN_NOT_FOUND` if it does not exist); without it a
new plan is named after the file. Poll `get_import_result` for `plan_id`, `account_url`, `dataset_id`,
`summary` (never all rows), `needs_confirmation`, the free-retry fields and `plan_replaced` /
`plan_temporary`. `update_import_mapping` corrects columns without re-upload: `{column: field}`, `null` to ignore a
column, or `{field, unit, format}` when the column is in another unit (lb, in, l) or date/number format;
Smart Import converts it, and a unit or format it does not accept answers `INVALID_INPUT`. `list_datasets` lists
imports with their `plan_id`, `plan_replaced` / `plan_temporary` (only when true) and `last_run`. A
`dataset_id` expires after 24 h; its plan stays. `import_delivery_file` also returns `account_url` once
the plan is known.

## `optimize_plan`

Published on every server. Runs stored stops: exactly one of `plan_id` (the plan's stops) or
`dataset_id` (an import's copy, run in the plan it loaded; named in the description only when the
import tools are published). Takes the same run parameters as before (`depot` with coordinates, or an address with optional `city` / `country`,
`vehicles[]`, `exclude_stop_ids`, `use_weight` / `use_volume` / `use_time_windows`, `route_start_time`,
`time_zone`, `service_time_minutes`, `max_route_minutes`, `date`, `confirmed`, `idempotency_key`) plus
`depot_name`. A depot given as an address is geocoded and comes back as `depot_resolved`
(`matched_address`, `latitude`, `longitude`) in the preflight and in the run, for the agent to tell the
user. A match that is not in the `valid` band is refused with `INVALID_INPUT` and `depot_resolved` in its
details, before anything is optimized or charged: after the user's yes the agent calls again with those
coordinates. Logs record only the match band, never the address or coordinates. The run's depot, fleet
and schedule are written to the plan. `exclude_stop_ids` applies to
that run only: the plan keeps every stop (the launch freezes the stops that ran). Output is the same as
`optimize_delivery_routes`, with `plan_id`, `plan_name`, `account_url` and the billing fields above.

Idempotency: Core deduplicates the request as sent, before it expands the plan's stops. For `plan_id`
the adapter reads `GET /plans/{plan_id}` and adds the plan's `revision` to the key (it bumps whenever the
plan's stops or settings change, including when a run starts), so a retry of the same call is
deduplicated while the same arguments after the plan changed start a new run. A second call while the plan
runs gets `PLAN_BUSY` (retryable).

With `confirmed: false`, the preflight reads `GET /plans/{plan_id}` (or `GET /datasets/{dataset_id}`)
and states the real stop count (minus `exclude_stop_ids`), `charges_stops` (0 when
`next_optimize_charged` is false), `total_weight_kg` / `total_volume_m3` for an enforced capacity when
nothing is excluded, `plan_id`, `charged_because`, `free_retry_window_ends_at`, the plan
check, and warnings for runs Core would reject: capacity enforced while some stops lack that value
(`stops_without_weight` / `stops_without_volume`), time windows without `route_start_time`, an import
that still needs confirmation, and for plans `plan_has_no_stops`, `plan_stops_not_optimizable` and
`plan_optimizing`. `confirm_with` says whether the run is the free retry or why it is charged.

Every optimization keeps a run record (depot, vehicles, schedule, objective, constraints, dataset).
`list_plans` with `plan_id` shows it as `last_agent_run`, `list_datasets` as each import's `last_run`,
and `get_optimization_result` returns it as `request`, so a new chat can repeat or vary a run after
confirming the depot and departure with the user, instead of asking for everything again. When the
account fleet cannot cover the stops within `max_stops`, the instructions tell the agent to offer
increasing the vehicle count to cover the demand, not a "test" fleet.

## `list_fleet`

What the account has saved: vehicles and fleets, with capacity in kg and m³, shaped to drop into
`optimize_delivery_routes` as `vehicles[]`, and **depots** (`depot_id`, `name`, `latitude`,
`longitude`) to pass as `depot`. No arguments, read-only, charges no stops. `empty: true` means the
account has no vehicles saved — then ask the user to describe them. Depots are RouteHub milestones
tagged `DEPOT` (all of them when the account tagged none, as the dashboard does).

Catalog ids are reshaped to the `vehicle_id` pattern optimize accepts (a RouteHub UUID is longer
than the 32-character limit) and kept unique within each fleet, so the output can be passed through
unchanged. Requires `GET /api/mcp/v1/catalog` in Core; a Core from before depots simply sends none.

Use it before planning a real delivery day: a fleet invented in conversation produces a geometric
plan that ignores what each vehicle carries, and route ids nobody in the operation recognises.

## `manage_vehicle` / `manage_depot`

Published with `MCP_CATALOG_WRITE_TOOLS_ENABLED=true`. They save the account's **master data**; they
never touch a plan. Neither spends stops. There is no delete, and no tool for fleets or drivers: which
vehicles form a fleet is arranged in the dashboard.

### Master data and plan settings

| | Master data | Plan settings |
|---|---|---|
| What | saved vehicles (a type and its capacity), saved depots | how many vehicles a run uses (`count`), stops per vehicle, a capacity or a depot for one day, a vehicle that is out tomorrow |
| Lives | in the account, after the conversation | in one optimization |
| Tool | `manage_vehicle`, `manage_depot` | `vehicles[]` / `depot` of `optimize_delivery_routes` or `optimize_plan` |

| The user says | What happens |
|---|---|
| "Add a 1,500 kg Sprinter." | `manage_vehicle` create, after a yes |
| "Add 3 Sprinters of 1,500 kg." | **one** create: a vehicle is a type; the 3 is `count` on each plan |
| "Use 25 vehicles tomorrow." | nothing is saved: `count: 25` on the run |
| "Van 4 now carries 12 m³." | `list_fleet` → `manage_vehicle` update |
| "The Sprinter is out tomorrow." | nothing is saved: it is left out of that run |
| "Use the Barracas depot." | `list_fleet` depots → its coordinates as `depot`; nothing is saved |
| "Save a depot at San Martín 700." | `geocode_addresses` → the user confirms the match → `manage_depot` create |

The shape defends the line: one vehicle per call, and no `count`, `available` or list exists, so
"use 25 vehicles" cannot become 25 saved vehicles — an unknown field is `INVALID_INPUT`.
`manage_depot` takes coordinates, never an address, for the same reason optimize does.

`action: "create"` takes `vehicle` (`name`, `max_weight_kg`, `max_volume_m3`) or `depot` (`name`,
`latitude`, `longitude`); `action: "update"` takes the id from `list_fleet` and `changes`, and only
what is sent changes (a depot moves with both coordinates or neither).

A create **converges by name**, because RouteHub has neither unique names nor idempotency. Names are
compared normalized (case, accents, `-_/.`). The same name with the same values answers the one that
exists with `outcome: "already_existed"` and writes nothing, so a retry is safe; the same name with
other values is `NAME_TAKEN` with `details.existing`, and the agent asks: update it, or another name.
The plan's catalog caps answer `PLAN_UPGRADE_REQUIRED` (`CATALOG_VEHICLE_LIMIT`,
`CATALOG_DEPOT_LIMIT`) before anything is written; the run can still use the vehicle or depot
without saving it. Requires the `/api/mcp/v1/catalog/*` routes in Core.

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

## `list_automations` / `create_automation`

An automation is a standing rule: it looks at the orders waiting, and when there are enough it works
out the routes — every weekday at 08:00, or as soon as a shop has thirty orders. `list_automations`
reads the account's rules (when each one looks, what it takes, whether it is on, what it last
decided) and the stores it could be fed from; `create_automation` prepares one.

**Nothing here switches a rule on.** `create_automation` always writes it with `enabled: false`, and
answers with `account_url`: only its owner turns it on, in the dashboard, because a rule that is on
spends their stops unattended, on a schedule nobody is watching. An agent that reports a rule as
running is wrong, and the tool's own description says so, because some hosts never show the server
instructions.

The rule keeps **a plan of its own**, copied from `template_plan_id`: the person is never asked to
pick a container for their deliveries, and editing or deleting the plan they copied does not change
the rule. That plan's id is deliberately absent from the answer — an id in an agent's hands is an id
it could pass to `optimize_plan`, spending the rule's stops by hand and moving the revision its next
batch checks against.

`operation_id` is the caller's own id for the attempt: the same one twice returns the rule already
written, never a second one. `missing` says what a run would still lack (today: `depot`, when the
copied plan's depot is not one from the account's catalog, which a run refuses).

Requires `GET`/`POST /api/mcp/v1/automations` in Core; until Core serves them, both tools return a
not-found error and the user is pointed at the dashboard.

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
| `vehicles[].min_stops` | integer | no (50% of `max_stops`) | Floor per route. The engine lowers it to floor(max×0.9) when higher, and lowers the fleet's minimums when they add up to more than 95% of the stops; the preflight warns (`stop_band_margin`, `fleet_min_above_stops`). |
| `vehicles[].max_stops` | integer | no | Maximum stops per vehicle route. |
| `vehicles[].max_weight_kg` | number > 0 | no | Setting it on any vehicle **enforces** weight capacity unless `use_weight=false`. Every vehicle and stop must then carry weight. |
| `max_load_ratio` | number 0.5–1 | no (0.95) | Highest share of each vehicle's weight and volume capacity to fill. The 5% margin is the default in every channel; 1 fills vehicles completely. The preflight warns `load_above_margin` when the load only fits without the margin. |
| `use_weight` / `use_volume` / `use_time_windows` | boolean | no (follow the data) | The flag decides what the engine applies. `false` keeps weights, volumes or windows for reference without optimizing by them; `true` needs the matching vehicle capacity. |
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
| `plan_name` | string 1–200 | no ("Optimization YYYY-MM-DD") | Name of the new plan the run is saved as. |
| `depot_name` | string 1–120 | no ("Depot") | Depot name shown in the plan. |

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
  "plan_id": "cmf8x2…",
  "plan_name": "Optimization 2026-09-14",
  "account_url": "https://vepathos.com/dashboard?tab=plans&plan=cmf8x2…",
  "plan_replaced": { "plan_id": "cmf1a9…", "name": "Lunes" },
  "schedule_date": "2026-09-14",
  "expires_at": "2026-09-15T14:02:11Z",
  "stops_remaining_this_period": 146800,
  "quota_charged": true,
  "free_retries_remaining": 1,
  "progress": { "percent": 35, "stage": "assigning_stops" },
  "poll_after_seconds": 10
}
```

`plan_replaced` is present only when the library was full, `plan_temporary: true` only when every slot
was protected. `expires_at` is when an unfinished run is dropped; completed runs stay with the plan.

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
`progress` with `poll_after_seconds` (and `plan_id`, `account_url` for plan runs). A completed plan run is
read from the plan's history (`history_id`): it has no `expires_at` and never answers
`OPTIMIZATION_EXPIRED`.

### `detail=summary`

```json
{
  "optimization_id": "mcp_3f1c…",
  "status": "completed",
  "plan_id": "cmf8x2…",
  "history_id": "cmf9q1…",
  "account_url": "https://vepathos.com/dashboard?tab=plans&plan=cmf8x2…&history=cmf9q1…",
  "detail": "summary",
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

## `create_optimization_map`

Published with `MCP_MAP_SHARES_ENABLED=true`. Creates a temporary public link (48 h, `access:
anyone_with_link`) to a completed optimization's map; for plan runs it is the history run's share, the
same link the dashboard gives. The description tells the agent that results reach the user two ways,
the plan in their account (`account_url`, login) or this link, to offer both and create the link only
when the user picks it, and to say that anyone with the link can see the delivery locations until it
expires. Repeating the call returns the same link without extending it.

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
| `OPTIMIZATION_NOT_FOUND` / `OPTIMIZATION_EXPIRED` | Unknown id, or an unfinished or pre-plan run past retention (24 h). Completed plan runs never expire. | no |
| `PLAN_NOT_FOUND` | No such plan in the connected account (deleted, or replaced to make room). | no |
| `NAME_TAKEN` | A saved vehicle or depot already has this name with other values; `details.existing` is the one that has it. | no |
| `VEHICLE_NOT_FOUND` / `DEPOT_NOT_FOUND` | No saved vehicle or depot with this id in the connected account. | no |
| `PLAN_BUSY` | The plan is optimizing; wait for that run (`retry_after_seconds`). | yes |
| `IMPORT_NOT_FOUND` / `DATASET_NOT_FOUND` | Unknown or expired import (24 h); its plan stays. | no |
| `OPTIMIZATION_FAILED` | The optimization ended without a result; it was not charged. | yes |
| `BACKEND_UNAVAILABLE` / `TIMEOUT` | Temporary; identical requests are safe to resend. | yes |
| `INTERNAL_ERROR` | Unexpected error. | no |

## Help, prompts and the reference resource

Where each kind of help lives, so no text is written twice:

| Question | Answered by | Sent in every conversation |
|---|---|---|
| What does this tool do, and when not to use it? | the tool description and each field's description | yes |
| How do the tools chain? | `server_instructions()` | yes (some hosts cut it) |
| "Start this flow" / "what can I do here?" | an MCP prompt (`src/vepathos_mcp/prompts.py`) | no |
| The full reference | the resource `vepathos://docs/reference`, and [tools-reference.md](tools-reference.md) | no |
| At a terminal, or operating the server | `vepathos-mcp tools`, `workflows`, `doctor` | no |

There is no `help` tool: it would cost context in every conversation and teach the model nothing its
tool list does not already say. A user asks in the chat, or picks a prompt in hosts that show them
(Claude Code, Claude Desktop): `vepathos_help` (plain language, no tool names), `vepathos_tools`
(technical names), `plan_deliveries`, `rerun_plan`. Prompts name no tool, so they never name one a
server does not publish. The resource is rendered from the server's own registry and lists only what
that deployment publishes. Nothing a tool needs lives only in a prompt or the resource: hosts differ
in whether they show them.

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

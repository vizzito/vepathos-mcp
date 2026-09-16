"""Model-facing text. Descriptions are part of the product: precise, factual, no promotion.

Two audiences, no overlap:

- server_instructions(): the order of work and when to ask the user. Read once per session, and some
  clients truncate or never show it, so nothing here may be the only place a rule lives.
- Tool descriptions: what one tool does and when to call it. These always reach the model, so every
  rule that governs a single tool belongs in that tool's description.

Both are sent in every conversation, so they are written to be short.

Text about confirming a charge follows `MCP_CONFIRM_BEFORE_OPTIMIZE`, so it is built per server rather
than fixed: a client told its first call is free, when the server optimizes and charges on it,
reports a real charged plan as a preview.

Keep them consistent with the Core capability matrix (docs/tools.md).
"""

_INSTRUCTIONS_HEAD = (
    "Vepathos solves vehicle routing problems (VRP) for delivery fleets: it assigns stops to "
    "vehicles and sequences each route from one depot, from dozens to thousands of stops. Units: "
    "kilograms, cubic meters, kilometers, minutes, local HH:MM times.\n"
    "Order of work:\n"
)
# Only on a server that publishes the import tools (MCP_IMPORT_TOOLS_ENABLED): instructions never name
# a tool the client cannot see.
_STEP_IMPORT = (
    "Large files (hundreds+ stops): call import_delivery_file with the attachment (never paste "
    "rows), then get_import_result until dataset_id is ready, then optimize_dataset. "
    "optimize_delivery_routes with stops[] is only for small plans."
)
_STEPS = (
    "Street addresses: call geocode_addresses first and confirm the pins it flags "
    "(matched_address). Never invent coordinates.",
    "Planning a real delivery day: call list_fleet and use the account's own vehicles. Never "
    "invent vehicle_id. Invent a fleet only for what-if questions, and say so.",
    "Always ask for route_start_time (depot departure). Never invent 08:00. Never reuse stops "
    "from an earlier plan. Never use a depot the user did not give or geocode_addresses did not return.",
    "Before a large or first optimization: call get_account and compare the stop count with the "
    "plan's maximum.",
)
_INSTRUCTIONS_TAIL = (
    "Ask the user only what the tools cannot answer: the depot when unknown; which fleet when the "
    "account fleet does not match; whether weights/volumes are real when capacity matters. Ask one "
    "question at the moment it matters. Offer weight, volume or time-window constraints only when "
    "get_account lists that feature. Do not send min_stops near max_stops unless the user asks. "
    "For everything else choose a sensible default and state it.\n"
    "Every call runs on the Vepathos account the connector is signed in to. When a request is "
    "rejected for the plan, quota or authorization, tell the user which account is connected."
)


def server_instructions(*, confirm_before_optimize: bool, import_tools: bool) -> str:
    steps = ([_STEP_IMPORT] if import_tools else []) + list(_STEPS)
    optimizers = "optimize_dataset or optimize_delivery_routes" if import_tools else "optimize_delivery_routes"
    if confirm_before_optimize:
        steps.append(
            f"Then optimize ({optimizers}) with confirmed=false, show the preflight, get a yes, call "
            "again with confirmed=true, and get_optimization_result while it runs."
        )
    else:
        replan = " (a dataset's first run is charged; later variants of it are free replans)" if import_tools else ""
        steps.append(
            f"Then say how many stops it charges and how many remain{replan}, get a yes, call "
            f"{optimizers} once, and get_optimization_result while it runs."
        )
    numbered = "".join(f"{number}. {step}\n" for number, step in enumerate(steps, start=1))
    return _INSTRUCTIONS_HEAD + numbered + _INSTRUCTIONS_TAIL


OPTIMIZE_TITLE = "Optimize delivery routes"
_OPTIMIZE_INTRO = (
    "Plan optimized delivery routes for a fleet (vehicle routing problem, VRP). Assigns each stop to a vehicle "
    "and sequences every route from one depot, minimizing total distance while respecting the constraints you "
    "provide: maximum stops per vehicle, weight capacity (kg), volume capacity (m3) and delivery time windows. "
    "Built for large problems, from dozens to thousands of stops. Every stop needs latitude and longitude; "
    "addresses are not geocoded. Runs asynchronously: returns an optimization_id, plus the result when the "
    "optimization finishes within a few seconds; use get_optimization_result to retrieve status and routes. "
    "Results stay available for 24 hours. "
)
_OPTIMIZE_CHARGE_CONFIRMED = (
    "Charges one plan stop per stop sent, so it takes two calls: "
    "with confirmed=false (the default) nothing runs and nothing is charged, and the preflight it returns "
    "is what to show the user — stops and stops remaining, totals, the vehicles, the constraints this "
    "enforces, and whether the plan accepts it. Add what the preflight cannot know: which columns of their "
    "data are not being sent, and that arrival times are absent unless schedule.route_start_time is set. "
    "Then call again with identical arguments and confirmed=true. "
)
_OPTIMIZE_CHARGE_DIRECT = (
    "Every call runs and charges one plan stop per stop sent, so confirm first: tell the user how many "
    "stops it charges and how many remain, the vehicles and the constraints it enforces, which columns of "
    "their data are not being sent, and that arrival times are absent unless schedule.route_start_time is "
    "set. Then call once. "
)
_OPTIMIZE_TAIL = (
    "Identical arguments are deduplicated and "
    "charged once, but any change, including a different max_stops or vehicle count on the same stops, is a "
    "new optimization and charges again. A request that exceeds the plan, or needs a constraint it lacks, is "
    "rejected with an explanation and never partially applied."
)


def optimize_description(*, confirm_before_optimize: bool) -> str:
    charge = _OPTIMIZE_CHARGE_CONFIRMED if confirm_before_optimize else _OPTIMIZE_CHARGE_DIRECT
    return _OPTIMIZE_INTRO + charge + _OPTIMIZE_TAIL


LIST_FLEET_TITLE = "List fleet"
LIST_FLEET_DESCRIPTION = (
    "List the vehicles and fleets the connected Vepathos account already has: capacity in kilograms "
    "and cubic meters, units per vehicle, and the ids to pass as vehicles[] to "
    "optimize_delivery_routes. Takes no arguments; read-only and it does not consume plan stops. "
    "empty=true means the account has no fleet loaded, so ask the user to describe the vehicles or "
    "let them add the fleet in the Vepathos dashboard. A vehicle with no capacity means the account "
    "never set one, not that it carries nothing."
)

GET_ACCOUNT_TITLE = "Get connected account"
GET_ACCOUNT_DESCRIPTION = (
    "Show which Vepathos account this connection uses and what its plan allows: account label "
    "(company name, or a masked email), plan name, maximum stops per optimization, fleet and route "
    "limits, the constraints the plan includes, and the stops used and remaining in the current "
    "billing period. Takes no arguments: the account comes from the connection itself. Read-only "
    "and it does not consume plan stops. Use it to answer which account or plan is connected, to "
    "check limits before a large optimization, and to diagnose a rejection for the plan, the quota "
    "or authorization. A client is often connected to a different account than the user expects, in "
    "which case the fix is to reconnect as the right user, not to change the plan: connecting signs "
    "the user in to Vepathos and creates a free account when they have none, and switching accounts "
    "needs the old grant revoked first in Connected apps in the Vepathos dashboard."
)

GET_RESULT_TITLE = "Get optimization result"
GEOCODE_TITLE = "Geocode addresses"
GEOCODE_DESCRIPTION = (
    "Turn street addresses into latitude/longitude using Vepathos Smart Import. Requires a depot "
    "lat/lng or a city. Each stop includes matched_address (what the gazetteer matched) — use it to "
    "catch bad pins. Charges Smart Import quota, not route stops. When needs_confirmation is true, "
    "tell the user which ids are missing or uncertain before optimizing. Do not invent coordinates."
)

GET_GEOCODE_TITLE = "Get geocode result"
GET_GEOCODE_DESCRIPTION = (
    "Get the status and coordinates of a geocode_addresses job. Read-only. When complete, check "
    "needs_confirmation, unresolved_stop_ids and review_stop_ids before optimizing. Does not consume "
    "route-stop quota."
)

GET_RESULT_DESCRIPTION = (
    "Get the status and outcome of a route optimization by its optimization_id. While it runs, "
    "returns status and progress. When complete, detail=summary "
    "returns totals and per-route metrics; detail=stops returns ordered stop_ids with arrival times "
    "(driver clock; duration_minutes also counts service). Read-only; does not consume plan stops."
)

IMPORT_FILE_TITLE = "Import delivery file"
IMPORT_FILE_DESCRIPTION = (
    "Upload a delivery file (Excel, CSV, JSON, text) via ChatGPT attachment "
    "(_meta openai/fileParams) or a public https url. Starts Smart Import; returns import_id. "
    "Never paste thousands of stops into optimize_delivery_routes. Poll get_import_result."
)

IMPORT_TEXT_TITLE = "Import pasted deliveries"
IMPORT_TEXT_DESCRIPTION = (
    "Import a short pasted delivery list through the same Smart Import pipeline as a file. "
    "Returns import_id; use get_import_result. Prefer import_delivery_file for large files."
)

GET_IMPORT_TITLE = "Get import result"
GET_IMPORT_DESCRIPTION = (
    "Status and summary of an import_delivery_file / import_delivery_text job. When complete: "
    "dataset_id, expires_at, summary (counts, mapping, sample, needs_confirmation). Never returns "
    "all rows. Then call optimize_dataset."
)

UPDATE_MAPPING_TITLE = "Update import mapping"
UPDATE_MAPPING_DESCRIPTION = (
    "Correct Smart Import column mapping as {source_column: vepathos_field} without re-uploading. "
    "Returns the updated summary."
)

OPTIMIZE_DATASET_TITLE = "Optimize imported dataset"
def optimize_dataset_description(*, confirm_before_optimize: bool) -> str:
    charge = (
        "With confirmed=false returns a preflight and charges nothing; then confirmed=true. "
        if confirm_before_optimize
        else "Confirm the charge (or free replan) with the user, then call once. "
    )
    return (
        "Optimize a previously imported dataset by dataset_id (from get_import_result). Pass depot, "
        "vehicles and per-run options (exclude_stop_ids, use_weight/volume/time_windows, "
        "service_time, max_route_minutes, min_stops/max_stops). The first run of a dataset charges "
        "its stops; after it, up to 5 variants are free replans, which must still fit the plan. "
        "first_optimize_charged and free_replans_remaining (get_import_result, list_datasets) say "
        "which applies. " + charge + "Use get_optimization_result while it runs."
    )


LIST_DATASETS_TITLE = "List datasets"
LIST_DATASETS_DESCRIPTION = (
    "List delivery datasets available to optimize_dataset: imports from this chat and (when Core "
    "exposes them) the web app. Read-only."
)

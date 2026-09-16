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
    "1. Street addresses: call geocode_addresses first and confirm the pins it flags. Never invent "
    "coordinates.\n"
    "2. Planning a real delivery day: call list_fleet and use the account's own vehicles and "
    "capacities. Invent a fleet only for what-if questions, and say that is what it is.\n"
    "3. Before a large or first optimization: call get_account and compare the stop count with the "
    "plan's maximum. Say the limit and offer options before geocoding or optimizing, not after a "
    "rejection.\n"
)
_STEP_4_CONFIRMED = (
    "4. Then optimize_delivery_routes with confirmed=false, show the preflight it returns and get a "
    "yes, call it again with confirmed=true, and get_optimization_result while it runs.\n"
)
_STEP_4_DIRECT = (
    "4. Then say how many stops the optimization charges and how many remain, get a yes, call "
    "optimize_delivery_routes once, and get_optimization_result while it runs.\n"
)
_INSTRUCTIONS_TAIL = (
    "Ask the user only what the tools cannot answer and what changes the plan: the depot, when it "
    "is not known; which fleet to use, when the account fleet does not match what they asked for; "
    "whether the weights or volumes in their data are the real load, when capacity matters. Ask one "
    "question at the moment it matters, not a list up front. Offer weight, volume or time-window "
    "constraints only when get_account lists that feature for the plan; when the data has them and "
    "the plan does not, say so rather than proposing it. For everything else choose a sensible "
    "default and state it (service time, objective) instead of asking.\n"
    "Every call runs on the Vepathos account the connector is signed in to, and that account's plan "
    "sets every limit. When a request is rejected for the plan, the quota or authorization, tell "
    "the user which account is connected: the usual cause is a different account than they mean, "
    "not a limit that has to be raised."
)


def server_instructions(*, confirm_before_optimize: bool) -> str:
    step_4 = _STEP_4_CONFIRMED if confirm_before_optimize else _STEP_4_DIRECT
    return _INSTRUCTIONS_HEAD + step_4 + _INSTRUCTIONS_TAIL


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
    "Turn street addresses into latitude/longitude using Vepathos Smart Import (the account's own "
    "geocoder, not a guessed coordinate). Requires a depot lat/lng or a city so the map region is known. "
    "Charges the Smart Import address quota, not route stops. When complete, unresolved_stop_ids has "
    "rows with no pin; review_stop_ids has pins that are band=review or confidence below 0.8. If "
    "needs_confirmation is true, tell the user which ids are missing or uncertain and wait for "
    "confirmation before optimize_delivery_routes. Do not invent coordinates. Returns a geocode_id; "
    "if the job is still running, use get_geocode_result."
)

GET_GEOCODE_TITLE = "Get geocode result"
GET_GEOCODE_DESCRIPTION = (
    "Get the status and coordinates of a geocode_addresses job. Read-only. When complete, check "
    "needs_confirmation, unresolved_stop_ids and review_stop_ids before optimizing. Does not consume "
    "route-stop quota."
)

GET_RESULT_DESCRIPTION = (
    "Get the status and outcome of a route optimization started with optimize_delivery_routes. While it runs, "
    "returns status and progress (waiting briefly for completion). When complete, detail=summary returns totals "
    "(stops assigned and unassigned, vehicles used, distance, duration, time-window compliance) and a page of "
    "per-route metrics; detail=stops returns the ordered stop_ids with estimated arrival times for one route "
    "(route_id) or all routes page by page; detail=unassigned lists stops that could not be routed. Read-only; "
    "it does not consume plan stops."
)

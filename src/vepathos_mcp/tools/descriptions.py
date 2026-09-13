"""Model-facing text. Descriptions are part of the product: precise, factual, no promotion.

Keep them consistent with the Core capability matrix (docs/tools.md).
"""

SERVER_INSTRUCTIONS = (
    "Vepathos solves vehicle routing problems (VRP) for delivery fleets: it assigns stops to vehicles and "
    "sequences each route from one depot, at scales from dozens to thousands of stops. "
    "optimize_delivery_routes requires latitude/longitude. If the user has street addresses, call "
    "geocode_addresses first (Vepathos Smart Import); never invent coordinates. After geocoding, if "
    "needs_confirmation is true (unresolved_stop_ids or review_stop_ids), list those stop ids and wait "
    "for the user: do not call optimize_delivery_routes until they confirm continuing with the pins "
    "they accept, or they send corrected addresses. Never invent pins for unresolved stops. Review "
    "means band=review or confidence below 0.8. Then pass only confirmed pins to "
    "optimize_delivery_routes. Use get_geocode_result / get_optimization_result while a job runs. "
    "Units: kilograms, cubic meters, kilometers, minutes, local HH:MM times."
)

OPTIMIZE_TITLE = "Optimize delivery routes"
OPTIMIZE_DESCRIPTION = (
    "Plan optimized delivery routes for a fleet (vehicle routing problem, VRP). Assigns each stop to a vehicle "
    "and sequences every route from one depot, minimizing total distance while respecting the constraints you "
    "provide: maximum stops per vehicle, weight capacity (kg), volume capacity (m3) and delivery time windows. "
    "Built for large problems, from dozens to thousands of stops. Every stop needs latitude and longitude; "
    "addresses are not geocoded. Runs asynchronously: returns an optimization_id, plus the result when the "
    "optimization finishes within a few seconds; use get_optimization_result to retrieve status and routes. "
    "Results stay available for 24 hours. Uses stops from the connected Vepathos account plan; a request that "
    "exceeds the plan, or needs a constraint the plan does not include, is rejected with an explanation and is "
    "never partially applied. Calling again with identical arguments returns the same optimization."
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

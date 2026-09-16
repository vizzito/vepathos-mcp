# Defaults across channels (T33)

Single place to look up how each channel builds the optimizer engine request. Change here and in the
linked sources together.

## Two request builders

| Channel | Path to the engine | Builder |
|---|---|---|
| Web (vepathos-router-client) | router-client → routehub-fastapi `POST /lots/{id}/plan` → engine | `libs/plan_build.py`, `libs/lot_plan_adapter.py`, `config/engine_defaults.json` |
| Shopify planner (vepathos-planner) | planner → api-doc `/api/shopify/v1/optimization/jobs` → engine | api-doc `src/server/rapidapi/transformer.ts` (`toEnginePayload`, same schema as RapidAPI) |
| RapidAPI | caller → api-doc `/api/rapidapi/v1/optimization/jobs` → engine | same `toEnginePayload` |
| MCP | agent → vepathos-mcp → api-doc `/api/mcp/v1/optimization/jobs` → engine | `validate.ts` `toRapidInput` → same `toEnginePayload` |

The two builders share no code: every value computed from the job is duplicated and must stay in sync.
Target: the engine computes derived values itself, and both builders stop sending them.

## Where each value comes from

`>` means "wins over". "Fixed" means no caller or profile can change it.

| Engine key | Web (routehub) | Shopify planner | RapidAPI | MCP |
|---|---|---|---|---|
| `start_time_minutes_route` | lot `schedule.start_at` **converted to UTC** > omitted | run `routeStartTime` > shop `runDefaults.routeStartTime` (08:00), **local** > 720 | `options.route_start_time` > 720 | `schedule.route_start_time` > 720 |
| `service_time_min` | lot `routing.service_time_min` > omitted | shop `runDefaults.serviceTimeMin` (3) > 2.2 | `options.service_time_min` > 2.2 | `schedule.service_time_minutes` > 2.2 |
| `early` / `late_tolerance_min`, `avg_speed_kph` | lot config > omitted | fixed 5 / 10 / 40 | fixed | fixed |
| `optimize_time_windows` | lot `schedule.respect_delivery_windows` | plan capability + windows + shop flag | `options` + plan capability | automatic when a stop has a window |
| `rebalance_by_size` | lot `rebalance` branch (a `rebalance` object without it turns size **off**) | plan capability (shop `bySize` is dropped) | plan capability | plan capability |
| `rebalance_by_volume` / `by_weight`, load ratios | lot `rebalance.*` > model 0.4–0.95 / 0.01–0.95 | shop flags, `runDefaults.loadWeight/loadVolume` (0–0.95) | `options.*` > 0–1 | not exposed (off, 0–1) |
| `rebalance_by_time` / `max_time_minutes_per_route` | lot `rebalance_by_time` > vehicle route time > 60–420 | not sent | `options.*` | `schedule.max_route_minutes` |
| `min_stops` / `max_stops` (`deliveries_qty`) | vehicle overrides > defaults > lot route stops > computed fallback | `VehiclePreset` (min 1 when omitted) | request (min 1) | request (min 1) |
| Stop margin warning | `STOP_MARGIN_RATIO = 0.8` in `lot_plan_adapter.py` | — | `validate.ts` | `validate.ts` + MCP `preflight_checks.py` |
| `force_vehicles_fleet_match` | lot `clustering` > env > `false` | fixed `true` | fixed `true` | fixed `true` |
| `min` / `max_size_cluster` | computed `ceil(stops / vehicle units)` / `floor(max / 2)`, ×1.15–1.25 when fleet is dynamic | computed, same base formula (`computeClusterSizes`). Until 2026-09-16: fixed 12 / 28 | same | same |
| `clustering_data_chunks` | `"auto"` (json) | integer from the engine's `calculate_auto_chunks`, `MAX_COMPLEXITY_PER_CHUNK = 20000`. Until 2026-09-16: not sent (one chunk) | same | same |
| `preprocessing_batch_size` | `max_size_cluster × 4` | `max_size_cluster × 4`, capped at 500. Until 2026-09-16: fixed 500 | same | same |
| `size_cluster_tolerance`, subclustering | env > json 0.15, volume subclustering on, 1.4 | fixed 0.2, volume subclustering only with volume rebalance, 1.3 | same | same |
| `nearby_threshold_m`, `distance_haversine_limit`, 2-opt budget | env > json 15, 15, 40 / 25 / 25 | fixed 50, 50, 50 / 25 / 45 | same | same |
| `preprocessing.max_distance_km` | `PLAN_PREPROCESSING_MAX_DISTANCE_KM` > env > json 500 | fixed 100 (the contract's `INVALID_COORDINATES` radius) | same | same |
| `hardware` parallelism | env > json 6 / 6 / 6 | engine defaults | same | same |
| `date` | UTC date of `start_at` > today | planned date > today (+offset) in shop time zone | `date` > today | `schedule.date` > today in `time_zone` |

Web values can be overridden on the routehub host by `ENGINE_DEFAULTS_PATH`, `PLAN_CLUSTERING_JSON`,
`PLAN_ROUTING_JSON`, `PLAN_SETTINGS_JSON` and `PLAN_PREPROCESSING_MAX_DISTANCE_KM`. api-doc reads no
environment variable for engine tuning: its values are constants in `transformer.ts`.

## Stored profiles

| Profile | Stored in | Applied by |
|---|---|---|
| Per user routing defaults (start time, time zone, service, parking, tolerances, speed) | api-doc `UserPreference.savedConfigJson` (`/api/dashboard/preferences`) | router-client only, merged into the lot config on the client. **Not applied to RapidAPI, Shopify or MCP.** |
| Per lot | routehub `DeliveryLot.config_data` | routehub |
| Per shop | planner `Shop.settingsJson` (`runDefaults`, `rebalance`), `VehiclePreset` | planner, sent as request options |

## Why computed values matter

2026-09-16, local stack: an MCP job of 10,931 stops and 115 vans (`max_stops` 100) reached the engine
with fixed clusters 12/28 and one chunk. The engine capped the batch at 50 clusters, then pushed the
cluster size past the vans' capacity to satisfy KMeansConstrained; it timed out twice and the job
produced 0 clusters. Computed sizes (48/96) and 8 chunks keep every chunk at ~15 clusters. The engine
adjustment that overrides the vehicle cap is a separate engine bug.

## Open issues

1. **Start time encoding differs.** routehub sends `start_at` converted to UTC (08:00-03:00 → 660);
   api-doc sends local wall-clock minutes (08:05 → 485). At most one matches what the engine expects for
   arrival times and time windows.
2. routehub: a `rebalance` object without a `rebalance_by_size` branch turns size rebalance off.
3. Shopify planner: `rebalance.bySize` is dropped by `parseShopSettings`, so it is always on.
4. routehub: `PLAN_PREPROCESSING_MAX_DISTANCE_KM=none` cannot disable the distance filter (the model
   restores 500).
5. MCP: user routing defaults from the dashboard are not applied; weight capacity is sent without
   `rebalance_by_weight` (verify whether the engine still enforces weight).

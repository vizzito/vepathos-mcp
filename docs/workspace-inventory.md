# Workspace inventory — unpushed branches & dirty trees (T29 / T30)

Snapshot taken while closing the MCP roadmap (2026-09-16). Do **not** revive
`route-optimizer` commit `0560e95` (local main, May) into any PR to `main`.

| Repo | Branch | Dirty files | Notes |
|---|---|---|---|
| vepathos-mcp | develop (ahead of origin) | many (this work) | Also local `main` / `stage` at initial commit. |
| vepathos-api-doc | develop | MCP datasets / imports WIP | Stash `shopify wip` was reviewed and dropped (T2). |
| vepathos-planner | main | 0 | Clean. |
| route-optimizer-app | open-router-develop-optimizer | ~13 (env + scripts) | Several unpushed optimizer feature branches. |
| routehub-fastapi | lot_config_rebalance | ~17 | Includes `STOP_MARGIN_RATIO` warning on tight min/max. |
| routehub-openapi | master (behind origin) | 6 | Local package/yaml edits. |
| routehub-dbschem | schema-develop | 3 | Lookup indexes + schema dumps. |
| vepathos-smart-import | feat/smart-import-produccion | 1 (`.DS_Store`) | Local `develop` unpushed relative to this feature branch. |
| vepathos-router-client | develop-refactor | 1 | Claude worktree branches present. |

## T38 — mapping units (Smart Import)

Account-saved column mapping with weight/volume unit conversion lives in
`vepathos-smart-import` (readers + mapping persistence), not in the MCP adapter.
MCP already exposes `update_import_mapping` and surfaces `summary.units` as kg/m³;
persisting per-account unit preferences remains an SI follow-up.

## T33 — load / journey defaults

| Channel | Weight fill | Volume fill | `max_time_minutes_per_route` |
|---|---|---|---|
| Web (planner `shop-settings`) | 0–0.95 | 0–0.95 | via rebalance when configured |
| API / MCP | 1.0 (no fill derate) | 1.0 | only when `schedule.max_route_minutes` is set (`rebalance_by_time`) |

MCP publishes `max_route_minutes` and warns when `min_stops` > floor(`max_stops` × 0.8).

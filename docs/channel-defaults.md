# Defaults across channels (T33)

Single place to look up load fill and journey caps. Change here and in the linked
sources together.

| Default | Web (planner) | RapidAPI / MCP |
|---|---|---|
| Load weight ratio | 0.95 max (`shop-settings.ts`) | 1.0 (raw kg) |
| Load volume ratio | 0.95 max | 1.0 (raw m³ via cube sides) |
| `min_stops` | lot / fleet config | **1** when omitted |
| Stop margin warning | `STOP_MARGIN_RATIO = 0.8` in `lot_plan_adapter.py` | same constant in Core `validate.ts` + MCP `preflight_checks.py` |
| Journey cap | lot time max / rebalance | `schedule.max_route_minutes` → `rebalance_by_time` only for that job |

API/MCP never turn on `rebalance_by_time` unless the caller sets `max_route_minutes`.

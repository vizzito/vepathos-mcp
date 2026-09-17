# Large problems and payload limits

Vepathos's solver handles tens of thousands of stops. **The agent layer is the practical limit, not the
solver.**

## Measured on 16/09

| Size | Path | Result |
|---|---|---|
| ~1,005 stops | `optimize_delivery_routes` with inline `stops[]` | Worked (within model output budget). |
| ~8,200 stops | inline `stops[]` | Did **not** fit: the model cannot emit that many tool arguments. |

**Decision:** import by reference, then optimize by id. The stops land in a plan
([architecture-mcp-plans.md](architecture-mcp-plans.md)), so they outlive the 24 h dataset.

1. `import_delivery_file` (ChatGPT attachment / URL) or `import_delivery_text` (short paste), optionally
   into an existing `plan_id`.
2. Poll `get_import_result` until `status=completed` and `plan_id` is set.
3. Call `optimize_plan(plan_id, …)` (or `dataset_id` within 24 h) — Core expands the stored stops; the
   model never pastes rows.
4. Within 24 h of the plan's charged run, one rerun with the same stops or fewer is free (the rule the
   dashboard shares). `next_optimize_charged` and the preflight say whether the next run is charged.
   See [tools.md](tools.md#plans).

## Where the limits are

| Layer | Limit | Notes |
|---|---|---|
| Tool arguments written by a model | about 1–3k stops per call | Each stop costs about 30–40 output tokens as JSON. |
| MCP request body | 8 MiB (`MCP_MAX_REQUEST_BODY_BYTES`) | About 50k compact stops; the model limit is reached far earlier. |
| Core import upload | 8 MiB (`MCP_IMPORT_MAX_BYTES`) | Adapter downloads ChatGPT `fileParams` and re-POSTs as base64. |
| Dataset TTL | 24 h (`MCP_DATASET_TTL_SECONDS`) | The plan the stops loaded into stays (library rules). |
| Edge proxy | 8 MB request body | Kept aligned with the app limit. |
| Core channel | 10 MiB request body | Returns `PAYLOAD_TOO_LARGE` with sizes. |
| Engine | 25,000 stops per job (current API schema) | Plan limits (stops per request) usually bind first. |
| Tool results | Default pages: 25 routes / 200 stops | Import/get_import_result never return all rows — only a summary. |

## Guidance for agents

- Hundreds+ stops: **always** import → `optimize_plan`. Never paste rows into `optimize_delivery_routes`.
- Rerun or vary a large day by `plan_id` (`list_plans` shows `last_agent_run`), never by pasting it again.
- Small plans only: inline `stops[]` on `optimize_delivery_routes` is fine.
- Send only the fields needed on small plans (id, coordinates, weight/volume/window when relevant).
- Read results with `detail=summary` first; page through `detail=stops` only when needed.

## Next

The order-set design ([architecture-mcp-orderset.md](architecture-mcp-orderset.md)) was superseded by
plans ([architecture-mcp-plans.md](architecture-mcp-plans.md)): one import loads one plan; import into
the same `plan_id` to replace its stops.

## Metrics

`mcp_optimization_stops`, `mcp_tool_request_bytes`, `PAYLOAD_TOO_LARGE` counts, import job outcomes,
and free retries per plan (`billing.mode = plan_free_retry` in Core).

# Large problems and payload limits

Vepathos's solver handles tens of thousands of stops. **The agent layer is the practical limit, not the
solver.**

## Measured on 16/09

| Size | Path | Result |
|---|---|---|
| ~1,005 stops | `optimize_delivery_routes` with inline `stops[]` | Worked (within model output budget). |
| ~8,200 stops | inline `stops[]` | Did **not** fit: the model cannot emit that many tool arguments. |

**Decision:** import by reference, then optimize by id.

1. `import_delivery_file` (ChatGPT attachment / URL) or `import_delivery_text` (short paste).
2. Poll `get_import_result` until `dataset_id` + `status=completed`.
3. Call `optimize_dataset(dataset_id, …)` — Core expands the stored stops; the model never pastes rows.
4. Variants of the same `dataset_id` may use free replans (per plan: Free 1, Starter 1, Growth 2, Scale 3, Enterprise 5, same as the dashboard). Preflight /
   billing report how many remain.

## Where the limits are

| Layer | Limit | Notes |
|---|---|---|
| Tool arguments written by a model | about 1–3k stops per call | Each stop costs about 30–40 output tokens as JSON. |
| MCP request body | 8 MiB (`MCP_MAX_REQUEST_BODY_BYTES`) | About 50k compact stops; the model limit is reached far earlier. |
| Core import upload | 8 MiB (`MCP_IMPORT_MAX_BYTES`) | Adapter downloads ChatGPT `fileParams` and re-POSTs as base64. |
| Dataset TTL | 24 h (`MCP_DATASET_TTL_SECONDS`) | Same as job result TTL. |
| Edge proxy | 8 MB request body | Kept aligned with the app limit. |
| Core channel | 10 MiB request body | Returns `PAYLOAD_TOO_LARGE` with sizes. |
| Engine | 25,000 stops per job (current API schema) | Plan limits (stops per request) usually bind first. |
| Tool results | Default pages: 25 routes / 200 stops | Import/get_import_result never return all rows — only a summary. |

## Guidance for agents

- Hundreds+ stops: **always** import → `optimize_dataset`. Never paste rows into `optimize_delivery_routes`.
- Small plans only: inline `stops[]` on `optimize_delivery_routes` is fine.
- Send only the fields needed on small plans (id, coordinates, weight/volume/window when relevant).
- Read results with `detail=summary` first; page through `detail=stops` only when needed.

## Next (design only)

Multi-file edit-before-optimize is specified as an account-scoped **orderset** (working set) in
[architecture-mcp-orderset.md](architecture-mcp-orderset.md). Not implemented yet; until then use
one import → one `dataset_id` per optimize.

## Metrics

`mcp_optimization_stops`, `mcp_tool_request_bytes`, `PAYLOAD_TOO_LARGE` counts, import job outcomes,
and free-replan consumption per `dataset_id`.

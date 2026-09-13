# Large problems and payload limits

Vepathos's solver handles tens of thousands of stops. **The agent layer is the practical limit, not the
solver.**

## Where the limits are

| Layer | Limit | Notes |
|---|---|---|
| Tool arguments written by a model | about 1–3k stops per call | Each stop costs about 30–40 output tokens as JSON (`{"stop_id":…,"latitude":…,"longitude":…,"weight_kg":…}`). Model output budgets make larger inline arrays slow, costly and error-prone. |
| MCP request body | 8 MiB (`MCP_MAX_REQUEST_BODY_BYTES`) | About 50k compact stops; the model limit is reached far earlier. |
| Edge proxy | 8 MB request body | Kept aligned with the app limit. |
| Core channel | 10 MiB request body | Returns `PAYLOAD_TOO_LARGE` with sizes. |
| Engine | 25,000 stops per job (current API schema) | Plan limits (stops per request) usually bind first. |
| Tool results | Default pages: 25 routes / 200 stops | A 3,000-stop summary is about 2.5k tokens. Clients such as Claude Code also cap MCP output tokens. |

## Guidance for agents today

- Send stops with only the fields needed (id, coordinates, weight/volume/time window when relevant).
- Split very large problems by depot or zone into several optimizations.
- Read results with `detail=summary` first; page through `detail=stops` only when needed.

## Future options (only after measuring real usage)

1. **Dataset upload handle.** Core issues a short-lived signed upload URL, the agent (with shell or code
   execution) uploads a CSV/JSON, and `optimize_delivery_routes` accepts a `dataset_id`.
2. **Programmatic tool calling** from code-execution environments, which avoids generating arguments as
   tokens.
3. **Existing Vepathos plan reference**, to optimize stops already imported through the web app or
   Smart Import.

Metrics that decide: `mcp_optimization_stops`, `mcp_tool_request_bytes`, `PAYLOAD_TOO_LARGE` counts
and invalid-input rates by payload size.

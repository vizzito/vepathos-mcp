# Asynchronous model

Vehicle routing for thousands of stops takes seconds to minutes. `vepathos-mcp` never converts that
into a long synchronous HTTP call.

## Flow used by every client today

```
optimize_routes ──► Core creates job (idempotent) ──► optimization_id
        │  waits ≤ 8 s (configurable) and includes the result when it finishes quickly
        ▼
get_optimization_result(optimization_id)
        │  waits ≤ 20 s for completion, reporting real progress
        ▼
status + progress + poll_after_seconds      or      summary / stops / unassigned (paginated)
```

- **Bounded waits.** `MCP_OPTIMIZE_INLINE_WAIT_SECONDS` (default 8) and `MCP_RESULT_LONGPOLL_SECONDS`
  (default 20) keep every tool call under about 30 seconds, well below common client and proxy
  timeouts. While waiting, the server polls Core every `MCP_POLL_INTERVAL_SECONDS` and emits
  `notifications/progress` when the client supplied a progress token.
- **Explicit handles.** The state lives in Vepathos Core. `optimization_id` is an unguessable id bound
  to the account; possessing it grants nothing without the account's credential.
- **Idempotency.** The adapter derives an `Idempotency-Key` from the canonical arguments and resolved
  date. Core deduplicates per account, so client retries after timeouts return the same job.
- **Retention.** Results are available for 24 hours (`expires_at`).
- **No cancellation.** A submitted optimization runs to completion by product decision.

## Stateless transport

The server runs Streamable HTTP with `stateless_http=True`:

- 2026-07-28 clients send self-contained requests (`_meta`, `server/discover`).
- Earlier clients (`initialize` handshake) are served statelessly: no `Mcp-Session-Id`, so any
  replica can answer any request behind a round-robin load balancer.
- No legacy HTTP+SSE transport. SSE is only used as the per-request response stream (progress).

## MCP Tasks (future)

The `io.modelcontextprotocol/tasks` extension (2026-07-28) lets a server return a task handle from
`tools/call`. As of September 2026 neither the Python nor the TypeScript official SDK implements it,
and no mainstream client lists it. When support lands, it becomes a second representation of the same
Core job, not a second job system:

| Tasks | Vepathos |
|---|---|
| `CreateTaskResult.taskId` | `optimization_id` |
| `ttlMs` | remaining time until `expires_at` |
| `pollIntervalMs` | 5000 |
| `tasks/get` → `working` | Core status `queued`/`running`, `statusMessage` from progress |
| `tasks/get` → `completed.result` | the exact `get_optimization_result` summary result |
| `tasks/get` → `failed.error` | JSON-RPC error carrying the domain error |
| `tasks/cancel` | acknowledged with an empty result; the optimization continues (cooperative cancellation) |

Tasks are only returned to clients that declare the extension in the request `_meta`; the explicit
`optimization_id` flow keeps working for everyone else.

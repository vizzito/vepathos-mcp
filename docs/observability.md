# Observability

Two layers answer different questions:

- **Operational** (this service): is the MCP channel healthy, fast and used correctly? Prometheus
  metrics and structured logs.
- **Commercial** (Vepathos Core database): is MCP bringing and converting customers? SQL on Core data.

## Metrics (`/metrics`, Prometheus)

| Metric | Labels | Answers |
|---|---|---|
| `mcp_http_requests_total` | `mcp_method`, `protocol_version`, `client_type` | Traffic by client and protocol version (replaces "connections" in a stateless server). |
| `mcp_tool_calls_total` | `tool`, `outcome`, `error_code`, `client_type` | Optimize/result calls and errors (`sum by (tool)`). |
| `mcp_auth_failures_total` | `reason` | Authentication problems. |
| `mcp_plan_rejections_total` | `code`, `reason` | Upgrade, quota and concurrency rejections (commercial signal). |
| `mcp_backend_request_duration_seconds` | `operation` (`submit`, `status`, `result`), `status_class` | Core latency, including optimization submit latency. |
| `mcp_tool_duration_seconds` | `tool` | End-to-end tool latency. |
| `mcp_tool_request_bytes` / `mcp_tool_response_bytes` | `tool` | Payload and output sizes. |
| `mcp_tool_response_tokens_estimate` | `tool` | Approximate tokens returned to models. |
| `mcp_optimization_stops` | — | Problem sizes agents submit. |

Useful queries:

```promql
sum by (tool, outcome) (rate(mcp_tool_calls_total[5m]))
sum by (client_type) (increase(mcp_tool_calls_total{tool="optimize_delivery_routes"}[1d]))
histogram_quantile(0.95, sum by (le, operation) (rate(mcp_backend_request_duration_seconds_bucket[5m])))
sum by (reason) (increase(mcp_plan_rejections_total{code="PLAN_UPGRADE_REQUIRED"}[7d]))
```

`client_type` comes from the OAuth `client_id` (Client ID Metadata Document host) and falls back to
the client's self-reported name: `claude`, `claude-code`, `cursor`, `vscode`, `codex`, `chatgpt`,
`inspector` or `other`.

## Logs

JSON lines on stderr. Every tool call logs: `tool`, `outcome`, `error_code`, `optimization_id`,
`status`, `account_hash` (salted hash, never the account id or credential), `client_type`,
`latency_ms`, `request_bytes`, `response_tokens`. Arguments, coordinates, tokens and payloads are never
logged.

## Tracing

Incoming W3C `traceparent` headers are forwarded to Core with an `X-Request-Id`, so one operation can be
followed from the client through the MCP service to the Core channel logs. OpenTelemetry export can be
enabled later without changing the call sites.

## Commercial analytics (Core database)

Core records, without invasive tracking:

- `User.signupSource = 'mcp'`, `User.signupClient` (verified client label)
- MCP usage on the account's MCP ledger credential (`ApiClient.channel = 'mcp'`) and job rows
  (`StopTransaction.requestPayload.source = 'mcp'`)
- `User.firstMcpUseAt`, `User.firstMcpClient`
- `User.mcpFullTrialUsedAt`, `mcpFullTrialStops`, `mcpFullTrialTrigger`
- Stripe Checkout metadata `source = 'mcp'` for upgrades started from an MCP upgrade URL

Example questions:

```sql
-- Accounts acquired through MCP, per client, per week
SELECT date_trunc('week', "createdAt") AS week, "signupClient", count(*)
FROM "User" WHERE "signupSource" = 'mcp' GROUP BY 1, 2 ORDER BY 1;

-- MCP-acquired accounts that moved to a paid plan
SELECT count(*) FILTER (WHERE "planId" NOT IN ('free', 'free_plus')) AS paid, count(*) AS total
FROM "User" WHERE "signupSource" = 'mcp';

-- What premium value users look for when the trial triggers
SELECT "mcpFullTrialTrigger", count(*) FROM "User"
WHERE "mcpFullTrialUsedAt" IS NOT NULL GROUP BY 1;
```

## Health

- `GET /health`: liveness. The process is up. No dependencies are checked.
- `GET /ready`: readiness. It answers `503` only for local faults (no service key, or OAuth signing
  keys never loaded). A Core outage is reported as `{"dependencies": {"core": "degraded"}}` with `200`,
  so a backend blip does not remove every replica from rotation. Tools fail fast meanwhile.

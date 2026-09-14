# ADR-001 — Vepathos MCP architecture

- **Status:** Accepted (2026-09-13)
- **Scope:** `vepathos-mcp` (this repository) and the MCP channel in Vepathos Core (`vepathos-api-doc`)
- **Protocol baseline:** MCP specification `2026-07-28` (serves earlier handshake-based revisions too)

## Context

Vepathos is a last-mile route optimization platform (vehicle routing problem, VRP).
AI agents (Claude, ChatGPT/Codex, Cursor, VS Code and other MCP clients) should be able to
discover Vepathos, connect a Vepathos account and solve real delivery routing problems
without learning the Vepathos REST API.

What exists today (audited in the Vepathos repositories):

- **`api.vepathos.com` (`vepathos-api-doc`, Next.js + Prisma)** is the identity system of record
  (NextAuth: Google, GitHub, email + password), the plan catalog (`Plan` / `PlanLimits`), the
  stop ledger (`StopTransaction` reserve → confirm/release, `ApiUsagePeriod`) and the only
  metered gateway. Accounts are `User` rows; new accounts default to the Free ("Duck") plan.
- The public REST API for accounts is a multi-step RouteHub flow (depot → deliveries → fleet →
  lot → queue plan → poll → read plan). It creates persistent catalog resources.
- A **single-request asynchronous job engine** already exists in api-doc for marketplace
  channels (RapidAPI, Shopify): create job → poll status → read routes, with plan-gated features
  and served-stop billing. It is bound to channel identities and channel plan catalogs.
- The optimizer (`route-optimizer-app`) runs clustering/routing workers behind RabbitMQ and Redis.
  It has no compute cancellation and routes from one depot per request.
- Vepathos is **not** an OAuth authorization server today.

## Goals

1. Expose **what users want to accomplish** ("optimize my deliveries"), not the REST API.
2. Keep MCP **isolated** from the product: separate service, separate deploy and rollback,
   no direct access to databases, queues or the optimizer.
3. **One account, one entitlement:** Web, REST and MCP share plan, features, limits and quota.
   Usage is attributed with `usage_channel = "mcp"`.
4. **Asynchronous** and **stateless**: long optimizations never pin HTTP connections; any replica
   can serve any request.
5. **Token-efficient** results for problems with thousands of stops.
6. Public onboarding through **OAuth** ("Connect → Sign in / Sign up → Authorize"), with
   automatic Free/Duck account creation and an upgrade path that never requires reconnecting.
7. Discoverable and publishable (MCP Registry, Claude Connectors Directory, other clients).

## Non-goals

- Mirroring every REST endpoint as a tool; CSV/Excel parsing; geocoding (coordinates are required).
- Separate MCP plans, balances, pricing or Stripe integration. No payments inside MCP (no
  purchase tool). No x402.
- Cancelling optimizations (product decision: a submitted plan runs to completion).
- Modifying the optimizer engine.

## Alternatives considered

| Criterion | A. Inside the web frontend | B. Inside the Core API | C. Standalone service | D. Standalone + local npm package |
|---|---|---|---|---|
| Coupling | Web release cycle | MCP becomes part of the core | Contract-only (HTTPS) | Same as C, plus a second artifact |
| Deploy / rollback | Tied to web | Coordinated with core releases | Independent | Two artifacts to version |
| Scaling | Web process | Core process | Independent, stateless replicas | Same as C |
| Security surface | Browser-facing app grows | Core production surface grows | Small, isolated adapter | Local secrets on developer machines |
| Agent traffic isolation | No | Hard | Yes (own metrics, limits, logs) | Partial |
| Open source | No | No | Yes | Yes |
| Observability | Mixed with web | Mixed with core | Channel-specific | Split |
| Production risk | High | Medium | Low | Low/medium |

- **A** is rejected: it couples agent traffic to the frontend lifecycle and cannot be published.
- **B** is rejected: MCP protocol churn would force coordinated core releases and enlarge the
  core's attack surface. (The *billing/entitlement* part of the channel must still live in the
  core — see Decision.)
- **D** is not needed now: every target client supports remote Streamable HTTP, and stdio-only
  clients can use a generic remote bridge. This package still offers a **stdio mode from the same
  code** for self-hosting and development (credentials from the environment, as the MCP
  authorization spec recommends for stdio), so no logic is duplicated.

## Decision

**C — a standalone, stateless Remote MCP service (`vepathos-mcp`, Python, MCP SDK v2) that talks
HTTPS to a dedicated MCP channel inside Vepathos Core.**

```
Claude / ChatGPT / Cursor / VS Code / Codex
        │  MCP over Streamable HTTP (OAuth bearer token)
        v
mcp.vepathos.com — vepathos-mcp (N stateless replicas)
        │  HTTPS: user credential + service key
        v
api.vepathos.com — MCP channel (gate → entitlements → job engine → ledger)
        │
        v
optimizer (workers, RabbitMQ, Redis)
```

- **Tools:** `geocode_addresses`, `get_geocode_result`, `optimize_delivery_routes`, `get_optimization_result`. No cancel tool.
- **MCP channel in Core:** same pattern as the RapidAPI and Shopify channels (shared job engine),
  but identity is a **Vepathos account** and entitlements come from the **account's plan**.
- **Python** because SDK v2 is Tier 1 and stable on `2026-07-28`, supports stateless Streamable
  HTTP and resource-server auth helpers, Pydantic produces LLM-friendly schemas, and it matches the
  team's backend stack. TypeScript offered no demonstrable advantage (neither SDK ships the Tasks
  extension yet; an npm package is not needed).

### Why the channel lives in Core but the protocol adapter does not

The job engine, the plan catalog and the stop ledger are the source of truth and must stay in one
place. Implementing entitlement logic in the MCP service would duplicate billing. Conversely, the
MCP protocol surface (transport, sessions, schemas, token-efficient rendering) changes faster than
the product and belongs outside the core.

## Security model

### Trust boundary (OAuth user → account → MCP server → MCP channel → Core)

Every call from `vepathos-mcp` to the Core channel carries **two independent factors**:

1. **Service factor** — `X-Vepathos-MCP-Service-Key` (constant-time comparison, rotatable with
   two valid keys). It proves the call comes from `vepathos-mcp`. It **does not identify or
   select an account**.
2. **User factor (cryptographic)** — `Authorization: Bearer` with either
   - an OAuth access token issued by the Vepathos authorization server (RS256; the Core verifies
     signature, `iss`, `aud = https://mcp.vepathos.com/mcp`, `exp`, `scope` and that the grant is
     still active), where the account is the token `sub`; or
   - a developer credential `client_id:client_secret` with scope `mcp:optimize` (verified against
     the stored hash), where the account is the credential owner.

The Core **never** accepts an account, user or company identifier from headers or body. Leaking the
service key alone grants access to no account. A leaked user token cannot call the Core directly
(it lacks the service key) and is limited to its own account; revoking the grant cuts access
within 60 seconds. mTLS or per-request HMAC signing can be added later without changing the
contract.

### Token handling

- `vepathos-mcp` is an OAuth resource server: it validates tokens (JWKS) and answers `401` with
  `WWW-Authenticate: Bearer resource_metadata=…` so clients can discover the authorization server.
- The same MCP-audience token is sent to the Core channel, which is the back half of the MCP
  resource server and verifies it again. Tokens for other audiences are rejected, and REST or
  dashboard endpoints never accept MCP tokens. Therefore no token exchange is required.
- Tokens carry **no plan information**. Entitlements are read on every request, so upgrades apply
  on the next tool call without reconnecting.

### Other controls

HTTPS only; strict input validation (unknown fields rejected); request body limits; per-subject
rate limits; circuit breaker towards the Core; no URL inputs (no SSRF surface); structured logs
without tokens, coordinates or payloads; privacy-minimal schemas (ids, coordinates, weight, volume,
time windows — no names, phones or emails). See `docs/security.md`.

## Async model

- `optimize_delivery_routes` submits a job and returns an `optimization_id` immediately. It waits
  up to a few seconds and includes the result when small jobs finish quickly.
- `get_optimization_result` long-polls for a bounded time (≤ 20 s) and returns status, progress or a
  paginated result. This explicit-handle flow works in every client today.
- **MCP Tasks** (`io.modelcontextprotocol/tasks`) will be added when the SDK or target clients
  support it, as a second representation of the same Core job: `taskId = optimization_id`,
  `tasks/get` reads Core, `tasks/cancel` is acknowledged without effect (cooperative cancellation;
  the product does not cancel). No second job system is introduced.
- Identical requests are deduplicated with an idempotency key derived from the canonical arguments,
  so client retries never create duplicate jobs or double charges.

## Auth model

- **Production:** OAuth 2.1. The authorization server is implemented in api-doc on top of the
  existing NextAuth identity (Google, email). It supports PKCE S256, Client ID Metadata Documents
  (preferred) and Dynamic Client Registration (compatibility), rotating refresh tokens,
  revocation and a "Connected apps" page. Users without an account are created on the Free/Duck
  plan during the flow (`signup_source = "mcp"`).
- **Development / headless:** a developer credential with scope `mcp:optimize`
  (`AUTH_MODES=api_key`) or a single service credential for local testing (`AUTH_MODES=service`).
  These are not the public onboarding flow.

## Entitlements, upgrade and first-use trial

- Normal requests are evaluated against the account plan (stops per request, features, fleet size,
  stops per route, monthly quota, concurrency).
- When a plan cannot run a request, the Core returns `PLAN_UPGRADE_REQUIRED` with the plans that
  can (computed from the catalog) and `upgrade_url` or `contact_url`. While
  `NEXT_PUBLIC_PAID_PLANS_ENABLED` is not `true`, only Free is offered and the agent gets
  `contact_url` (internal / beta). For the public Claude directory, enable Stripe or state
  clearly that the channel is Free + contact. When paid plans are on, payment is delegated
  entirely to Stripe (Checkout / subscription). Vepathos does not take cards; MCP has no
  pay tool. The token has no plan claims, so retrying does not need a reconnect.
- **`MCP_FULL_FIRST_TRIAL`**: one promotional optimization per account, up to 2,000 stops, with
  weight, volume and time windows, that does **not** consume monthly quota. It is used only when
  the plan cannot run the request **and** the request has more than 500 stops **or** asks for a
  premium constraint the plan lacks. It is not a balance. See `docs/core-changes.md`.

## Deployment

`mcp.vepathos.com` runs as its own container, compose project, logs, health checks and DNS record,
initially on the same host as api-doc behind the edge reverse proxy. Replicas are stateless and
need no sticky sessions. See `docs/deployment.md`.

## Consequences

- Positive: isolated blast radius and releases, channel-specific observability, a single
  entitlement system, open-source adapter, and no engine changes.
- Negative: two repositories must evolve a shared HTTP contract (versioned under `/api/mcp/v1`,
  guarded by contract tests on both sides). The Core gains an OAuth authorization server that
  requires careful security testing.
- Known limitation: agents that write tool arguments as JSON can realistically send about 1–3k
  stops per call, although the solver accepts far more (see `docs/large-payloads.md`).

## Future extensions (measure first)

MCP Tasks; dataset upload handles for very large problems; MCP Apps (interactive route maps); multi-depot and structured unassignment reasons
in the engine; x402-style paid calls. None of these are part of v1.

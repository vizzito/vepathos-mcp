# Changes required in Vepathos Core

`vepathos-mcp` is only a protocol adapter. Everything that decides **who** the caller is,
**what** they may run and **how much** it costs stays in Vepathos Core (`vepathos-api-doc`).
This document lists the Core changes needed to support MCP, why each one is needed, and what is
intentionally *not* changed.

## 1. Gap analysis

| Need | Exists today? | Gap |
|---|---|---|
| Single-request async optimization for a Vepathos **account** | Only for RapidAPI/Shopify identities; accounts only have the multi-step RouteHub lots flow | New MCP channel on the shared job engine |
| Account-level entitlements for that channel | `getBillingOverview(userId)` exists (plan limits, features, pooled usage) | Adapter to the job engine's quota shape |
| Usage attribution `usage_channel = "mcp"` | Channel `usageSource` exists for marketplace channels | Hidden per-account ledger credential (`ApiClient.channel = "mcp"`) |
| Reservation reconciliation safe for channel jobs | **No** — the reconciler treats every reservation of a user-owned credential as a RouteHub lot | Exclude channel ledgers |
| Idempotent job creation | No | Deterministic job id from `Idempotency-Key` |
| Structured upgrade errors | Generic `plan_limit_exceeded` 403 | `PLAN_UPGRADE_REQUIRED` with eligible plans computed from the catalog |
| First-use promotional optimization | No promotional/Flex credit system exists (only the card welcome bonus) | Single-use marker on `User` (not a balance) |
| OAuth authorization server for third-party clients | No (NextAuth is only an OAuth *client*) | Minimal OAuth 2.1 AS |
| Signup attribution | No | `User.signupSource`, `User.signupClient` |

## 2. MCP channel (step 1)

Implemented with the same pattern as the RapidAPI and Shopify channels (`src/server/channels/metered-channel.ts`,
`src/server/rapidapi/*`). Contract: [`core-channel-contract.md`](./core-channel-contract.md).

- `lib/mcp-channel.ts` — configuration: enable flag, service keys (rotation), result TTL (24 h),
  stale window (1 h), max concurrent jobs per plan id, trial settings, resource URI.
- `src/server/channels/metered-channel.ts` — `MCP_CHANNEL` descriptor (`usageSource: "mcp"`).
- `src/server/mcp/gate.ts` — two-factor gate (service key + user credential). The account comes only
  from the verified user credential; identifiers in headers/body are ignored and rejected.
- `src/server/mcp/ledger-client.ts` — ensures one hidden `ApiClient` per account with
  `channel = "mcp"`; all MCP reservations are recorded there, so they count toward the account pool
  and are attributable.
- `src/server/mcp/entitlements.ts` — evaluates a request against the account plan and returns the
  list of failing dimensions (stops per request, features, fleet units, stops per route, monthly
  quota, concurrency).
- `src/server/mcp/idempotency.ts` — `job_id = "mcp_" + sha256(userId + ":" + Idempotency-Key)[:32]`,
  body hash stored with the reservation; replays return the existing job.
- `src/server/mcp/validate.ts` — unique ids, capacity completeness, time-window rules, depot distance.
- `src/server/mcp/{errors,results,attribution}.ts` and routes under `app/api/mcp/v1/`.
- Backward-compatible optional parameters in `src/server/rapidapi/usage.ts` and `job-access.ts`
  (period start, account-adjusted stop limit, replay flag, trial billing mode). RapidAPI and Shopify
  behaviour does not change; their test suites must stay green.

### Reconciler fix (done first, before integrating the channel)

`src/server/account/reserve-reconciliation.ts` scans reserved `StopTransaction` rows of user-owned
credentials and asks RouteHub about `lots/{lotId}/plan`. MCP job ids are not lots, so they would be
reconciled wrongly. Change: exclude credentials with `channel != null`. The generic stale sweep in
`stop-transaction-service.ts` also excludes channel ledgers, because channel jobs settle through
their own read path and stale window. Tests prove web lots are still reconciled.

### Credential scoping

- `lib/api-client-scope.ts`: channel ledger credentials are hidden from user-visible lists and from
  the API-key quota.
- `src/server/account/api-bearer-auth.ts`: the REST gateway requires a `routes:*` scope when the
  credential declares scopes, so an MCP-only developer key cannot call REST.
- Dashboard credentials page: option to create a developer credential with scope `mcp:optimize`
  (development/headless path).

## 3. `MCP_FULL_FIRST_TRIAL` (step 3)

One promotional optimization per account (lifetime), up to 2,000 stops, with weight, volume and
time windows, that does **not** consume the monthly quota. It is not a balance.

Configuration (backend-owned): `MCP_FULL_TRIAL_ENABLED`, `MCP_FULL_TRIAL_MAX_STOPS=2000`,
`MCP_FULL_TRIAL_STOPS_THRESHOLD=500`, `MCP_FULL_TRIAL_FEATURES=weight_capacity,volume_capacity,time_windows`,
`MCP_FULL_TRIAL_REFERENCE_PLAN=growth` (other caps for the trial run — stops per route, fleet units —
come from that plan row, so a 2,000-stop problem is not blocked by Free caps; duration routing is not
included).

Algorithm:

1. Validate input (`INVALID_*` on failure; nothing is touched).
2. Evaluate against the normal plan. If everything fits → run on the plan, charge quota normally,
   the trial stays available.
3. Otherwise, run under the trial (no quota reservation or charge) when **all** hold: trial available;
   `stops ≤ 2000`; trigger is `stops > 500` **or** a premium constraint (weight, volume, time windows)
   the plan lacks; the request fits the trial overlay.
4. Otherwise return `QUOTA_EXCEEDED` (only quota fails), `CONCURRENT_OPTIMIZATION_LIMIT`, or
   `PLAN_UPGRADE_REQUIRED`. The trial is not consumed. Nothing is split or truncated.

| Account + request | Result |
|---|---|
| Duck + 10 basic stops | plan, charges 10, trial available |
| Duck + 400 basic stops | `PLAN_UPGRADE_REQUIRED` (150/request; ≤ 500 and no premium), trial available |
| Duck + 600 basic stops | trial, quota untouched |
| Duck + 80 stops + time windows | trial |
| Duck + 1,850 stops + weight + volume + time windows | trial |
| Duck + 2,500 stops | `PLAN_UPGRADE_REQUIRED`, trial available (error says the trial covers up to 2,000) |
| Duck without quota + 100 basic stops | `QUOTA_EXCEEDED` |
| Duck without quota + 600 basic stops | trial (uses no quota) |
| Starter + 450 stops + time windows | trial |
| Growth + 600 stops + time windows | plan |
| Any plan + duration routing not in plan | `PLAN_UPGRADE_REQUIRED` |

Persistence (same pattern as `ccWelcomeBonus*`): `User.mcpFullTrialUsedAt`, `mcpFullTrialStops`,
`mcpFullTrialTrigger`, `mcpFullTrialJobId @unique`.

Lifecycle: atomic claim inside the reservation transaction (before the engine submit, so two
in-flight requests cannot both take the trial). Restored if submission fails, the engine returns
failed/expired, or the reservation goes stale without a result. Confirmed at the 7.5 review:
a technical failure of ours does not consume the lifetime trial. Idempotent retries of a
successful claim never consume it again. The trial job is recorded in `StopTransaction` with
`billing: "mcp_full_trial"` and never touches `ApiUsagePeriod`.

## 4. `PLAN_UPGRADE_REQUIRED` (step 6)

- The entitlement evaluation yields the failing dimensions and requested values.
- Eligible plans are computed from the plan catalog in the database (the MCP never knows plan
  names): plans whose limits and features cover every failing dimension, ordered by catalog order.
- `upgrade_url` points to `/dashboard/billing?upgrade=<plan>&reason=<reason>&source=mcp` when a
  self-serve paid plan is eligible and paid plans are enabled; otherwise `contact_url`.
  Free-only (no Stripe) is the intended beta posture. A Claude directory reviewer who submits a
  large job will be told to contact, not to pay, until Stripe is on or the listing says Free +
  contact.
- The billing page preselects the plan and sends the user to Stripe for payment (no in-app card
  capture on the MCP path). After Checkout, it tells them to go back to the assistant and retry.
  Checkout metadata carries `source=mcp` for attribution only.
- Rejections store nothing, so retrying the same tool call after upgrading creates the job. Tokens
  carry no plan data, so no reconnection is needed.

## 4b. `GET /api/mcp/v1/account` (done)

`vepathos-mcp` ships the `get_account` tool against the contract in
[`core-channel-contract.md`](./core-channel-contract.md#get-apimcpv1account); Core serves the route
in `app/api/mcp/v1/account/route.ts`. Against an older Core that lacks it, the tool answers that the
deployment does not report account information yet, rather than a generic failure.

Why it is needed: nothing in the channel tells a user *which* Vepathos account a client is connected
to. A second sign-in (an older personal account, another company) silently becomes the billed
account, and the mismatch only surfaces as a plan or quota rejection, which reads as a Vepathos
limitation rather than a wrong connection. The tool also feeds the account label that plan and quota
errors now carry.

Implementation is a read of data Core already has, with no new state:

- Route `app/api/mcp/v1/account/route.ts` over `src/server/mcp/account.ts` (database access stays in
  `src/server/**`), behind the same two-factor gate as every other MCP route
  (`src/server/mcp/gate.ts`), account derived only from the verified credential. No billing record
  for that user fails closed with `401 INVALID_CREDENTIALS`.
- Body built from `getBillingOverview(userId)` — the same source
  `src/server/mcp/entitlements.ts` already uses for limits, features and period usage — plus
  `maxActiveJobsForPlan`, the `User`/company record for the label, and the existing full-trial
  marker for `full_trial_available`.
- Send `max_stops_per_request: null` with `unlimited_stops_per_request: true` for Unlimited plans,
  so "no ceiling" and "field missing" stay distinguishable. `features` is derived from the same
  `OptimizationFeatures` flags `evaluateMcpEntitlements` checks, so the advertised set cannot drift
  from what an optimization accepts. The email is masked in Core, before it reaches the adapter.
- No quota is charged and no job is created; it is a read of the account's own data, so it needs no
  new scope beyond `optimize` / `mcp:optimize`.

## 4c. `GET /api/mcp/v1/catalog` (done)

Serves the account's fleets and vehicles for the `list_fleet` tool
(`app/api/mcp/v1/catalog/route.ts` over `src/server/mcp/catalog.ts`).

Why: without it an assistant invents a fleet, so the plan is geometric — it cannot honour payload or
volume, and its vehicle ids mean nothing to the operation. The stop weights users already keep in
their spreadsheets are unusable until the real capacities are known.

- Reads through `proxyRoutehubForUser(userId, "GET", ["fleets" | "vehicles"])`, so the RouteHub
  tenant guard scopes the catalog to the caller's own company; no new upstream path.
- `GET /fleets` and `GET /vehicles` are not billable triggers (`isBillableOptimizationTrigger`), so
  nothing is metered.
- Capacities are converted to kilograms and cubic metres in Core; an unrecognised unit drops the
  value rather than guessing it, because a wrong capacity plans a route that cannot be loaded.
- One resource failing still returns the other; both failing is `503 BACKEND_UNAVAILABLE`.
- Read-only, and it runs under the existing `optimize` / `mcp:optimize` scope: it reads the caller's
  own catalog in service of an optimization. **Any write to the catalog needs a new scope and fresh
  consent** — today's tokens carry no permission to modify anything.

## 5. OAuth authorization server (steps 4–5)

Minimal OAuth 2.1 AS in api-doc (issuer `https://api.vepathos.com`), reusing NextAuth sessions,
`User`/`Account`, RS256 signing and JWKS:

- `/.well-known/oauth-authorization-server` (RFC 8414; `authorization_response_iss_parameter_supported`,
  `client_id_metadata_document_supported`, `code_challenge_methods_supported: ["S256"]`,
  `token_endpoint_auth_methods_supported` including `none`, `scopes_supported: ["optimize", "offline_access"]`).
- Authorization endpoint with consent screen (client name, redirect host, loopback warning, CSRF,
  anti-framing). Users without a session sign in or sign up with Google or email; new users get the
  Free/Duck plan and `signupSource = "mcp"`.
- Token endpoint (form-urlencoded): authorization code + PKCE, rotating refresh tokens with reuse
  detection (`invalid_grant`).
- Client ID Metadata Documents with SSRF-safe fetching; Dynamic Client Registration for compatibility.
- Revocation (RFC 7009) and a "Connected apps" dashboard page; password change or suspension revokes
  grants.
- Access tokens: RS256, 1 h, `aud = https://mcp.vepathos.com/mcp`, `sub = User.id`, `client_id`,
  `scope`, `grant_id`; no plan claims.

## 6. Not changed

- Optimizer engine (no cancellation, single depot per job).
- RouteHub API and database.
- `POST /api/token` (M2M) and the public REST contract, except for the scope check.
- Stripe products, prices and billing logic.
- No MCP-specific plans, balances, pricing or subscriptions.

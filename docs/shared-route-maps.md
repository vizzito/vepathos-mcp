# Shared route maps — integration contract (v1)

Implementation spans `vepathos-mcp`, `vepathos-api-doc` and the existing webclient.
Not deployed. The adapter tool is disabled by default until Core and the page are ready.

## Webclient handoff

Page: `/shared/routes/[token]`, with no login. Use the existing
`lib/shared-routes/types.ts` wire shape (`version: 1`). Fetch server-side:

`GET {API_DOC_ORIGIN}/api/shared/routes/{token}`

No account bearer or MCP service key is needed for this read. The opaque token
is the share capability. Keep it out of analytics, error reports and access logs.
Fetch with `cache: 'no-store'`; do not forward cookies or cache the page/CDN response.
Use `Referrer-Policy: no-referrer` and `X-Robots-Tag: noindex, nofollow, noarchive`.
Core returns:

- 200: the wire view directly, not wrapped in `result` or `data`.
- 404 + `error.code = SHARE_INVALID`: invalid or missing share.
- 410 + `error.code = SHARE_EXPIRED`: expired share.
- 410 + `error.code = SHARE_REVOKED`: revoked share.
- 5xx/network error: unavailable; do not report it as expired.

The token is a 116-character base64url string compatible with the page's existing
22–128 character allowlist. The browser must not decode it or use it as an account ID.

Display `expires_at` in the user's local time and a visible notice:
“This map is shared by link and is available for 48 hours after creation.”
Expired page: “This map has expired. Shared maps are available for 48 hours.”
Do not suggest refreshing to extend the link. When an open page reaches `expires_at`,
clear its rendered map and show the same expired state. Downloads/screenshots already
made by viewers cannot be revoked.

Snapshot fields currently produced:
- version, generated_at, expires_at;
- summary: stops_assigned, stops_unassigned, vehicles_used;
- routes: route_key, distance_km, duration_minutes, stops;
- each stop: sequence, lat, lng, stop_ref (share-scoped `s1`, `s2`, ...);
- unassigned: stop_ref only.

Depot, street geometry, labels, schedule and coordinates of unassigned stops are
optional and currently omitted: do not invent them or draw a road trace. The current
Core ledger preserves submitted stop IDs, not their coordinates or depot; routed
coordinates come from the engine. Preserve original optimization behavior.

## Language

The map link may carry `?lang=en|es|pt`. It is presentation only: not part of the
token, not stored with the share, not sent to Core. One share (one token) opens in
any language, so Core's one-share-per-job dedupe and the fixed expiry are unaffected.

- MCP: `create_optimization_map` accepts optional `language` (BCP 47, e.g. `es-AR`,
  `pt_BR`). Supported base languages add `?lang=<base>` to `map_url` and echo
  `language` in the output; unsupported ones return the plain link. Core still
  receives `{}` with `Idempotency-Key: map:{jobId}`.
- Webclient: `?lang` wins over the viewer's cookie / `Accept-Language`, falls back to
  English. It localizes the page, metadata, the WhatsApp share message and the PDF
  report; re-shared links keep `?lang`. Unsupported values are ignored.

## Core operations

`POST /api/mcp/v1/optimization/jobs/{jobId}/map`, body `{}`:
requires the existing MCP gate (user credential + service key), verified account
ownership and a completed, unexpired optimization. Returns `map_url`, `expires_at`,
`access: "anyone_with_link"`, `notice`. No route optimization or stop billing occurs.

One share per account/job; a unique DB constraint and upsert deduplicate retries.
Adapter sends `Idempotency-Key: map:{jobId}`. Repeated requests never extend expiry.
Expired or revoked shares cannot be recreated by retrying. A minimal snapshot makes
the map independent of the engine's 24-hour result retention.

`DELETE` on the same authenticated endpoint revokes the share and clears its JSON.
This revokes the link, not the optimization; no cancellation tool is introduced.

`GET /api/cron/mcp-map-retention` with `Authorization: Bearer {CRON_SECRET}` clears
expired/revoked snapshot JSON. Schedule this in the existing production scheduler
(e.g. every minute). Access expires exactly at 48 hours; physical JSON deletion
happens on the next successful sweep. Non-geographic tombstones remain for deduplication.
Signed expired tokens still return SHARE_EXPIRED after their snapshot is gone.
Account deletion cascades to share rows. Backup retention is a separate policy.

## Deployment sequence

1. Core migration `20260914140000_mcp_map_shares`; do not reset the DB.
2. Core env: `MCP_MAP_SHARES_ENABLED=true`, `MCP_MAP_WEB_URL=https://<webclient-origin>`,
   `MCP_MAP_SHARE_SECRET=<independent random secret of at least 32 characters>`.
   Never send the secret to MCP or the webclient. Keep it stable across replicas;
   changing it invalidates existing links. Do not reuse a user/API credential.
3. Configure and verify the authenticated cleanup schedule.
4. Deploy the webclient page using the read contract above.
5. Enable adapter env `MCP_MAP_SHARES_ENABLED=true` and restart/recreate the adapter.
6. Run authenticated end-to-end tests with a completed job. Verify same-link replay,
   another account denied, public page, missing/invalid token, expiry and revocation.
   Test expiry on a controlled local fixture/clock, not by changing production dates.

## MCP caller

Call `create_optimization_map` with `{"optimization_id":"mcp_...","language":"es"}` only when the
user wants a map/share link. The tool description asks the agent to explain public
link access and expiration. It does not publish a share automatically on optimization.

## Verification at implementation time

MCP: 75 non-integration tests pass, mypy passes, changed Python files pass ruff.
Core: 10 new unit tests pass (token validity/expiry, public privacy and revocation,
owner-scoped lookup, replay and expired replay). Prisma client generation succeeds.
Core's full typecheck is blocked by existing errors in Shopify's reservation union
and MCP entitlements' required catalog field from concurrent RapidAPI changes.
No migration was applied, no PostgreSQL concurrency test or live browser end-to-end
was run, and nothing was committed or deployed by this task.

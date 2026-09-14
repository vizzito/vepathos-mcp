Integrate the shared routes page with the contract in `docs/shared-route-maps.md`
in the sibling vepathos-mcp repository. Core endpoints have now been implemented
locally, but not deployed or migrated.

Use server-side GET /api/shared/routes/[token] on api-doc, no-store, without forwarding
user cookies. The response is the version:1 wire view already described by your
lib/shared-routes/types.ts. Tokens conform to your base64url allowlist.

Handle SHARE_INVALID (404), SHARE_EXPIRED (410), SHARE_REVOKED (410) separately from
upstream failures. Links expire 48 hours after creation, without renewal on replay.
Show the expiration timestamp and “Anyone with this link can see these locations”.
Clear an open map at expires_at and show the expired message. Disable indexing,
shared caches, analytics token capture and referrer leakage.

This initial snapshot includes routed coordinates, share-scoped stop references,
route metrics and unassigned references. Depot, road geometry and unassigned
coordinates are not yet available: respect their optionality. Do not fabricate them.

Do not change the working Results screen or deploy until the local end-to-end test
passes with the new Core migration. Coordinate any wire schema changes first.

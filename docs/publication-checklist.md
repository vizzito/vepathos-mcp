# Publication checklist

Re-verify every external requirement immediately before submitting: directory and registry rules change.
Publishing steps (production deploy, DNS, public repository, submissions) require explicit approval.
**Publish-everywhere approved 2026-09-14.** Execute in the order in [docs/publish-marketplaces.md](publish-marketplaces.md). Listings stay **Free + contact** until Stripe is on.

## 0. Pre-deploy review (required)

Presented 2026-09-13 (step 7.5). Approved the same day. Restore-on-engine-failure: yes.
Paid upgrades, when enabled, are delegated entirely to Stripe. Public deploy (step 8) still
needs a separate explicit OK.

- [x] Final `MCP_FULL_FIRST_TRIAL` implementation in Core (algorithm, persistence, restoration on engine failure)
- [x] Reservation reconciler change (channel ledgers excluded, web lots still reconciled)
- [x] Trust model OAuth → account → MCP channel (two-factor gate and negative tests)
- [x] `PLAN_UPGRADE_REQUIRED` implementation (eligible plans from the catalog, upgrade URL, no reconnect after upgrade)

## 1. Production readiness

- [x] Core MCP channel and OAuth authorization server deployed on api-prod (2026-09-14); local security suites were green before ship — re-run if Core changes
- [x] `mcp.vepathos.com` deployed on api-prod ([docs/deploy-api-prod.md](deploy-api-prod.md): GoDaddy `A`, `vepathos-net` overlay, Caddy, hairpin `CADDY_VETH_IP`, then `MCP_CHANNEL_ENABLED`); `/ready` `core: ok`; `401` + resource metadata; authenticated `tools/list` via curl + dashboard key. Geocode/optimize smoke still open.
- [ ] Privacy policy updated with an MCP section (data sent, retention of 24 h results, logs without payloads): public HTTPS URL — draft: `docs/privacy-mcp.md`
- [ ] Public documentation page (e.g. `vepathos.com/mcp`) with onboarding, tools, limits and support contact — draft: `docs/public-mcp-page.md`
- [ ] Test account (Growth plan, populated) for reviewers
- [ ] Smoke prompts pass with at least one real client (`docs/smoke-prompts.md`)
- [x] Compatibility matrix updated with tested clients (Inspector fake Core + real-stack discovery; Claude still empty)

## 2. Official MCP Registry — done, 0.11.1 live

- [x] `server.json` validated against `https://static.modelcontextprotocol.io/schemas/2025-12-11/server.schema.json`
- [x] DNS authentication for `com.vepathos/*`: Ed25519 key, TXT record `v=MCPv1; k=ed25519; p=<public key>` at the apex `vepathos.com`
- [x] `mcp-publisher login dns --domain vepathos.com --private-key …`
- [x] `mcp-publisher publish`; **republish on every version bump** — the last one it did not see is a live gap, not a one-time task. See [publish-marketplaces.md §3](publish-marketplaces.md) for the exact command.

## 3. Claude Connectors Directory

- [x] Listing posture decided: **Free + contact** (Stripe off). Copy in [docs/directory-listing.md](directory-listing.md)
- [ ] Team or Enterprise organization with directory permission (submission portal in Claude.ai)
- [ ] Remote server over HTTPS, Streamable HTTP
- [ ] OAuth: CIMD (`client_id_metadata_document_supported: true` and `none` in `token_endpoint_auth_methods_supported`), PKCE S256 advertised, callback `https://claude.ai/api/mcp/auth_callback`, Claude Code loopback redirects on any port, `offline_access`, refresh rotation with `invalid_grant`, OAuth endpoints answering in under 10 s
- [ ] Protected resource metadata `resource` equals `https://mcp.vepathos.com/mcp` exactly; authorization server reachable from `160.79.104.0/21`
- [ ] Every tool has `title` and the correct `readOnlyHint` / `destructiveHint`; names ≤ 64 chars
- [ ] Listing: name, tagline (≤ 55 chars), description (≤ 2,000 chars), categories, documentation URL, privacy policy URL, support contact, icon
- [ ] Data handling: first-party API; no health data
- [ ] Seven policy acknowledgments
- [ ] Every tool exercised via MCP Inspector and as a custom connector

## 4. Other channels

- [ ] ChatGPT: developer mode test; Apps directory submission if pursued
- [ ] Cursor: verify install flow and any directory listing
- [ ] VS Code: MCP gallery entry via the official registry
- [x] Smithery and Glama: both listed, both ingest the official registry so they refresh on their own crawl (can lag a deploy by up to a day)
- [x] GitHub: public repository `vizzito/vepathos-mcp` (Apache-2.0), README, security policy
- [ ] Vepathos website: landing section, "Research & benchmarks" link to DOI 10.5281/zenodo.19859531 (no peer-review or superiority claims)

## 5. README buttons

Add "Add to Claude / Cursor / VS Code / Codex" entries only after each flow is verified end to end.

## Notes

- The list of tools is generated in [tools-reference.md](tools-reference.md)
  (`vepathos-mcp tools --write-docs`); it stays in sync with the code on every version bump.
- `server.json`'s version has to match `__version__` before `mcp-publisher publish`, every time —
  the registry does not infer it, and a mismatch means the entry advertises a version the endpoint
  does not serve.

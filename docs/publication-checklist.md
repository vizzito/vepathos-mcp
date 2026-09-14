# Publication checklist

Re-verify every external requirement immediately before submitting: directory and registry rules change.
Publishing steps (production deploy, DNS, public repository, submissions) require explicit approval.

## 0. Pre-deploy review (required)

Presented 2026-09-13 (step 7.5). Approved the same day. Restore-on-engine-failure: yes.
Paid upgrades, when enabled, are delegated entirely to Stripe. Public deploy (step 8) still
needs a separate explicit OK.

- [x] Final `MCP_FULL_FIRST_TRIAL` implementation in Core (algorithm, persistence, restoration on engine failure)
- [x] Reservation reconciler change (channel ledgers excluded, web lots still reconciled)
- [x] Trust model OAuth → account → MCP channel (two-factor gate and negative tests)
- [x] `PLAN_UPGRADE_REQUIRED` implementation (eligible plans from the catalog, upgrade URL, no reconnect after upgrade)

## 1. Production readiness

- [ ] Core MCP channel and OAuth authorization server deployed; security test suites green
- [ ] `mcp.vepathos.com` deployed (`docs/deployment.md`); `/ready` ok; `401` + resource metadata
- [ ] Privacy policy updated with an MCP section (data sent, retention of 24 h results, logs without payloads): public HTTPS URL — draft: `docs/privacy-mcp.md`
- [ ] Public documentation page (e.g. `vepathos.com/mcp`) with onboarding, tools, limits and support contact — draft: `docs/public-mcp-page.md`
- [ ] Test account (Growth plan, populated) for reviewers
- [ ] Smoke prompts pass with at least one real client (`docs/smoke-prompts.md`)
- [x] Compatibility matrix updated with tested clients (Inspector fake Core + real-stack discovery; Claude still empty)

## 2. Official MCP Registry

- [ ] `server.json` validated against `https://static.modelcontextprotocol.io/schemas/2025-12-11/server.schema.json` (check the description length limit)
- [ ] DNS authentication for `com.vepathos/*`: Ed25519 key, TXT record `v=MCPv1; k=ed25519; p=<public key>` at the apex `vepathos.com`
- [ ] `mcp-publisher login dns --domain vepathos.com --private-key …`
- [ ] `mcp-publisher publish`; version bumped on every contract change

## 3. Claude Connectors Directory

- [ ] Stripe self-serve is live, **or** the listing and public docs state that MCP is Free + contact (a reviewer who submits a large job will be told to contact, not to pay)
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
- [ ] Smithery and other aggregators: confirm they are still relevant; most ingest the official registry
- [ ] GitHub: public repository `vepathos-mcp` (Apache-2.0), README, security policy
- [ ] Vepathos website: landing section, "Research & benchmarks" link to DOI 10.5281/zenodo.19859531 (no peer-review or superiority claims)

## 5. README buttons

Add "Add to Claude / Cursor / VS Code / Codex" entries only after each flow is verified end to end.

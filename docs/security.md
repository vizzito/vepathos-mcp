# Security review

Scope: `vepathos-mcp` and its trust boundary with the Vepathos Core MCP channel. Reviewed against the
MCP 2026-07-28 authorization specification and security best practices.

## Threat model

| Asset | Threat | Control |
|---|---|---|
| Account access | Service key leak used to act on accounts | Two-factor channel: the service key never selects an account; Core derives it only from a verified user credential. Negative tests in Core. |
| Account access | Stolen OAuth token | 1 h RS256 tokens bound to the MCP audience; grant revocation checked by Core (≤ 60 s); refresh token rotation with reuse detection. |
| Account access | Token for another resource replayed here | Audience (`aud = MCP resource URI`), issuer and expiry verified locally and again in Core; RS256 only (no `none`/HS256). |
| Account access | Token passthrough to other APIs | MCP-audience tokens are accepted only by the MCP channel; REST and dashboard reject them. |
| Optimization data | Handle guessing (`optimization_id`) | Unguessable ids scoped to the account in Core; possession grants nothing. |
| Billing | Duplicate jobs from retries | Deterministic `Idempotency-Key`, per-account dedupe in Core; POST retries only with the same key. |
| Billing / capacity | Agent loops or floods | Per-subject rate limits at the edge; Core quota and concurrency limits; circuit breaker; body size limits. |
| Service | DNS rebinding / cross-origin browser calls | Host and Origin allow-lists (`MCP_ALLOWED_HOSTS`, `MCP_ALLOWED_ORIGINS`). |
| Service | SSRF | No tool accepts URLs; outbound traffic only to the configured Core base URL and JWKS URL. |
| Secrets | Leakage in logs | JSON logs with key- and pattern-based redaction (bearer tokens, `vpt_sk_` secrets, JWTs); no payloads, coordinates or arguments logged; httpx/SDK request logs muted. |
| Privacy | Unnecessary personal data | Schemas accept ids, coordinates, weight, volume and time windows only; outputs never echo coordinates; Core retains results 24 h. |
| Supply chain | Vulnerable dependencies | Pinned ranges, `pip-audit` in CI, minimal runtime image, non-root user, read-only filesystem. |
| Prompt injection | Tool text steering the model | Descriptions describe behaviour only; user-provided ids are length/charset limited; no raw engine messages are forwarded. |

## Controls checklist

- [x] HTTPS required in production (`ENVIRONMENT=production` refuses non-https URLs); HSTS at the proxy.
- [x] Secrets only from the environment or secret files; `.env` ignored by git; production refuses default salt or empty service key.
- [x] Strict JSON-schema validation with Pydantic (`extra=forbid`, ranges, patterns); unknown fields rejected.
- [x] Validation errors summarize at most 10 issues and never echo input values.
- [x] Authentication on every MCP request; `401` + `WWW-Authenticate` with protected resource metadata (OAuth).
- [x] Rate limiting (edge) + quota/concurrency (Core) + circuit breaker + timeouts (connect 5 s, read 20 s, submit 45 s).
- [x] Request body limit (8 MiB) at the app and the proxy.
- [x] Error messages clipped and free of stack traces, paths, worker or queue names.
- [x] `/metrics` only with a bearer token or from private networks; blocked at the public proxy.
- [x] Container: non-root, read-only root filesystem, `no-new-privileges`, all capabilities dropped.
- [x] Core: negative tests for the two-factor gate (service key without token, token without service key, ledger used as user factor, revoked grant, suspended account, injected account id).
- [x] Core: OAuth authorization server security suite (PKCE, redirect URI exact match, refresh reuse, CIMD SSRF). External review still required before public launch.

## Findings in Vepathos Core (outside this repository)

- The REST plan-limit guard fails open when the billing lookup throws. The MCP channel fails closed.
- The reservation reconciler treats every reservation of a user-owned credential as a RouteHub lot. It
  must exclude channel ledgers before MCP jobs exist (see `docs/core-changes.md`).
- Rate limiters in Core are in-memory per process; the MCP channel relies on DB-level concurrency and
  quota checks as the authoritative limits.

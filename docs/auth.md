# Authentication and account model

Vepathos owns identity and entitlements. `vepathos-mcp` authenticates callers, forwards the caller's
own credential to the Vepathos Core MCP channel, and never decides which account is used.

## Modes (`AUTH_MODES`)

| Mode | For | Inbound credential | Forwarded to Core |
|---|---|---|---|
| `oauth` | Production (default public onboarding) | OAuth access token issued by `https://api.vepathos.com` for `https://mcp.vepathos.com/mcp`, scope `optimize` | The same token |
| `api_key` | Developers, CI and headless agents | `vpt_mcp_<id>:vpt_sk_<env>_<secret>` (or a classic `vpt_<id>`) with scope `mcp:optimize` | The same credential |
| `service` | Local development only (refused in production) | Static `MCP_DEV_BEARER_TOKEN` | `VEPATHOS_SERVICE_CREDENTIAL` from the environment |

`MCP_TRANSPORT=stdio` (self-hosting) uses `VEPATHOS_SERVICE_CREDENTIAL` for every call, as the MCP
authorization specification recommends for stdio.

## Trust boundary

```
OAuth user ──► authorized Vepathos account ──► vepathos-mcp ──► authenticated MCP channel ──► Core
```

Each call from `vepathos-mcp` to Core carries two independent factors:

1. `X-Vepathos-MCP-Service-Key`: proves the caller is the MCP service. It **does not select an account**.
2. `Authorization: Bearer <user credential>`: Core verifies it (JWT signature, issuer, audience,
   expiry, scope and active grant; or the stored credential hash) and derives the account from it.

Core never accepts an account, user or company id in headers or body. A leaked service key alone
cannot reach any account. A leaked user token cannot call Core directly (no service key) and only
affects its own account. Revoking the grant stops access within 60 seconds.

## OAuth (production)

Discovery follows the MCP authorization specification (2026-07-28):

1. An unauthenticated request to `/mcp` receives `401` with
   `WWW-Authenticate: Bearer error="invalid_token", resource_metadata="https://mcp.vepathos.com/.well-known/oauth-protected-resource/mcp"`.
2. Protected Resource Metadata (RFC 9728) lists `authorization_servers: ["https://api.vepathos.com"]`
   and `scopes_supported: ["optimize"]`.
3. The client reads `https://api.vepathos.com/.well-known/oauth-authorization-server` (RFC 8414),
   registers through a Client ID Metadata Document (preferred) or Dynamic Client Registration, and
   runs authorization code + PKCE (S256) with `resource=https://mcp.vepathos.com/mcp`.
4. The user signs in or signs up on Vepathos (Google or email), approves the client on a consent
   screen, and returns to the client.
5. Access tokens (RS256, 1 h) carry `sub` (Vepathos user id), `client_id`, `scope` and `grant_id`. They
   carry **no plan data**, so upgrades never require reconnecting. Refresh tokens rotate.

The authorization server lives in `vepathos-api-doc` and reuses NextAuth sessions, the `User` table,
the RS256 signing key and the JWKS endpoint. See `docs/core-changes.md`.

### Accounts created from MCP

A user without a Vepathos account who connects from an MCP client gets an ordinary account on the
Free ("Duck") plan, created during the OAuth flow. Core records `signupSource = "mcp"` and, when the
client is verifiable through its metadata document host, `signupClient` (e.g. `claude`). There are
no MCP-specific plans, balances or subscriptions.

### Logout and revocation

- "Connected apps" in the Vepathos dashboard and `POST /api/oauth/revoke` (RFC 7009) revoke a grant
  and its refresh tokens.
- Password change and account suspension revoke all grants.
- "Sign out everywhere" ends web sessions but does not disconnect apps.

## Development configuration examples

Claude Code against a local server in `service` mode:

```bash
claude mcp add --transport http vepathos-dev http://localhost:8080/mcp --header "Authorization: Bearer $MCP_DEV_BEARER_TOKEN"
```

Headless agents with a developer credential (`api_key` mode) send
`Authorization: Bearer vpt_mcp_…:vpt_sk_…` (classic `vpt_…` keys still work). Keep credentials out of shared or committed configuration files.

Local OAuth against the Core worktree (`vepathos-api-doc-mcp` on `:3001`):

```bash
# adapter .env
AUTH_MODES=oauth,api_key
OAUTH_ISSUER=http://localhost:3001
OAUTH_JWKS_URL=http://localhost:3001/api/jwks
MCP_PUBLIC_URL=http://localhost:8080

# Core .env.local
MCP_OAUTH_ISSUER=http://localhost:3001
MCP_RESOURCE_URI=http://localhost:8080   # JWT aud becomes http://localhost:8080/mcp
```

`./scripts/oauth-local.sh` checks discovery, registers a DCR client and prints the consent URL.
`./scripts/inspect.sh` still uses an API key (CLI). The Inspector **web** UI can now complete DCR.

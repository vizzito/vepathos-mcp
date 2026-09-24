# Incident runbook — MCP on api-prod

How to diagnose a client that cannot connect, cannot optimize, or behaves oddly. Written from the
2026-09-16 incidents, where most of the time went into evidence that was missing or misread rather
than into the fixes.

- Deploying, secrets, Caddy, rollback: [deploy-api-prod.md](deploy-api-prod.md)
- What each log field means: [observability.md](observability.md)
- The tool contract: [tools.md](tools.md)
- Checking a real agent end to end: [smoke-prompts.md](smoke-prompts.md)

## Triage

| Symptom | First check | Section |
|---|---|---|
| The client shows "Connect" again after authorizing | Auth codes exchanged, grant `lastUsedAt` | [Connect loop](#chatgpt-shows-connect-again-after-authorizing) |
| The client reads account and fleet but never optimizes | `optimize_routes` lines with `optimization_id: null` | [Never optimizes](#the-client-reads-data-but-never-optimizes) |
| Nothing from a client reaches the MCP | `tool_call` lines with its `client_type` | [Where the evidence is](#where-the-evidence-is) |
| `curl` to `/mcp` answers 401 | Nothing: 401 without a credential is correct | [Checking the endpoint](#checking-the-endpoint-by-hand) |
| `[auth][error] CallbackRouteError: unexpected "iss"` in api-doc | Which provider the error names | [Auth.js iss error](#authjs-callbackrouteerror-unexpected-iss) |
| An `.env` change has no effect | Whether the container was recreated | [Configuration](#configuration-that-must-not-change-casually) |

## Where the evidence is

What logs a request and what does not:

| Hop | Logs requests? | Use instead |
|---|---|---|
| Caddy | No access log configured | — |
| api-doc (`next start`) | No request log; `[auth][error]` lines only | The database, below |
| MCP adapter | Yes: uvicorn access lines and one JSON `tool_call` line per tool call | `docker logs` |

So the OAuth flow (`/oauth/register`, `/oauth/authorize`, `/oauth/token`) leaves **no log line
anywhere**. An empty api-doc log does not mean the client never arrived. The database records every
step; see [Tracing an OAuth connection](#tracing-an-oauth-connection-through-the-database).

Both log streams go to **stderr**, so a pipe needs `2>&1` or `grep` filters nothing:

```bash
docker logs --since 30m vepathos-mcp-vepathos-mcp-1 2>&1 | grep tool_call
```

Follow live while reproducing:

```bash
docker logs -f --since 1m vepathos-mcp-vepathos-mcp-1 2>&1 | grep --line-buffered tool_call
```

Uvicorn access lines carry no timestamp. `-t` adds one to every line, and `--since`/`--until` take
UTC with a `Z`, matching the database:

```bash
docker logs -t --since 2026-09-16T15:33:00Z --until 2026-09-16T15:37:00Z vepathos-mcp-vepathos-mcp-1 2>&1 | grep -v /health
```

A recreated container starts a new log. Check its start time before trusting an empty window:

```bash
docker inspect vepathos-mcp-vepathos-mcp-1 --format '{{.State.StartedAt}}'
```

### Reading a `tool_call` line

- `optimization_id` is read from the tool's **output**, not its arguments. It is `null` for
  `get_account`, `list_fleet`, the geocode tools and `create_optimization_map` (which takes an id but
  does not return one), and for an optimize call answered with a preflight.
- `client_type` is `chatgpt`, `claude`, `claude-code`, `cursor`, `vscode`, `codex`, `inspector` or
  `other`.
- An optimize call that really ran has a non-null `optimization_id` and `status` `queued`, `running`
  or `completed`. Large problems do not finish within the ~8 s inline wait; the client must then call
  `get_optimization_result`, which appears as its own line with the same id.
- Lines from scanners probing paths such as `/js/twint_ch.js` answer 404 and are background noise.

## Tracing an OAuth connection through the database

Every step of the flow leaves a row with a timestamp, in api-doc's database:

| Step | Row |
|---|---|
| Client registration (DCR) or first use of a metadata-document client | `McpOAuthClient` |
| User signs in and consents at `/oauth/authorize` | `McpOAuthGrant` upserted, `McpOAuthAuthCode` created |
| Client exchanges the code at `/oauth/token` | `McpOAuthAuthCode.consumedAt` set, `McpOAuthRefreshToken` created |
| A **tool call** reaches Core with that grant's token | `McpOAuthGrant.lastUsedAt` (at most once a minute) |

`initialize` and `tools/list` are answered by the adapter and never reach Core, so they do not move
`lastUsedAt`. ChatGPT, Claude and Codex register as metadata-document clients: their `clientId` is an
`https://` URL, and one grant exists per user, client and resource.

Auth codes from the last two hours — how far each connection attempt got:

```bash
docker exec -i vepathos-postgres psql -U postgres -d vepathos_app -c "SELECT c.\"clientName\", a.\"createdAt\" AS code_issued, a.\"consumedAt\" AS code_exchanged, a.\"redirectUri\" FROM \"McpOAuthAuthCode\" a JOIN \"McpOAuthGrant\" g ON g.id = a.\"grantId\" JOIN \"McpOAuthClient\" c ON c.\"clientId\" = g.\"clientId\" WHERE a.\"createdAt\" > now() - interval '2 hours' ORDER BY a.\"createdAt\" DESC;"
```

- No row for the attempt: it never completed sign-in and consent.
- `code_exchanged` empty: consent worked, the token exchange failed (PKCE, `redirect_uri`, `client_id`).
- `code_exchanged` set: OAuth finished. Anything still wrong is after the token, usually in the client.

Grants — whether a client is using its tokens:

```bash
docker exec -i vepathos-postgres psql -U postgres -d vepathos_app -c "SELECT c.\"clientId\", c.\"clientName\", c.\"createdAt\" AS client_created, g.scope, g.\"revokedAt\", g.\"lastUsedAt\", g.\"updatedAt\" FROM \"McpOAuthGrant\" g JOIN \"McpOAuthClient\" c ON c.\"clientId\" = g.\"clientId\" ORDER BY g.\"updatedAt\" DESC LIMIT 10;"
```

Registered clients and their callbacks:

```bash
docker exec -i vepathos-postgres psql -U postgres -d vepathos_app -c "SELECT \"clientId\", \"clientName\", \"redirectUris\", \"createdAt\" FROM \"McpOAuthClient\" ORDER BY \"createdAt\" DESC LIMIT 10;"
```

None of these select tokens or hashes.

### Detecting a connect loop

A user who reconnects twice within minutes is stuck; nobody reconnects when the first attempt worked.
This lists consecutive exchanged codes for the same grant less than 15 minutes apart. It compares each
code with the previous one, so a wide window does not hide a loop.

```bash
docker exec -i vepathos-postgres psql -U postgres -d vepathos_app -c "SELECT c.\"clientName\", a.prev AS previous_exchange, a.\"consumedAt\" AS reconnect FROM (SELECT x.\"grantId\", x.\"consumedAt\", lag(x.\"consumedAt\") OVER (PARTITION BY x.\"grantId\" ORDER BY x.\"consumedAt\") AS prev FROM \"McpOAuthAuthCode\" x WHERE x.\"consumedAt\" > now() - interval '1 hour') a JOIN \"McpOAuthGrant\" g ON g.id = a.\"grantId\" JOIN \"McpOAuthClient\" c ON c.\"clientId\" = g.\"clientId\" WHERE a.prev IS NOT NULL AND a.\"consumedAt\" - a.prev < interval '15 minutes' ORDER BY a.\"consumedAt\" DESC;"
```

Scheduled: the api-doc cron calls `GET /api/cron/mcp-connect-loops` every 15 minutes
(`src/server/mcp/connect-loops.ts`, same query). Each loop is logged as an `[mcp-connect-loop]` line in the
api-doc log and posted to `MCP_ALERT_WEBHOOK_URL` when set:

```bash
docker logs --since 24h vepathos-api-doc 2>&1 | grep mcp-connect-loop
```

To confirm on production that it catches the 16/09 loop, run the query above with `interval '2 days'`
instead of `'1 hour'`: it must list ChatGPT, 15:33:46 → 15:35:31, the loop described below.

## Known incidents

### ChatGPT shows "Connect" again after authorizing

2026-09-16. Authorizing appeared to succeed and the plugin screen went back to "Connect".

Evidence:

- Two codes issued and exchanged within ~2 s each (15:33:44 → 15:33:46, 15:35:29 → 15:35:31), with
  `redirectUri` `https://chatgpt.com/connector_platform_oauth_redirect`.
- The grant's `lastUsedAt` stayed at 05:50, and the adapter log had no request in the window.
- Same metadata-document client that had worked that morning; not revoked, not duplicated.

Vepathos issued valid tokens; ChatGPT did not use them. **Fix: remove the connector in ChatGPT and add
it again** with `https://mcp.vepathos.com/mcp`.

Ruled out, with the evidence above: the authorization server's metadata, PKCE, the `iss` parameter
(always sent, from the same value as the metadata `issuer`), the token endpoint's content type, and
refresh tokens (always issued, whatever the scope). Do not change the authorization server for this
symptom.

### The client reads data but never optimizes

2026-09-16. ChatGPT called `get_account`, `list_fleet` and the geocode tools, then nothing ran.

Signature: an `optimize_routes` line with `optimization_id: null`, `status: null`, ~100 ms,
(before 0.9.0 that tool was `optimize_delivery_routes`; older logs carry the old name)
and no `get_optimization_result` after it. That is a preflight: with `MCP_CONFIRM_BEFORE_OPTIMIZE`
on (the default), a call without `confirmed=true` returns a summary and charges nothing. ChatGPT sent
`confirmed: false` explicitly and never made the second call, because a preflight answers as a success
and does not look unfinished. It does chain calls that look unfinished: it polled
`get_optimization_result` on its own once the gate was off.

Fix: `MCP_CONFIRM_BEFORE_OPTIMIZE=false` in `~/vepathos-deploy/vepathos-mcp/.env`, then recreate the
container ([Configuration](#configuration-that-must-not-change-casually)). Verify with an optimize line
that has a non-null `optimization_id`.

On builds from before the description followed the flag, turning it off left the tool description
telling clients that a call with `confirmed=false` charges nothing, and ChatGPT narrated a real, charged
optimization of 1005 stops as if it were still preparing it. Since that fix, the description, the server
instructions and the input schema follow the flag. Check which one is live:

```bash
docker exec vepathos-mcp-vepathos-mcp-1 python -c "from vepathos_mcp.tools import descriptions as d; print('confirmed' in d.optimize_description(confirm_before_optimize=False))"
```

`False` means the running build has the fix. On an older build the command fails, because the function
does not exist there. Either way, trust `stops_remaining_this_period` or `get_account` over the narration.

### Auth.js `CallbackRouteError: unexpected "iss"`

Seen in api-doc on 2026-09-16 with `"provider": "github"` and `"expected": "https://authjs.dev"`.

`https://authjs.dev` is Auth.js's placeholder issuer for non-OIDC providers such as GitHub, not a
misconfigured variable. The error means a request carrying an `iss` parameter reached
`/api/auth/callback/github`. GitHub never sends `iss`; Vepathos's authorization server adds it to every
authorization redirect. Cause not yet found. It does not affect users who sign in with Google, and it
was not the cause of the ChatGPT connect loop.

### A browser client cannot read the 401

Fixed in `c7dcec9`. Before it, the 401 carried no CORS headers, so a browser-based client could not
read `WWW-Authenticate`. The live 401 now includes `access-control-expose-headers: WWW-Authenticate`.

## Checking the endpoint by hand

A 401 without a credential is the correct answer: `WWW-Authenticate` points the client at the resource
metadata. Quote the arguments; unquoted, the shell splits the JSON into extra URLs:

```bash
curl -i -sS -X POST https://mcp.vepathos.com/mcp -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"debug","version":"1.0"}}}'
```

With a developer credential, without leaving it in shell history. The credential is
`vpt_<24 hex>:vpt_sk_<test|live>_<48 hex>` (89 characters; 93 with `vpt_mcp_`), created on production:

```bash
read -rs VPT && curl -sS -X POST https://mcp.vepathos.com/mcp -H "Authorization: Bearer $VPT" -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}'; unset VPT
```

An answer identical to the unauthenticated 401 means the credential did not match that shape.

The authorization server's metadata lives on `api.vepathos.com`, not on the MCP host:

```bash
curl -sS https://api.vepathos.com/.well-known/oauth-authorization-server
```

## Configuration that must not change casually

- **`OAUTH_SCOPE`** is the scope every token must carry (verifier). **`OAUTH_ADVERTISED_SCOPES`**
  (optional) is what Protected Resource Metadata announces; when unset it equals `OAUTH_SCOPE`.
  Setting `OAUTH_SCOPE` to `optimize offline_access` makes the verifier look for that whole string as
  one scope and rejects every OAuth token — ChatGPT, Claude and Codex at once. To announce multiple
  scopes without changing the required one, set `OAUTH_ADVERTISED_SCOPES` separately.
- **`MCP_CONFIRM_BEFORE_OPTIMIZE`** also changes the tool description, the server instructions and the
  input schema. Clients cache tool definitions: after changing it, a client may need its connector
  removed and added again to see the new ones, though one that keeps sending `confirmed` still works.
  **Do not flip this in prod without [smoke-prompts.md](smoke-prompts.md) on a ChatGPT staging
  connector (T19).**
- **An `.env` edit needs a recreated container.** `docker restart` keeps the old environment. Recreate
  with the deploy command in [deploy-api-prod.md § 4](deploy-api-prod.md#4-adapter-container), adding
  `--force-recreate --no-build`, from a shell where `CADDY_VETH_IP` is exported. Then confirm:

```bash
docker exec vepathos-mcp-vepathos-mcp-1 env | grep MCP_CONFIRM
```

## Connect-loop cron (T21)

Every 15 minutes on api-prod, run the Connect-loop query from this runbook (grants authorized but
unused in the last window — the 16/09 pattern was ~15:33:46 → 15:35:31). Alert the on-call channel
configured in the cron host (set recipient when installing). Validate the query against that
incident window before enabling alerts.

## Engine image provenance (T25)

`route-optimizer-api` may be `api-latest` from CI (`main`) or a local `--build`. Feature work merges
`feature → develop → main`; rebuilds must set `IMAGE_TAG` explicitly and compare km/geometry against
a known good job before promoting. Never merge abandoned local `main` commits such as `0560e95`
(May) into production `main`.

## Neighbouring services on the same host

`route-optimizer-api` runs an image tagged `api-latest`, but its compose file has both `image:` and
`build:`, so `up --build` on the host builds from the local checkout and gives the result that tag.
CI publishes `api-latest` only from `main`. Before any `docker compose pull` there, check where the
running image came from; an empty label means a local build, and pulling would replace it with `main`:

```bash
docker inspect route-optimizer-api --format '{{index .Config.Labels "org.opencontainers.image.revision"}}'
```

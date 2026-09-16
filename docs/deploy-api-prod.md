# Runbook — first production MCP on api-prod (2026-09-14)

This is the deploy that actually shipped, including mistakes we hit. Follow it in order.
Do not invent a second Smart Import MCP or rebuild RouteHub / SI for this channel.

**Status after this run:** `https://mcp.vepathos.com` is live. `/health` 200, `/ready`
`core: ok` + `signing_keys: ok`, unauthenticated `POST /mcp` → 401 + PRM, authenticated
`tools/list` returns the six tools. Geocode/optimize smoke and Claude directory are **not**
done. Registry / directory still need an explicit OK (`docs/publication-checklist.md`).

---

## What this is (and is not)

```
Internet
  → Caddy :443
       ├─ api.vepathos.com     → vepathos-api-doc:3000
       └─ mcp.vepathos.com     → vepathos-mcp:8080
                                    │ HTTPS (hairpin-fixed via extra_hosts)
                                    └─ https://api.vepathos.com/api/mcp/v1
                                         └─ SI http://vepathos-smart-import:8100  (Core only; same Docker net)
                                         └─ optimizer :8000
```

| Service | This deploy? |
|---|---|
| `vepathos-api-doc` (Core + OAuth AS + MCP channel) | **Yes** — rebuild from GitHub `develop` |
| `vepathos-mcp` (adapter) | **Yes** — new container |
| Caddy site `mcp.vepathos.com` | **Yes** — block added |
| DNS `A` `mcp` | **Yes** — GoDaddy |
| Smart Import | **No** — already healthy (`runtime-2026.09.07`) |
| RouteHub / webclient / optimizer | **No** |
| SI stdio `smart-import-mcp` | **No** — discarded as a product |

Agents never see `:8100`. `SMART_IMPORT_URL` exists only on **api-doc**.

---

## Host map

| Item | Value |
|---|---|
| SSH | `deploy@` api-prod (`178.105.42.199`). User **`deploy` has no sudo** — never `/etc/…` |
| DNS | GoDaddy, NS `ns53`/`ns54.domaincontrol.com` |
| Docker network | **`vepathos-net`** (Caddy, api-doc, adapter). Not `vepathos-edge` |
| Caddyfile on disk | `/home/deploy/vepathos-deploy/vepathos-router-client/deploy/hetzner/vm-api/Caddyfile` |
| api-doc checkout | `~/vepathos-deploy/vepathos-api-doc` |
| api-doc compose | `~/vepathos-deploy/vepathos-router-client/deploy/hetzner/vm-api/05-api-doc.yml` |
| api-doc env_file | `~/vepathos-deploy/vepathos-api-doc/.env` |
| adapter checkout | `~/vepathos-deploy/vepathos-mcp` (`develop`) |
| adapter secrets | `~/vepathos-deploy/vepathos-mcp/.env` (gitignored, `chmod 600`) |
| adapter container | `vepathos-mcp-vepathos-mcp-1` |

Confirm Caddyfile mount:

```bash
docker inspect vepathos-caddy --format '{{range .Mounts}}{{println .Source}}{{end}}'
```

---

## 0. Do not skip this order

1. Pull/fix **api-doc** `develop` so `next build` typechecks. Deploy Core with **`MCP_CHANNEL_ENABLED=false`**.
2. Confirm login on `https://api.vepathos.com`.
3. DNS `mcp.vepathos.com` → `178.105.42.199`. Wait for `dig`.
4. Adapter `.env` + compose on `vepathos-net`. **Do not** enable the channel yet.
5. Caddy block + Let's Encrypt. `https://mcp.vepathos.com/health` → 200.
6. Hairpin fix (`CADDY_VETH_IP`). JWKS 200 from inside the adapter container.
7. `MCP_CHANNEL_ENABLED=true` on api-doc, recreate **without** `--build`.
8. `/ready` → `core: ok`. Then authenticated `tools/list` from a **laptop**, not the VM.

---

## 1. Core (api-doc) — code and build

Checkout: `~/vepathos-deploy/vepathos-api-doc`, branch `develop`.

`next build` in Docker failed three times on GitHub `develop`. Fixes that had to land **before**
the image would build (do not mix Shopify WIP):

| Commit (api-doc) | Failure |
|---|---|
| RapidAPI reservation union | `reservation.remaining` after MCP added `trial_taken` |
| `src/server/mcp/geocode.ts` | `created.message` after `!ok \|\| !body?.job_id` |
| `stripe-provider.ts` | MCP `metadata` union → Stripe v20 picked `RequestOptions` |

Usual rebuild (this VM; **no** `--force-recreate` unless you only changed env):

```bash
cd ~/vepathos-deploy/vepathos-router-client/deploy/hetzner/vm-api
docker compose -f 05-api-doc.yml up -d --build api-doc
```

After a good build: `docker image prune -f` (dangling only, not `prune -a`).

### 1.1 Env on api-doc (runtime, `~/vepathos-deploy/vepathos-api-doc/.env`)

Add (do not paste secrets into git or chat):

```
MCP_CHANNEL_ENABLED=false
MCP_SERVICE_KEYS=<openssl rand -hex 32>
MCP_OAUTH_ISSUER=https://api.vepathos.com
MCP_RESOURCE_URI=https://mcp.vepathos.com
SMART_IMPORT_URL=http://vepathos-smart-import:8100
MCP_FULL_TRIAL_ENABLED=false
```

Notes:

- `SMART_IMPORT_URL` was **never** on api-doc before. Web geocode goes RouteHub → SI. MCP is the
  first Core caller. Use the **container DNS** on `vepathos-net`
  (`http://vepathos-smart-import:8100`), not the host publish `10.0.0.2:8100` (from api-doc that
  address times out — same hairpin class as `api.vepathos.com`). Not
  `ROUTEHUB_SMART_IMPORT_URL` and not `127.0.0.1` (that is the Next container, not SI).
  Join SI to `vepathos-net` if it is not already there (`docker network connect`).
- `MCP_OAUTH_ISSUER` is also a **build-time** value via `next.config.mjs`. Keep it in the env
  compose reads at `build`.
- Do **not** change prod `AUTH_URL` / Google callbacks.
- Leave trial **false** for the first smoke.
- `MCP_SERVICE_KEYS` (Core, comma-list) **value** = `VEPATHOS_MCP_SERVICE_KEY` (adapter, one key).
  Names stay different.

From inside Core, SI must answer:

```bash
docker exec vepathos-api-doc wget -qO- --timeout=5 http://vepathos-smart-import:8100/health
```

### 1.2 Migrations

The api-doc entrypoint runs `prisma migrate deploy`. On first MCP deploy this applied, among
others:

- `20260913210000_mcp_channel_ledger`
- `20260914010000_mcp_oauth_grants`

plus the plan-library migrations that were already on `develop`. **Never** `migrate reset`.

Logs: `All migrations have been successfully applied` then `Ready`.

### 1.3 Login check

Open `https://api.vepathos.com` and sign in. If that breaks, stop — do not add Caddy/MCP.

---

## 2. DNS (GoDaddy)

vepathos.com → DNS → Add:

| Type | Name | Value | TTL |
|---|---|---|---|
| A | `mcp` | `178.105.42.199` | 600 |

Same IP as `api.vepathos.com`. No CNAME to `api`. No HTTPS at GoDaddy.

```bash
dig +short mcp.vepathos.com A
# 178.105.42.199
```

Do not reload Caddy with the new host until this answers (Let's Encrypt HTTP-01).

---

## 3. Adapter secrets

`deploy` cannot `mkdir /etc/vepathos-mcp`. Use the repo `.env` (already gitignored).

```bash
cd ~/vepathos-deploy/vepathos-mcp
nano .env
chmod 600 .env
```

```
VEPATHOS_MCP_SERVICE_KEY=<same hex as MCP_SERVICE_KEYS>
ACCOUNT_HASH_SALT=<different openssl rand -hex 32>
```

- `ACCOUNT_HASH_SALT` is **only** on the adapter. Production refuses the default
  `vepathos-mcp-dev-salt`. Nothing in api-doc validates it.
- Do not copy `.env.local` from a laptop.

`--env-file` on the Compose CLI does **not** override `env_file:` in the YAML. Always:

```bash
export VEPATHOS_MCP_ENV_FILE="$HOME/vepathos-deploy/vepathos-mcp/.env"
```

or the container still looks for `/etc/vepathos-mcp/.env` on older compose files.

---

## 4. Adapter container

Compose project `vepathos-mcp` + overlay `deploy/docker-compose.apiprod.yml`:

- maps internal network name `vepathos-edge` → existing **`vepathos-net`**
- sets `extra_hosts: api.vepathos.com:${CADDY_VETH_IP}`

The hyphen in `vepathos-net` breaks Go templates. Use `index`:

```bash
export CADDY_VETH_IP
CADDY_VETH_IP=$(docker inspect vepathos-caddy --format '{{index .NetworkSettings.Networks "vepathos-net" "IPAddress"}}')
echo "caddy=$CADDY_VETH_IP"
```

If empty:

```bash
docker network inspect vepathos-net --format '{{range .Containers}}{{.Name}} {{.IPv4Address}}{{println}}{{end}}'
```

`CADDY_VETH_IP` must be set or Compose aborts on `${CADDY_VETH_IP:?}`.

```bash
cd ~/vepathos-deploy/vepathos-mcp
git checkout develop
git pull origin develop
# If pull fails: untracked deploy/docker-compose.apiprod.yml — mv it aside, pull, keep the git file.

export VEPATHOS_MCP_ENV_FILE="$HOME/vepathos-deploy/vepathos-mcp/.env"
export CADDY_VETH_IP   # already set

docker compose -p vepathos-mcp \
  -f deploy/docker-compose.prod.yml \
  -f deploy/docker-compose.apiprod.yml \
  --env-file "$VEPATHOS_MCP_ENV_FILE" \
  up -d --build
```

The image is `python:3.12-slim` — **no wget**. Healthcheck uses Python:

```bash
docker inspect vepathos-mcp-vepathos-mcp-1 --format '{{.State.Health.Status}}'
docker exec vepathos-mcp-vepathos-mcp-1 python -c "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8080/health', timeout=2).read().decode())"
docker inspect vepathos-mcp-vepathos-mcp-1 --format '{{json .NetworkSettings.Networks}}'
docker inspect vepathos-mcp-vepathos-mcp-1 --format '{{json .HostConfig.ExtraHosts}}'
```

Expect: `healthy`, JSON `{"status":"ok",…}`, network **`vepathos-net`**, alias `vepathos-mcp`,
`ExtraHosts` like `["api.vepathos.com:172.18.0.2"]` (IP = current Caddy veth).

If Caddy is recreated, its IP may change — export `CADDY_VETH_IP` again and
`up -d --force-recreate --no-build`.

---

## 5. Caddy

Append **only** the `mcp.vepathos.com` block from `deploy/Caddyfile.example`. Do not edit
`vepathos.com` / `api.vepathos.com`.

```bash
nano /home/deploy/vepathos-deploy/vepathos-router-client/deploy/hetzner/vm-api/Caddyfile
docker exec vepathos-caddy caddy validate --config /etc/caddy/Caddyfile
docker exec vepathos-caddy caddy reload --config /etc/caddy/Caddyfile
```

First `curl https://mcp…` can fail TLS (`tlsv1 alert internal error`) for a few seconds while
ACME runs. Logs:

```bash
docker logs vepathos-caddy --tail 80 2>&1 | grep -iE 'mcp|acme|certificate|error|obtain'
```

Want: `certificate obtained successfully` for `mcp.vepathos.com`. Then from a **laptop**:

```bash
curl -fsS https://mcp.vepathos.com/health
# {"status":"ok","version":"0.2.0"}
```

`GET /` and `/favicon.ico` 404 from scanners is fine. The MCP path is `/mcp`.

---

## 6. Hairpin (why `/ready` was 503)

The adapter calls `https://api.vepathos.com` (JWKS + `/api/mcp/v1/health`). That name is this
VM. From a container, `178.105.42.199:443` **times out**. `host-gateway:443` also times out
(published-port DNAT does not apply between bridge peers).

`/ready` returns **503** only if the service key is missing or **JWKS never loaded**. A Core
outage is `degraded` with HTTP 200. We saw:

```json
{"status":"not_ready","dependencies":{"core":"degraded","signing_keys":"degraded"}}
```

and from the adapter:

```text
URLError: timed out   # urlopen https://api.vepathos.com/api/jwks
```

Fix = `extra_hosts` → Caddy’s `vepathos-net` IP (section 4). Then:

```bash
docker exec vepathos-mcp-vepathos-mcp-1 python -c "import urllib.request; print(urllib.request.urlopen('https://api.vepathos.com/api/jwks', timeout=5).status)"
# 200
curl -sS https://mcp.vepathos.com/ready
# {"status":"ready","dependencies":{"core":"ok","signing_keys":"ok"}}
```

`core` stays `degraded` until step 7.

Production settings **forbid** `http://api-doc:3000` (`VEPATHOS_API_BASE_URL` must be `https://`).

---

## 7. Turn the channel on

In `~/vepathos-deploy/vepathos-api-doc/.env`:

```
MCP_CHANNEL_ENABLED=true
```

```bash
cd ~/vepathos-deploy/vepathos-router-client/deploy/hetzner/vm-api
docker compose -f 05-api-doc.yml up -d --force-recreate --no-build api-doc
```

`--force-recreate` is justified here (env only). No SI / RouteHub / adapter rebuild.

---

## 8. Perimeter checks

```bash
curl -sS https://mcp.vepathos.com/ready
curl -sS -D- -o /dev/null -X POST https://mcp.vepathos.com/mcp | head -20
```

Expect 401 and:

```
www-authenticate: Bearer error="invalid_token", … resource_metadata="https://mcp.vepathos.com/.well-known/oauth-protected-resource/mcp"
```

---

## 9. Authenticated smoke (laptop, never the VM)

Do **not** copy developer keys onto api-prod.

### 9.1 Credential

On `https://api.vepathos.com` → dashboard credentials. Create a key (prefer MCP / `mcp:optimize`).
The UI shows **two** values once:

| Field | Prefix |
|---|---|
| Client id | `vpt_` or `vpt_mcp_` |
| Client secret | `vpt_sk_live_` |

Bearer is **both**, colon, no spaces:

```bash
export VEPATHOS_MCP_BEARER='vpt_THE_ID:vpt_sk_live_THE_SECRET'
```

Check **shape** only:

```bash
python3 -c '
import os
b=os.environ.get("VEPATHOS_MCP_BEARER","")
print("parts", len(b.split(":")), "starts_vpt_", b.startswith("vpt_"), "has_sk", ":vpt_sk_" in b)
'
# parts 2  starts_vpt_ True  has_sk True
```

`parts 1` = you exported only the secret (or only the id). That yields
`{"error":"invalid_token","error_description":"Authentication required"}`.

If a live secret was pasted into chat or a ticket, **revoke it** and mint a new pair.

This is **not** `MCP_SERVICE_KEYS`, not RouteHub, not Google.

### 9.2 `initialize` + `tools/list`

From the Mac, same shell as the export:

```bash
curl -sS -X POST https://mcp.vepathos.com/mcp \
  -H "Accept: application/json, text/event-stream" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer ${VEPATHOS_MCP_BEARER}" \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"prod-smoke","version":"0"}}}'

curl -sS -X POST https://mcp.vepathos.com/mcp \
  -H "Accept: application/json, text/event-stream" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer ${VEPATHOS_MCP_BEARER}" \
  -d '{"jsonrpc":"2.0","id":2,"method":"tools/list","params":{}}'
```

Expect SSE `event: message` and tools:

`optimize_delivery_routes`, `get_optimization_result`, `geocode_addresses`, `get_geocode_result`,
`get_account`, `list_fleet`.

Verified 2026-09-14 with a dashboard `vpt_…:vpt_sk_live_…` pair.

### 9.3 Inspector (optional)

**CLI + Bearer** often still starts OAuth because prod sends `resource_metadata`. Symptom:

`Unexpected: auth() returned AUTHORIZED without authorization code`

Use the `curl` above for keys. Inspector **web** (no `--cli`) is a different path: DCR + consent
on `api.vepathos.com`, callback **`127.0.0.1:<port>`**. That loopback is correct for Inspector
on a laptop. End users on Claude.ai redirect to `https://claude.ai/api/mcp/auth_callback`, not
localhost. Only **Allow** if you started Inspector.

---

## 10. Next (not done in this run)

1. `geocode_addresses` (few CABA streets) + `get_geocode_result`.
2. `optimize_delivery_routes` with those coordinates + `get_optimization_result`.
3. Claude **custom** connector — not the directory.
4. Keep `MCP_FULL_TRIAL_ENABLED=false` until you want the first-use trial.
5. Registry / Claude directory / “Add to …” buttons: explicit OK only.

Kill switch: `MCP_CHANNEL_ENABLED=false` + api-doc recreate, no `--build`. Web/REST stay up.

---

## Pitfalls (this night)

| Symptom | Cause | Fix |
|---|---|---|
| api-doc `next build` RapidAPI / geocode / Stripe | Unions on `develop` | Three narrow-type commits; do not commit Shopify WIP |
| `mkdir /etc/vepathos-mcp` denied | `deploy` has no sudo | `~/vepathos-deploy/vepathos-mcp/.env` + `VEPATHOS_MCP_ENV_FILE` |
| Compose: `/etc/vepathos-mcp/.env not found` | YAML `env_file` default | Export `VEPATHOS_MCP_ENV_FILE` |
| `git pull` blocked on `apiprod.yml` | Local untracked overlay | Remove/backup, pull the git file |
| `wget` missing in adapter | slim image | `python -c urllib` |
| inspect template `bad character '-'` | `vepathos-net` in Go template | `index .NetworkSettings.Networks "vepathos-net"` |
| TLS `internal error` then OK | ACME still issuing | Wait for Caddy log; retry from laptop |
| `/ready` 503, JWKS timeout | Hairpin to public A | `CADDY_VETH_IP` = Caddy veth, not `host-gateway` |
| Claude geocode `BACKEND_UNAVAILABLE`, ~30–40 s | api-doc → `10.0.0.2:8100` connect timeout | SI on `vepathos-net`, `SMART_IMPORT_URL=http://vepathos-smart-import:8100`, recreate api-doc `--no-build` |
| `invalid_token` with 60-char bearer | Secret only | `client_id:client_secret` |
| Inspector CLI OAuth error | CLI follows PRM | `curl` + Bearer |
| Consent shows `127.0.0.1` | Inspector local callback | Expected; not prod Claude |

---

## Rollback

- Adapter: previous `vepathos/mcp` tag or prior compose project; no migrations in this repo.
- Channel: `MCP_CHANNEL_ENABLED=false` on api-doc.
- Caddy: delete the `mcp.vepathos.com` site block and reload (cert stays in the volume).
- DNS: remove the `mcp` A record if you want the name gone.

# Deployment — `mcp.vepathos.com`

The first production cut (2026-09-14) is documented end to end in
**[docs/deploy-api-prod.md](deploy-api-prod.md)** — every command, env name, hairpin fix,
Let's Encrypt wait, and the mistakes we actually hit. Use that file to repeat or rollback.
This page is the short operator index.

`vepathos-mcp` is deployed independently from Vepathos Core: its own image, container, compose
project, logs, health checks and DNS record. It can share a host with `api.vepathos.com`.

Smart Import and RouteHub are **not** part of this deploy. Core already calls SI on the private
network (`SMART_IMPORT_URL=http://vepathos-smart-import:8100` on `vepathos-net`).
The host bind `10.0.0.2:8100` is for the host / RouteHub, not for api-doc. Do not expose SI.

```
Internet ─► edge Caddy (TLS, :443) ─┬─► api.vepathos.com  (api-doc)
                                    └─► mcp.vepathos.com  ─► vepathos-mcp:8080 (1..N stateless replicas)
                                                              │ HTTPS
                                                              └─► https://api.vepathos.com/api/mcp/v1
```

## Current host (api-prod)

| Item | Value |
|---|---|
| VM | `deploy@` api-prod (`178.105.42.199`) |
| DNS | GoDaddy (`ns53`/`ns54.domaincontrol.com`) |
| Edge | `vepathos-caddy` on Docker network **`vepathos-net`** |
| Caddyfile | `~/vepathos-deploy/vepathos-router-client/deploy/hetzner/vm-api/Caddyfile` |
| Adapter checkout | `~/vepathos-deploy/vepathos-mcp` (branch `develop`) |
| Adapter secrets | `~/vepathos-deploy/vepathos-mcp/.env` (mode 600; `deploy` has no sudo for `/etc`) |
| Core | `vepathos-api-doc` — channel **on** after adapter `/health` 200 (2026-09-14) |

`deploy/docker-compose.prod.yml` talks about a network named `vepathos-edge`. On this VM that
network does not exist. Use `deploy/docker-compose.apiprod.yml` so the same compose key binds to
`vepathos-net`. The overlay must set `extra_hosts` so `api.vepathos.com` is Caddy's address on
that network (`CADDY_VETH_IP`). The public A record and `host-gateway:443` both time out from
the adapter container. Do not create a second network. Do not publish `:8080` on a public NIC.

## Order (do not skip)

1. Core (`api-doc`) already on `develop`, MCP migrations applied, `MCP_CHANNEL_ENABLED=false`.
2. DNS `A` for `mcp.vepathos.com` → `178.105.42.199`. Wait until `dig +short mcp.vepathos.com A`
   returns that address **before** reloading Caddy (Let's Encrypt).
3. Adapter secrets + compose up (channel still false).
4. `https://mcp.vepathos.com/health` → 200.
5. Then `MCP_CHANNEL_ENABLED=true` on api-doc and recreate **without** `--build`.
6. `https://mcp.vepathos.com/ready` → `core: ok`. Then Inspector / a custom Claude connector.
7. Official registry and Claude directory need a separate explicit OK (`docs/publication-checklist.md`).

## 1. DNS (GoDaddy)

Domain **vepathos.com** → DNS → Add record:

| Type | Name | Value | TTL |
|---|---|---|---|
| A | `mcp` | `178.105.42.199` | 600 s |

Same address as `api.vepathos.com`. No CNAME to `api`. No HTTPS at the registrar; Caddy terminates
TLS. Apex / `www` stay unchanged.

```bash
dig +short mcp.vepathos.com A
# expect: 178.105.42.199
```

## 2. Adapter secrets

The `deploy` user cannot write `/etc`. Put secrets in the checkout (gitignored as `.env`):

```bash
cd ~/vepathos-deploy/vepathos-mcp
nano .env
chmod 600 .env
```

Only secrets. Production URLs, `AUTH_MODES` and `ENVIRONMENT` come from
`deploy/docker-compose.prod.yml`. Production refuses `http://` URLs, `service` auth, an empty
service key and the default salt.

```
VEPATHOS_MCP_SERVICE_KEY=<same hex as MCP_SERVICE_KEYS on api-doc>
ACCOUNT_HASH_SALT=<openssl rand -hex 32, different from the service key>
```

Optional: `METRICS_BEARER_TOKEN` if `/metrics` is scraped from outside the host network. Never copy
a developer `.env` or `.env.local` onto this path. To use another file,
`VEPATHOS_MCP_ENV_FILE=/absolute/path`.

## 3. Adapter container

```bash
cd ~/vepathos-deploy/vepathos-mcp
git checkout develop
git pull

export VEPATHOS_MCP_ENV_FILE="$PWD/.env"
export CADDY_VETH_IP
CADDY_VETH_IP=$(docker inspect vepathos-caddy --format '{{index .NetworkSettings.Networks "vepathos-net" "IPAddress"}}')

# --env-file alone does not override env_file: in the YAML. Export VEPATHOS_MCP_ENV_FILE.
docker compose -p vepathos-mcp \
  -f deploy/docker-compose.prod.yml \
  -f deploy/docker-compose.apiprod.yml \
  --env-file "$VEPATHOS_MCP_ENV_FILE" \
  up -d --build
```

Compose names the container `vepathos-mcp-vepathos-mcp-1` unless `container_name` is set.

```bash
docker exec vepathos-mcp-vepathos-mcp-1 python -c "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8080/health', timeout=2).read().decode())"
```

Expect HTTP 200. After a successful rebuild, `docker image prune -f` removes dangling images only.

## 4. Caddy site block

Confirm the mounted file:

```bash
docker inspect vepathos-caddy --format '{{range .Mounts}}{{println .Source "->" .Destination}}{{end}}'
```

Append the block in `deploy/Caddyfile.example` (do not edit the `vepathos.com` / `api.vepathos.com`
sites). Reload only after DNS answers:

```bash
docker exec vepathos-caddy caddy validate --config /etc/caddy/Caddyfile
docker exec vepathos-caddy caddy reload --config /etc/caddy/Caddyfile
curl -fsS https://mcp.vepathos.com/health
```

The proxy streams SSE (`flush_interval -1`), limits bodies to 8 MB and allows 75 s for response
headers. Tools answer within about 55 s; long optimizations continue in Core.

`/ready` may report Core down while `MCP_CHANNEL_ENABLED=false`. That is expected.

## 5. Enable the Core channel

On api-doc only, after public `/health` is 200:

```
MCP_CHANNEL_ENABLED=true
```

Recreate **api-doc** without `--build` (env change only). Do not rebuild SI, RouteHub or the
webclient.

## Scaling and rollback

- Stateless: add replicas (`--scale vepathos-mcp=3`) behind the proxy. No sticky sessions.
- Rate limits are per replica. Quota and concurrency are enforced in Core.
- Rollback: previous image tag (`VEPATHOS_MCP_VERSION`). This service has no migrations.
- Kill switch: `MCP_CHANNEL_ENABLED=false` on Core. Web and REST keep running.

## Resource usage

The adapter does I/O only: about 0.1 vCPU and less than 150 MB RAM at low traffic. Optimization
compute stays in the Vepathos engine.

## Verification after the channel is on

1. `curl https://mcp.vepathos.com/ready` → `{"status":"ready"}` with `core: ok`.
2. `curl -X POST https://mcp.vepathos.com/mcp` without a token → `401` with `resource_metadata`.
3. `curl https://mcp.vepathos.com/.well-known/oauth-protected-resource/mcp` → resource and
   authorization server.
4. Authenticated `tools/list` via `curl` + `Authorization: Bearer <client_id>:<client_secret>`
   (dashboard pair). Inspector **CLI** often starts OAuth because of PRM — use curl for keys.
5. Still open: small CABA geocode, then optimize those coordinates; custom Claude connector.
   Directory / registry submissions are step 8 and need an explicit OK.

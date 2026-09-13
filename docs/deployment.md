# Deployment — `mcp.vepathos.com`

`vepathos-mcp` is deployed independently from Vepathos Core: its own image, container, compose
project, logs, health checks and DNS record. It can share a host with `api.vepathos.com`.

```
Internet ─► edge Caddy (TLS, :443) ─┬─► api.vepathos.com  (api-doc)
                                    └─► mcp.vepathos.com  ─► vepathos-mcp:8080 (1..N stateless replicas)
                                                              │ HTTPS
                                                              └─► https://api.vepathos.com/api/mcp/v1
```

## Prerequisites

1. The Core MCP channel is enabled on `api.vepathos.com` (`MCP_CHANNEL_ENABLED=true`) with the service
   key configured on both sides.
2. For public onboarding: the Vepathos OAuth authorization server is live on `api.vepathos.com`.
3. DNS `A`/`AAAA` record `mcp.vepathos.com` → host IP.
4. Docker network shared with the edge proxy: `docker network create vepathos-edge`.

## Configuration

Create `/etc/vepathos-mcp/.env` (mode 600) from `.env.example` with at least:

```
VEPATHOS_MCP_SERVICE_KEY=<random 32+ bytes, same value as Core>
ACCOUNT_HASH_SALT=<random 32+ bytes>
METRICS_BEARER_TOKEN=<random, if metrics are scraped from outside the host network>
```

`deploy/docker-compose.prod.yml` sets the production URLs, `AUTH_MODES=oauth,api_key` and
`ENVIRONMENT=production`. Production mode refuses `http://` URLs, the `service` auth mode, an empty
service key and the default salt.

## Deploy

```bash
docker compose -p vepathos-mcp -f deploy/docker-compose.prod.yml up -d --build
docker compose -p vepathos-mcp -f deploy/docker-compose.prod.yml ps
curl -fsS https://mcp.vepathos.com/health
```

Add the site block from `deploy/Caddyfile.example` to the edge Caddy and reload it. The proxy streams
SSE without buffering (`flush_interval -1`), limits bodies to 8 MB and allows up to 75 s for response
headers. Tools answer within about 55 s; long optimizations continue asynchronously in Core, so no
connection is held open for minutes.

## Scaling and rollback

- Stateless: add replicas (`--scale vepathos-mcp=3`) behind the proxy. No sticky sessions are needed.
- Rate limits are per replica (best effort). Quota and concurrency limits are enforced in Core.
- Rollback: redeploy the previous image tag (`VEPATHOS_MCP_VERSION`). No data migrations live in this
  service.
- Kill switch: disable the channel in Core (`MCP_CHANNEL_ENABLED=false`); tools then return a
  temporary error without affecting web or REST traffic.

## Resource usage

The adapter does I/O only: about 0.1 vCPU and less than 150 MB RAM at low traffic. Optimization compute
stays in the Vepathos engine.

## Verification after deploy

1. `curl https://mcp.vepathos.com/ready` → `{"status":"ready"}` with `core: ok`.
2. `curl -X POST https://mcp.vepathos.com/mcp` without a token → `401` with `resource_metadata`.
3. `curl https://mcp.vepathos.com/.well-known/oauth-protected-resource/mcp` → resource and
   authorization server.
4. MCP Inspector (`--cli … --method tools/list --strict`) with a test account token.
5. Connect a real client (Claude custom connector) with the test account and run the smoke prompts.

#!/usr/bin/env bash
# Deploy the MCP adapter on api-prod and smoke it. Run on the VM after `git pull origin develop`:
#
#   cd ~/vepathos-deploy/vepathos-mcp && git pull origin develop && scripts/deploy-api-prod.sh
#
# Fails loudly (non-zero exit, last log lines) when the container does not come up with this
# checkout's version or the public endpoint does not answer like an MCP resource server. Optional:
#   MCP_SMOKE_BEARER   read with `read -rs MCP_SMOKE_BEARER; export MCP_SMOKE_BEARER` to check tools/list
#   EXPECT_IMPORT_TOOLS=true  when MCP_IMPORT_TOOLS_ENABLED=true in the .env
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "${ROOT}"
ENV_FILE="${VEPATHOS_MCP_ENV_FILE:-$HOME/vepathos-deploy/vepathos-mcp/.env}"
CONTAINER="${MCP_CONTAINER:-vepathos-mcp-vepathos-mcp-1}"

fail() {
  echo "DEPLOY FAILED: $*" >&2
  docker logs --tail 40 "${CONTAINER}" 2>&1 | sed 's/^/  | /' >&2 || true
  exit 1
}

[[ -f "${ENV_FILE}" ]] || fail "env file ${ENV_FILE} not found"
export VEPATHOS_MCP_ENV_FILE="${ENV_FILE}"
CADDY_VETH_IP="$(docker inspect vepathos-caddy --format '{{index .NetworkSettings.Networks "vepathos-net" "IPAddress"}}' 2>/dev/null || true)"
[[ -n "${CADDY_VETH_IP}" ]] || fail "could not read vepathos-caddy's address on vepathos-net"
export CADDY_VETH_IP

echo "== build and start (commit $(git rev-parse --short HEAD)) =="
docker compose -p vepathos-mcp -f deploy/docker-compose.prod.yml -f deploy/docker-compose.apiprod.yml \
  --env-file "${ENV_FILE}" up -d --build

echo "== wait for the public /health =="
for _ in $(seq 1 30); do
  if curl -fsS "${ADAPTER_URL:-https://mcp.vepathos.com}/health" >/dev/null 2>&1; then break; fi
  sleep 2
done

echo "== smoke =="
"${ROOT}/scripts/smoke-prod.sh" || fail "smoke-prod.sh failed"
echo "DEPLOY OK: $(git rev-parse --short HEAD)"

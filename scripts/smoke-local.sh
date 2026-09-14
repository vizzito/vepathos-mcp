#!/usr/bin/env bash
# Discovery + auth-challenge smoke against the real local stack (adapter :8080, Core :3001).
# Does not call optimize: that needs VEPATHOS_MCP_BEARER or scripts/oauth-local.sh.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
if [[ -f "${ROOT}/.env" ]]; then
  set -a
  # shellcheck disable=SC1091
  source "${ROOT}/.env"
  set +a
fi

ADAPTER="${ADAPTER_URL:-http://127.0.0.1:8080}"
CORE="${OAUTH_ISSUER:-http://localhost:3001}"
RESOURCE="${ADAPTER}/mcp"
export OAUTH_ISSUER="${CORE}"

echo "== adapter /health =="
curl -fsS "${ADAPTER}/health"
echo
echo "== adapter /ready =="
curl -fsS "${ADAPTER}/ready"
echo
echo "== Core MCP /health =="
curl -fsS -H "X-Vepathos-MCP-Service-Key: ${VEPATHOS_MCP_SERVICE_KEY:?set VEPATHOS_MCP_SERVICE_KEY}" \
  "${CORE}/api/mcp/v1/health"
echo

echo "== 401 + resource_metadata =="
www="$(curl -sS -D - -o /dev/null -X POST "${RESOURCE}" \
  -H "Accept: application/json, text/event-stream" \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"smoke","version":"0"}}}')"
echo "${www}" | grep -i www-authenticate
echo "${www}" | grep -qi 'resource_metadata='

echo "== PRM =="
curl -fsS "${ADAPTER}/.well-known/oauth-protected-resource/mcp" | python3 -c '
import json, os, sys
d = json.load(sys.stdin)
assert d.get("resource", "").endswith("/mcp")
assert os.environ.get("OAUTH_ISSUER", "http://localhost:3001") in (d.get("authorization_servers") or [])
print("resource", d["resource"])
'

echo "== AS + JWKS =="
curl -fsS "${CORE}/.well-known/oauth-authorization-server" | python3 -c '
import json, sys
d = json.load(sys.stdin)
assert d.get("code_challenge_methods_supported") == ["S256"]
assert d.get("client_id_metadata_document_supported") is True
assert "none" in (d.get("token_endpoint_auth_methods_supported") or [])
assert "optimize" in (d.get("scopes_supported") or [])
print("issuer", d.get("issuer"))
'
curl -fsS "${CORE}/api/jwks" | python3 -c 'import json,sys; d=json.load(sys.stdin); assert d.get("keys"); print("jwks", len(d["keys"]))'

echo "== DCR =="
curl -fsS -X POST "${CORE}/oauth/register" \
  -H "Content-Type: application/json" \
  -d '{"client_name":"vepathos-smoke-local","redirect_uris":["http://127.0.0.1:3457/callback"],"token_endpoint_auth_method":"none"}' \
  | python3 -c 'import json,sys; d=json.load(sys.stdin); assert str(d.get("client_id","")).startswith("mcp_dcr_"); print("registered", d["client_id"][:16])'

echo "smoke-local: discovery ok"

#!/usr/bin/env bash
# Inspector CLI against the local adapter. Does not open the web UI (that one
# tries OAuth DCR and fails with HTTP 404).
set -euo pipefail

if [[ -z "${VEPATHOS_MCP_BEARER:-}" ]]; then
  echo "Set VEPATHOS_MCP_BEARER to client_id:client_secret (Vepathos, not Anthropic)." >&2
  echo "  export VEPATHOS_MCP_BEARER='vpt_…:vpt_sk_test_…'" >&2
  exit 1
fi

method="${1:-tools/list}"
shift || true

exec npx -y @modelcontextprotocol/inspector@latest --cli \
  http://127.0.0.1:8080/mcp \
  --transport http \
  --header "Authorization: Bearer ${VEPATHOS_MCP_BEARER}" \
  --method "${method}" \
  --format json \
  "$@"

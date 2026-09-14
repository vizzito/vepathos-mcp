#!/usr/bin/env bash
# Inspector CLI against the local adapter (API key). The web UI can use OAuth DCR
# when AUTH_MODES includes oauth (see scripts/oauth-local.sh).
#
#   ./scripts/inspect.sh tools/list
#   ./scripts/inspect.sh tools/call geocode_addresses '{"addresses":[...],"city":"CABA"}'
#
# Do not pass arrays with --tool-arg key=[...]: Inspector leaves them as strings
# and the tool returns INVALID_INPUT. Use --tool-args-json (this script does).
set -euo pipefail

if [[ -z "${VEPATHOS_MCP_BEARER:-}" ]]; then
  echo "Set VEPATHOS_MCP_BEARER to client_id:client_secret (Vepathos, not Anthropic)." >&2
  echo "  export VEPATHOS_MCP_BEARER='vpt_…:vpt_sk_test_…'" >&2
  exit 1
fi

method="${1:-tools/list}"
shift || true

args=(
  http://localhost:8080/mcp
  --transport http
  --header "Authorization: Bearer ${VEPATHOS_MCP_BEARER}"
  --method "${method}"
  --format json
)

if [[ "${method}" == "tools/call" && "${1:-}" != "" && "${1:-}" != --* ]]; then
  args+=(--tool-name "$1")
  shift
  if [[ "${1:-}" != "" && "${1:-}" != --* ]]; then
    args+=(--tool-args-json "$1")
    shift
  fi
fi

exec npx -y @modelcontextprotocol/inspector@latest --cli "${args[@]}" "$@"

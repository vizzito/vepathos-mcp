#!/usr/bin/env bash
# Post-deploy smoke against a live MCP URL (staging or prod). Discovery + tools/list shape.
# Does not call optimize (needs a real user token). For local discovery see smoke-local.sh.
#
# With MCP_SMOKE_BEARER it prints the tool names the server actually publishes, and can assert them:
#   MCP_SMOKE_BEARER=...  EXPECT_TOOL_COUNT=15  EXPECT_IMPORT_TOOLS=true  EXPECT_CATALOG_TOOLS=true
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ADAPTER="${ADAPTER_URL:-https://mcp.vepathos.com}"
RESOURCE="${ADAPTER%/}/mcp"
# The version this checkout builds, so a deploy that did not pick up the new image fails here.
SOURCE_VERSION="$(sed -n 's/^__version__ = "\(.*\)"$/\1/p' "${ROOT}/src/vepathos_mcp/__init__.py")"
EXPECTED_VERSION_PREFIX="${EXPECTED_VERSION_PREFIX:-${SOURCE_VERSION}}"

echo "== adapter /health =="
health="$(curl -fsS "${ADAPTER%/}/health")"
echo "${health}"
echo "${health}" | grep -q "\"version\":\"${EXPECTED_VERSION_PREFIX}"

echo "== 401 + resource_metadata =="
www="$(curl -sS -D - -o /dev/null -X POST "${RESOURCE}" \
  -H "Accept: application/json, text/event-stream" \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"smoke-prod","version":"0"}}}')"
echo "${www}" | grep -i www-authenticate
echo "${www}" | grep -qi 'resource_metadata='

if [[ -n "${MCP_SMOKE_BEARER:-}" ]]; then
  echo "== tools/list (authenticated) =="
  body="$(curl -fsS -X POST "${RESOURCE}" \
    -H "Authorization: Bearer ${MCP_SMOKE_BEARER}" \
    -H "Accept: application/json, text/event-stream" \
    -H "Content-Type: application/json" \
    -d '{"jsonrpc":"2.0","id":2,"method":"tools/list"}')"
  # What actually went live, by name: a flag left off is the difference between 10 tools and 15,
  # and grepping three names never showed it.
  names="$(printf '%s' "${body}" | tr '{' '\n' | sed -n 's/.*"name"[[:space:]]*:[[:space:]]*"\([a-z_]*\)".*/\1/p' | sort -u)"
  count="$(printf '%s\n' "${names}" | grep -c .)"
  echo "published ${count} tools:"
  printf '  %s\n' ${names}
  echo "${body}" | grep -q 'optimize_routes'
  echo "${body}" | grep -q 'get_optimization_result'
  echo "${body}" | grep -q 'list_plans'
  if [[ "${EXPECT_IMPORT_TOOLS:-false}" == "true" ]]; then
    echo "${body}" | grep -q 'import_deliveries' || { echo "MCP_IMPORT_TOOLS_ENABLED is not on in this deployment" >&2; exit 1; }
  fi
  if [[ "${EXPECT_CATALOG_TOOLS:-false}" == "true" ]]; then
    echo "${body}" | grep -q 'manage_catalog' || { echo "MCP_CATALOG_WRITE_TOOLS_ENABLED is not on in this deployment" >&2; exit 1; }
  fi
  if [[ -n "${EXPECT_TOOL_COUNT:-}" && "${count}" != "${EXPECT_TOOL_COUNT}" ]]; then
    echo "expected ${EXPECT_TOOL_COUNT} tools, the server published ${count}" >&2
    exit 1
  fi
else
  echo "(set MCP_SMOKE_BEARER to also check tools/list)"
fi

echo "OK"

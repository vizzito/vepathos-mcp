#!/usr/bin/env bash
# Local OAuth loop: PRM → AS → JWKS → DCR → consent → token → MCP initialize.
#
# Core must be the process on :3001 (AUTH_URL=http://localhost:3001). Google
# always returns to AUTH_URL; if that is :3000 and nothing is listening, Chrome
# shows "localhost refused to connect".
#
# 1. Core on :3001, adapter on :8080 (this repo).
# 2. Run this script; it opens the authorize URL.
# 3. If you are not signed in: email/password on :3001, or Google (only if
#    Google Cloud has http://localhost:3001/api/auth/callback/google).
# 4. Allow this client? → Allow.
# 5. "Vepathos MCP connected" = callback ok; the terminal finishes initialize.
set -euo pipefail

ADAPTER="${ADAPTER_URL:-http://localhost:8080}"
CORE="${OAUTH_ISSUER:-http://localhost:3001}"
RESOURCE="${ADAPTER}/mcp"
CALLBACK_PORT="${CALLBACK_PORT:-3456}"
REDIRECT_URI="http://127.0.0.1:${CALLBACK_PORT}/callback"

echo "== adapter PRM =="
prm="$(curl -sS "${ADAPTER}/.well-known/oauth-protected-resource/mcp")"
echo "${prm}"
echo "${prm}" | grep -q "${CORE}" || { echo "PRM authorization_servers should list ${CORE}" >&2; exit 1; }

echo
echo "== 401 WWW-Authenticate =="
auth="$(curl -sS -D - -o /dev/null -X POST "${RESOURCE}" -H "Accept: application/json, text/event-stream" \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"oauth-local","version":"0"}}}')"
echo "${auth}" | grep -i www-authenticate

echo
echo "== Core AS + JWKS =="
curl -sS "${CORE}/.well-known/oauth-authorization-server"
echo
curl -sS "${CORE}/api/jwks" | python3 -c 'import json,sys; d=json.load(sys.stdin); assert d.get("keys"); print("jwks keys", len(d["keys"]))'

echo
echo "== DCR =="
registered="$(curl -sS -X POST "${CORE}/oauth/register" \
  -H "Content-Type: application/json" \
  -d "$(python3 -c 'import json,sys; print(json.dumps({"client_name":"MCP Inspector (Vepathos local test)","redirect_uris":[sys.argv[1]],"token_endpoint_auth_method":"none"}))' "${REDIRECT_URI}")")"
echo "${registered}"
client_id="$(python3 -c 'import json,sys; print(json.load(sys.stdin)["client_id"])' <<<"${registered}")"

verifier="$(python3 -c 'import secrets; print(secrets.token_urlsafe(48))')"
challenge="$(python3 -c 'import hashlib,base64,sys; print(base64.urlsafe_b64encode(hashlib.sha256(sys.argv[1].encode()).digest()).rstrip(b"=").decode())' "${verifier}")"
state="$(python3 -c 'import secrets; print(secrets.token_urlsafe(16))')"

authorize="$(python3 - <<PY
import urllib.parse
params = {
    "response_type": "code",
    "client_id": "${client_id}",
    "redirect_uri": "${REDIRECT_URI}",
    "code_challenge": "${challenge}",
    "code_challenge_method": "S256",
    "scope": "optimize offline_access",
    "resource": "${RESOURCE}",
    "state": "${state}",
}
print("${CORE}/oauth/authorize?" + urllib.parse.urlencode(params))
PY
)"

callback_file="$(mktemp)"
python3 - "${CALLBACK_PORT}" "${callback_file}" "${state}" <<'PY' &
import json, sys
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse

port = int(sys.argv[1])
out = sys.argv[2]
expected_state = sys.argv[3]

class Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        query = {key: values[0] for key, values in parse_qs(urlparse(self.path).query).items()}
        ok = query.get("state") == expected_state and "code" in query
        body = (
            b"<html><body style='font-family:sans-serif;padding:2rem'>"
            b"<h1>Vepathos MCP connected</h1>"
            b"<p>You can close this tab and go back to the terminal.</p>"
            b"</body></html>"
            if ok
            else b"<html><body><p>Missing authorization code.</p></body></html>"
        )
        self.send_response(200 if ok else 400)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
        with open(out, "w", encoding="utf-8") as handle:
            json.dump(query, handle)

    def log_message(self, *_args: object) -> None:
        return

server = HTTPServer(("127.0.0.1", port), Handler)
server.timeout = 300
server.handle_request()
PY
listener_pid=$!
trap 'kill "${listener_pid}" 2>/dev/null || true; rm -f "${callback_file}"' EXIT

echo
echo "Listening on ${REDIRECT_URI}"
echo "Open this URL, sign in on :3001, Allow:"
echo "${authorize}"
if command -v open >/dev/null 2>&1; then
  open "${authorize}" >/dev/null 2>&1 || true
fi

echo
echo "Waiting for the browser to come back (5 minutes)…"
wait "${listener_pid}"

if [[ ! -s "${callback_file}" ]]; then
  echo "No callback received." >&2
  exit 1
fi

code="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1])).get("code",""))' "${callback_file}")"
got_state="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1])).get("state",""))' "${callback_file}")"
if [[ -z "${code}" || "${got_state}" != "${state}" ]]; then
  echo "Callback missing code or state mismatch:" >&2
  cat "${callback_file}" >&2
  exit 1
fi

echo
echo "== token =="
token_json="$(curl -sS -X POST "${CORE}/oauth/token" \
  -H "Content-Type: application/x-www-form-urlencoded" \
  --data-urlencode "grant_type=authorization_code" \
  --data-urlencode "client_id=${client_id}" \
  --data-urlencode "code=${code}" \
  --data-urlencode "redirect_uri=${REDIRECT_URI}" \
  --data-urlencode "code_verifier=${verifier}")"
echo "${token_json}" | python3 -c 'import json,sys; d=json.load(sys.stdin); print("token_type", d.get("token_type"), "expires_in", d.get("expires_in"), "scope", d.get("scope"), "has_refresh", bool(d.get("refresh_token")))'
access_token="$(python3 -c 'import json,sys; print(json.load(sys.stdin)["access_token"])' <<<"${token_json}")"

echo
echo "== MCP initialize with JWT =="
curl -sS -X POST "${RESOURCE}" \
  -H "Accept: application/json, text/event-stream" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer ${access_token}" \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"oauth-local","version":"0"}}}'
echo

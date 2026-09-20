#!/usr/bin/env bash
# Tunnels + .env de mcp / api-doc / router. Un puerto = las vars de ese servicio.
#
#   ./scripts/sync-dev-urls.sh up 8080              # ChatGPT + chat del dashboard (lo usual)
#   ./scripts/sync-dev-urls.sh up 8080 3000 3005    # además UI/Google desde internet
#   ./scripts/sync-dev-urls.sh start --mcp-only     # alias de up 8080
#   ./scripts/sync-dev-urls.sh stores                # pin fixed ngrok store callbacks
#   ./scripts/sync-dev-urls.sh clean
#   ./scripts/sync-dev-urls.sh status
#
# 8080 = MCP (OpenAI/ChatGPT NUNCA localhost). 3000 = api-doc (Google/Shopify). 3005 = UI.
# Queda en foreground. Ctrl+C cierra tunnels y vuelve env a localhost.
# Después reiniciá mcp + api-doc + router.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
exec python3 "$ROOT/scripts/sync_dev_urls.py" "$@"

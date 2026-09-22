# Directory listing — paste pack

Use this in Claude, ChatGPT, and the official registry. Do not invent extra claims
(no peer-review, no “best”, no superiority). Paid self-serve is **off**: say Free + contact.

## Short fields

| Field | Text |
|---|---|
| Server name | Vepathos |
| Tagline (≤ 55) | Delivery and fleet routing for AI agents. |
| Registry description (≤ 100) | Multi-vehicle delivery route optimization at scale, with capacity and time-window constraints. |
| Slug (Claude, permanent) | `vepathos` |
| Categories (pick 1–5) | Business, Productivity, Developer tools |
| Documentation URL | `https://vepathos.com/mcp` (publish first; until then GitHub README) |
| Privacy policy URL | `https://vepathos.com/privacy` (add MCP section first) |
| Website | `https://vepathos.com` |
| Support | The public support email already on vepathos.com |
| Icon | Existing Vepathos mark, square PNG/SVG, HTTPS |
| MCP URL | `https://mcp.vepathos.com/mcp` |
| Transport | Streamable HTTP |
| Auth | OAuth 2.0 (DCR + CIMD). Scope `optimize`. Resource `https://mcp.vepathos.com/mcp` |

## Description (≤ 2,000) — Claude / ChatGPT listing

Vepathos plans last-mile delivery routes for a fleet: it assigns each stop to a vehicle and sequences every route from one depot, minimizing total distance while respecting the limits you set (stops per vehicle, weight, volume, time windows).

Connect from Claude or another MCP client, sign in (or create a Free account) at Vepathos, and ask the assistant to geocode addresses and optimize routes. The same account, plan and monthly stop quota apply as the Vepathos web app and REST API. No API keys for that flow. Once connected, inspect the signed-in account (plan, fleet, saved plans) before explaining what the user can do; do not list tool names unless they ask.

Tools: get_account, list_fleet, list_plans and list_automations (the connected account); create_automation (prepares a rule that routes on a schedule, switched off — only the user turns one on); geocode_addresses and get_geocode_result (street addresses → coordinates via Vepathos Smart Import); optimize_routes and get_optimization_result (async VRP for a saved plan by id or for stops with coordinates; poll with optimization_id). Optimize does not invent coordinates — confirm unresolved or low-confidence pins before routing.

This public channel currently offers the Free plan. Jobs the plan cannot run return a contact link, not a payment page. When paid plans are enabled later, payment is handled by Stripe outside the chat; you retry without reconnecting.

Do not put names, phones or emails in stop ids. We do not log access tokens, coordinates or request bodies.

Technical report (not peer-reviewed): https://doi.org/10.5281/zenodo.19859531

## Use cases (Claude portal)

- A dispatcher pastes today’s stops (addresses or lat/lng) and a fleet, and wants routes plus unassigned deliveries.
- An ops lead asks how many vehicles a large order set actually needed.
- A developer connects a headless agent with a dashboard MCP key (`mcp:optimize`) to the same account.

**What users need before connect:** a Vepathos account (created during OAuth if they have none). Free plan is enough for small jobs. No API key for Claude/ChatGPT OAuth.

**Reads / writes:** both. Geocode and optimize write jobs and consume quota; get_* are read-only.

## Company

| Field | Text |
|---|---|
| Company | Vepathos |
| Website | https://vepathos.com |
| Data handling | First-party API (Vepathos Core + Smart Import). Not a third-party proxy. No personal health data. No sponsored content. |

## Test account instructions (reviewer)

Replace the placeholders. Do not put live secrets in git.

1. Open the connector → Connect → sign in at https://api.vepathos.com with:
   - Email: `<reviewer-test@…>`
   - Password: `<give in the portal only>`
   - Or “Continue with Google” using the mailbox you share with the reviewer.
2. Click Allow on the consent screen.
3. In chat, run the three prompts below. If a job exceeds Free, the tool returns a contact URL — that is expected while paid plans are off.

## Starter / review prompts

**Positive (5)**

1. Geocode these stops in Buenos Aires and tell me which pins I should review before routing: (2–3 real CABA streets + a depot lat/lng).
2. Optimize those coordinates with one van from the depot. Summarize vehicles used, distance and any unassigned stops.
3. Show the stop order for the first route with estimated arrivals.
4. I already have latitudes and longitudes (paste 10). Build routes for 2 vans, max 8 stops each.
5. What is the status of optimization `<id from prompt 2>`?

**Negative (3)**

1. Optimize these street addresses without geocoding first — the assistant must refuse to invent coordinates.
2. Submit a job far above Free limits (e.g. 3,000 stops with time windows) — expect `PLAN_UPGRADE_REQUIRED` and a **contact** link, not a paywall.
3. Call get_optimization_result with a fake id — expect a clear not-found / failed status, not a crash.

## Manual install (not a directory)

```text
Claude.ai  → Settings → Connectors → Add custom → https://mcp.vepathos.com/mcp
Claude Code → claude mcp add --transport http vepathos https://mcp.vepathos.com/mcp
Cursor     → MCP url: https://mcp.vepathos.com/mcp
```

Add “Add to Claude / Cursor / …” README buttons only after that client’s flow is verified.

# Vepathos MCP

**Large-scale delivery and fleet optimization for AI agents.**

Vepathos solves vehicle routing problems (VRP) for last-mile fleets: it assigns stops to vehicles
and sequences each route from one depot, at scales from dozens to thousands of stops.

This repository is the remote MCP adapter (`mcp.vepathos.com`). It is not a product of its own.
Web, REST and MCP share the same Vepathos account, plan, features, limits and monthly stop quota.

| Tool | What it does |
|---|---|
| `geocode_addresses` | Turn street addresses into coordinates via Vepathos Smart Import. |
| `get_geocode_result` | Read geocode status and pins. |
| `optimize_delivery_routes` | Submit an asynchronous fleet optimization (VRP). |
| `get_optimization_result` | Read status, a compact summary, stop sequences or unassigned ids. |
| `get_account` | Read which Vepathos account is connected, its plan limits and period usage. |
| `list_fleet` | Read the account's own vehicles and fleets, ready to pass to an optimization. |

There is no cancel tool. A submitted optimization runs to completion. Street addresses must go
through `geocode_addresses` first; `optimize_delivery_routes` does not invent coordinates.

## Status (2026-09-14)

Production is live at `https://mcp.vepathos.com/mcp` (`/ready` ok, authenticated `tools/list`
ok). Claude and other directories do **not** list Vepathos yet — a user must add a custom
connector with that URL. Publication order: [docs/publish-marketplaces.md](docs/publish-marketplaces.md).

Do not add "Add to Claude / Cursor / …" buttons until each flow is verified end to end.

Paid self-serve is **off**. Public listings must say **Free + contact**. Jobs the plan cannot
run return `contact_url`, not Stripe Checkout.

- Local / CI: this server + a **fake Core** (test double; it does not route or geocode for real).
- Local real: `vepathos-api-doc` MCP channel + optimizer + Smart Import worker.

## Connect (production target)

```
Add Vepathos → Connect → Sign in / Sign up → Authorize
```

1. Discover Vepathos from Claude or another MCP client.
2. Connect. The client signs in (or creates a Free / Duck account) at `api.vepathos.com`.
3. Authorize the client to optimize routes with that account.
4. Call `optimize_delivery_routes`. If the plan cannot run the request, the tool returns
   `PLAN_UPGRADE_REQUIRED` with `upgrade_url` (when paid plans are on) or `contact_url`
   (Free-only, until Stripe is configured). Payment, when enabled, is handled entirely by
   Stripe. Retry without reconnecting after the account can run the job.

No API keys and no JSON config for that flow. See [docs/onboarding.md](docs/onboarding.md).

### Developers and headless agents

Create a Vepathos dashboard credential with scope `mcp:optimize` and send

`Authorization: Bearer <client_id>:<client_secret>`

to `https://mcp.vepathos.com/mcp`. Usage counts against the same account plan as the web app and
the REST API.

## Example (3,200 deliveries)

```json
{
  "depot": { "latitude": 40.7128, "longitude": -74.0060 },
  "vehicles": [
    { "vehicle_id": "van", "count": 35, "max_weight_kg": 900, "max_volume_m3": 8.0 }
  ],
  "stops": [
    {
      "stop_id": "ORD-10045",
      "latitude": 40.7306,
      "longitude": -73.9352,
      "weight_kg": 18.5,
      "volume_m3": 0.04,
      "time_window": { "start": "09:00", "end": "12:00" }
    }
  ],
  "schedule": {
    "date": "2026-09-14",
    "route_start_time": "07:30",
    "time_zone": "America/New_York",
    "service_time_minutes": 4
  }
}
```

Every stop needs latitude and longitude. Addresses are not geocoded. Weight, volume and time
windows are optional; if you set a capacity on any vehicle, every vehicle and every stop must
include that field. Time windows require `schedule.route_start_time`.

Results stay available for 24 hours. `detail=summary` is compact (totals and a page of routes).
`detail=stops` returns ordered `stop_id` + arrival time, without echoing coordinates.

Full schemas and errors: [docs/tools.md](docs/tools.md).

## Run locally

Requires Python 3.12+.

```bash
python3.12 -m venv .venv && .venv/bin/pip install -e ".[dev]"
cp .env.example .env
docker compose up --build
```

The MCP endpoint is `http://127.0.0.1:8080/mcp` with
`Authorization: Bearer dev-bearer-token-change-me`. That compose stack talks to the fake Core, not
the optimizer.

```bash
.venv/bin/pytest -m "not integration"
.venv/bin/mypy
.venv/bin/ruff check src tests devtools
```

MCP Inspector and the real local-stack integration (after the Core channel lands):
[docs/testing.md](docs/testing.md).

## Privacy

The tools accept coordinates, optional weight/volume/time windows and your own stop and vehicle
ids. They do not accept names, phones, emails or addresses. Results do not echo coordinates.
Logs omit tokens, payloads and coordinates. Hosted results are retained for 24 hours. See
[docs/security.md](docs/security.md) and [SECURITY.md](SECURITY.md).

## Research & benchmarks

The Vepathos last-mile optimizer is described in a public technical report:
[doi:10.5281/zenodo.19859531](https://doi.org/10.5281/zenodo.19859531). That deposit is not a
peer-reviewed publication.

## Docs

| Document | Topic |
|---|---|
| [docs/architecture.md](docs/architecture.md) | ADR: standalone adapter, trust model, auth, trial |
| [docs/core-changes.md](docs/core-changes.md) | Required Core (`vepathos-api-doc`) changes |
| [docs/core-channel-contract.md](docs/core-channel-contract.md) | HTTP contract `/api/mcp/v1` |
| [docs/auth.md](docs/auth.md) | OAuth, API keys, service mode |
| [docs/onboarding.md](docs/onboarding.md) | Connect, signup, upgrade |
| [docs/tools.md](docs/tools.md) | Tool schemas, annotations, errors |
| [docs/async.md](docs/async.md) | `optimization_id` + poll; Tasks later |
| [docs/deployment.md](docs/deployment.md) | Operator index (container, Caddy, health) |
| [docs/deploy-api-prod.md](docs/deploy-api-prod.md) | First prod cut on api-prod (2026-09-14): every step and pitfall |
| [docs/publish-marketplaces.md](docs/publish-marketplaces.md) | Claude, MCP Registry, ChatGPT, Cursor — order and blockers |
| [docs/directory-listing.md](docs/directory-listing.md) | Paste-ready listing copy (Free + contact) |
| [docs/publication-checklist.md](docs/publication-checklist.md) | Registry and directory gates |
| [docs/privacy-mcp.md](docs/privacy-mcp.md) | Draft MCP section for the public privacy policy |
| [docs/public-mcp-page.md](docs/public-mcp-page.md) | Draft copy for vepathos.com/mcp |

## Privacy

Account and logistics data follow the public policy at [vepathos.com/privacy](https://vepathos.com/privacy).
The MCP-specific section (what agents send, 24 h result retention, no payload logs) is drafted in
[docs/privacy-mcp.md](docs/privacy-mcp.md) and must be copied onto that page before directory review.

## License

[Apache License 2.0](LICENSE)

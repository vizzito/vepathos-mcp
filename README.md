# Vepathos MCP

**Large-scale delivery and fleet optimization for AI agents.**

Vepathos solves vehicle routing problems (VRP) for last-mile fleets: it assigns stops to vehicles
and sequences each route from one depot, at scales from dozens to thousands of stops.

This repository is the remote MCP adapter (`mcp.vepathos.com`). It is not a product of its own.
Web, REST and MCP share the same Vepathos account, plan, features, limits and monthly stop quota.

<!-- BEGIN GENERATED: tools-table (vepathos-mcp tools --write-docs) -->
| Tool | What it does | Changes data | Available |
|---|---|---|---|
| `get_account` | Show which Vepathos account this connection uses and what its plan allows. | no | always |
| `optimize_routes` | Plan delivery routes (vehicle routing problem, VRP). | yes | always |
| `list_plans` | List the plans in the connected Vepathos account (the dashboard's plans; every optimization is saved in one). | no | always |
| `get_optimization_result` | Get the status and outcome of a route optimization by its optimization_id. | no | always |
| `import_deliveries` | Import deliveries into Vepathos so their rows never pass through this chat. | yes | `MCP_IMPORT_TOOLS_ENABLED` |
| `get_import_result` | Status and summary of an import_deliveries job. | no | `MCP_IMPORT_TOOLS_ENABLED` |
| `update_import_mapping` | Correct an import's column mapping without re-uploading. | yes | `MCP_IMPORT_TOOLS_ENABLED` |
| `list_datasets` | List imports from this or earlier chats. | no | `MCP_IMPORT_TOOLS_ENABLED` |
| `geocode_addresses` | Turn street addresses into latitude/longitude using Vepathos Smart Import. | yes | always |
| `get_geocode_result` | Get the status and coordinates of a geocode_addresses job. | no | always |
| `list_fleet` | List what the connected Vepathos account has saved. | no | always |
| `manage_catalog` | Add or change one of the account's own things. | yes | `MCP_CATALOG_WRITE_TOOLS_ENABLED` |
| `list_automations` | List the connected Vepathos account's standing rules. | no | always |
| `create_automation` | Prepare a rule that routes deliveries on a schedule. | yes | always |
| `create_optimization_map` | Create a temporary public link to the map of a completed optimization owned by the connected account. | yes | `MCP_MAP_SHARES_ENABLED` |
<!-- END GENERATED: tools-table -->

No tool switches an automation on: `create_automation` always writes it switched off, and only its
owner turns it on in the dashboard, because a rule that is on spends their stops unattended.

There is no cancel tool. A submitted optimization runs to completion. Street addresses must go
through `geocode_addresses` (or, with imports on, `import_deliveries`) first; `optimize_routes` does not
invent coordinates.

## Status

Production runs **0.11.0** at `https://mcp.vepathos.com/mcp` and publishes all **15 tools**: the
import tools, the shareable map and `manage_catalog` are switched on there, so the "Available"
column above describes what the code gates, not what this deployment withholds.

`manage_catalog` saves three kinds of thing: a vehicle, a depot, and a fleet grouping vehicles the
account already has. A fleet's `units` is what it holds standing, never how many go out on one run,
which is `vehicles[].count` on `optimize_routes`. To run with a saved fleet, read it with
`list_fleet` and pass its vehicles: `optimize_routes` takes no `fleet_id`.

Vepathos is not in the official MCP registry yet, so a client has to add it as a custom connector
with that URL. It is listed on [Smithery](https://smithery.ai/servers/@martinvizzolini/vepathos)
and [Glama](https://glama.ai/mcp/servers/vizzito/vepathos-mcp). Both take a snapshot when they
crawl, so a listing can lag a deployment by a day.

Paid self-serve is off. A request the account's plan cannot run answers `PLAN_UPGRADE_REQUIRED`
with a `contact_url`, never a Stripe Checkout link, and the user retries without reconnecting once
the account can run it.

Two ways to run it away from production:

- **Local and CI**: this server against a **fake Core**, a test double that neither routes nor
  geocodes for real. Everything in `tests/` uses it.
- **Local against the real stack**: the `vepathos-api-doc` MCP channel, the optimizer and the Smart
  Import worker.

## Connect (production target)

```
Add Vepathos → Connect → Sign in / Sign up → Authorize
```

1. Discover Vepathos from Claude or another MCP client.
2. Connect. The client signs in (or creates a Free / Duck account) at `api.vepathos.com`.
3. Authorize the client to optimize routes with that account.
4. Call `optimize_routes`. If the plan cannot run the request, the tool returns
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
    "date": "2026-10-01",
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

MCP Inspector and the integration against the real local stack:
[docs/testing.md](docs/testing.md).

## Privacy

The tools accept coordinates, optional weight/volume/time windows and your own stop and vehicle
ids. Street addresses reach Smart Import through `geocode_addresses` and `import_deliveries` and are
not echoed back. Results do not repeat coordinates, logs omit tokens, payloads and coordinates, and
hosted results are kept for 24 hours.

Account and logistics data follow the public policy at
[vepathos.com/privacy](https://vepathos.com/privacy), which carries an MCP section. See also
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
| [docs/tools.md](docs/tools.md) | How the tools behave: plans, confirmation, master data, errors |
| [docs/tools-reference.md](docs/tools-reference.md) | Generated reference: every tool, inputs, annotations, workflows |
| [docs/agent-test-plan.md](docs/agent-test-plan.md) | Every scenario to run with a real agent, from health to a connected store, and the open findings |
| [docs/chatgpt-test-battery.md](docs/chatgpt-test-battery.md) | What only ChatGPT can tell us: the 512-character window, attachments, cached schemas |
| [docs/async.md](docs/async.md) | `optimization_id` + poll; Tasks later |
| [docs/deployment.md](docs/deployment.md) | Operator index (container, Caddy, health) |
| [docs/deploy-api-prod.md](docs/deploy-api-prod.md) | First prod cut on api-prod (2026-09-14): every step and pitfall |
| [docs/publish-marketplaces.md](docs/publish-marketplaces.md) | Claude, MCP Registry, ChatGPT, Cursor — order and blockers |
| [docs/directory-listing.md](docs/directory-listing.md) | Paste-ready listing copy (Free + contact) |
| [docs/publication-checklist.md](docs/publication-checklist.md) | Registry and directory gates |
| [docs/privacy-mcp.md](docs/privacy-mcp.md) | The MCP section of the public privacy policy |
| [docs/public-mcp-page.md](docs/public-mcp-page.md) | The copy behind vepathos.com/mcp |

## License

[Apache License 2.0](LICENSE)

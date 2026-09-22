# Public page copy — vepathos.com/mcp (draft)

Paste this onto the marketing site when you are ready to publish. Do not add “Add to Claude”
buttons until each client flow is verified. No peer-review or superiority claims.

## Tagline (≤ 55 characters)

Delivery and fleet routing for AI agents.

## Intro

Vepathos solves last-mile vehicle routing: it assigns stops to vehicles and sequences each
route from one depot. MCP is one more channel on the **same Vepathos account** as the web
app and the REST API — same plan, same features, same monthly stop quota.

Connect from Claude or another MCP client, sign in (or create a Free / Duck account), and
ask the assistant to optimize deliveries. No API keys for that flow.

## Tools

<!-- BEGIN GENERATED: tools-table (vepathos-mcp tools --write-docs) -->
| Tool | What it does | Changes data | Available |
|---|---|---|---|
| `get_account` | Show which Vepathos account this connection uses and what its plan allows. | no | Always |
| `optimize_routes` | Plan delivery routes (vehicle routing problem, VRP). | yes | Always |
| `list_plans` | List the plans in the connected Vepathos account (the dashboard's plans; every optimization is saved in one). | no | Always |
| `get_optimization_result` | Get the status and outcome of a route optimization by its optimization_id. | no | Always |
| `import_deliveries` | Import deliveries into Vepathos so their rows never pass through this chat: a file, a link or pasted rows, street addresses included (the import geocodes them). | yes | Rolling out (imports) |
| `get_import_result` | Status and summary of an import_deliveries job. | no | Rolling out (imports) |
| `update_import_mapping` | Correct an import's column mapping without re-uploading. | yes | Rolling out (imports) |
| `list_datasets` | List imports from this or earlier chats. | no | Rolling out (imports) |
| `geocode_addresses` | Turn street addresses into latitude/longitude using Vepathos Smart Import. | yes | Always |
| `get_geocode_result` | Get the status and coordinates of a geocode_addresses job. | no | Always |
| `list_fleet` | List what the connected Vepathos account has saved. | no | Always |
| `manage_vehicle` | Add or change a vehicle saved in the user's Vepathos account: master data that stays after this conversation and shows in their dashboard. | yes | Rolling out (saved vehicles and depots) |
| `manage_depot` | Add or change a depot saved in the user's Vepathos account, the place routes start from: master data that stays after this conversation. | yes | Rolling out (saved vehicles and depots) |
| `list_automations` | List the connected Vepathos account's standing rules. | no | Always |
| `create_automation` | Prepare a rule that routes deliveries on a schedule. | yes | Always |
| `create_optimization_map` | Create a temporary public link to the map of a completed optimization owned by the connected account. | yes | Rolling out (shareable maps) |
<!-- END GENERATED: tools-table -->

`optimize_routes` does not invent coordinates. If the geocoder leaves gaps, confirm
them before optimizing. There is no cancel tool: a submitted job runs to completion.

## Limits on Free (current public posture)

Until Stripe is configured, this channel offers the **Free plan only**. Jobs the plan cannot
run return a contact link, not a checkout page. A reviewer who submits a very large job will
be told to contact Vepathos, not to pay.

Free accounts can still run normal Duck-sized optimizations. The first time a request needs
more than 500 stops or a premium constraint the plan lacks (weight, volume or time windows),
and it has at most 2,000 stops, Vepathos may run it once as a full-feature trial without
using the monthly quota.

When paid plans are enabled, payment is handled entirely by Stripe. After you upgrade, go
back to the assistant and retry — you do not need to reconnect.

## What we do not do

- We do not take card numbers inside the assistant.
- We do not log tokens, coordinates or payloads.
- Results are kept for 24 hours.

## Support

Contact: the address already used on vepathos.com (replace with the live support email
before publish). Privacy: link the public policy URL that includes
[docs/privacy-mcp.md](privacy-mcp.md).

## Research

Technical report (not peer-reviewed): https://doi.org/10.5281/zenodo.19859531

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

| Tool | Use it when |
|---|---|
| `geocode_addresses` | You have street addresses and need coordinates |
| `get_geocode_result` | You need to wait for or review those pins |
| `optimize_delivery_routes` | You already have lat/lng (or just finished geocode) |
| `get_optimization_result` | You need status, a summary, stop order or unassigned ids |

`optimize_delivery_routes` does not invent coordinates. If the geocoder leaves gaps, confirm
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

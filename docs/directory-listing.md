# Directory listing, paste pack

Text to paste into Claude, ChatGPT and the official registry. Every line here is meant to survive a
reviewer checking it: no peer-review claims, no "best", no comparison with anyone. Paid self-serve is
**off**, so the listing says Free plus contact.

Re-read the portal's own field list before pasting. Directories rename and reorder their categories,
and the ones below were read from Claude's connector directory on 2026-09-24.

## Short fields

| Field | Text |
|---|---|
| Server name | Vepathos |
| Tagline (≤ 55) | Import orders, route your fleet, at scale. |
| Registry description (≤ 100) | Import orders, manage your fleet, and generate optimized last-mile delivery routes at scale. |
| Slug (Claude, permanent) | `vepathos` |
| Categories (pick 1–5) | Productivity, Commerce & shopping, Travel |
| Documentation URL | `https://vepathos.com/mcp` |
| Privacy policy URL | `https://vepathos.com/privacy` |
| Website | `https://vepathos.com` |
| Support | The public support email on vepathos.com |
| Icon | Existing Vepathos mark, square PNG/SVG, HTTPS |
| MCP URL | `https://mcp.vepathos.com/mcp` |
| Transport | Streamable HTTP |
| Auth | OAuth 2.0 (DCR + CIMD). Scope `optimize`. Resource `https://mcp.vepathos.com/mcp` |

**On the categories.** Claude's directory has no logistics, transport or delivery category, so none of
these is a perfect fit. Productivity is the honest anchor: the server takes a dispatcher's daily work
off their hands. Commerce & shopping is the closest adjacency, because the orders usually come from a
store and the server reads connected Shopify and Mercado Libre accounts. Travel is the only category
that touches vehicles and geography. Developer tools was dropped on purpose: the person using this is
a dispatcher, not a developer, even though the channel is technical.

## Description (≤ 2,000), for the Claude and ChatGPT listings

Vepathos plans last-mile delivery routes for a fleet: bring the orders in from a spreadsheet, a link or the chat itself, and it assigns each stop to a vehicle and sequences every route from one depot, minimizing total distance while respecting the limits you set (stops per vehicle, weight, volume, time windows). Dozens of stops or thousands, anywhere in the world.

Connect from Claude or another MCP client, sign in (or create a Free account) at Vepathos, and ask the assistant to import orders, geocode addresses and optimize routes. The same account, plan and monthly stop quota apply as the Vepathos web app and REST API. No API keys for that flow.

Tools: get_account, list_fleet, list_plans, list_datasets and list_automations (the account); import_deliveries, get_import_result and update_import_mapping (a spreadsheet, a link or pasted rows become a plan, without the rows passing through the chat); geocode_addresses and get_geocode_result (street addresses into coordinates via Vepathos Smart Import); manage_catalog (save a vehicle or a depot in the account); optimize_routes and get_optimization_result (routing for a saved plan, an import or stops with coordinates; poll with optimization_id); create_optimization_map (a 48 hour public link to the result); create_automation (prepares a rule that routes on a schedule, created switched off, so only the user starts it). Optimize never invents coordinates: unresolved and low confidence pins are flagged to confirm first. A rerun of the same plan within 24 hours, with the same stops or fewer, charges nothing.

This public channel currently offers the Free plan. Jobs the plan cannot run return a contact link, not a payment page. When paid plans are enabled later, Stripe handles payment outside the chat, and you retry without reconnecting.

Do not put names, phones or emails in stop ids. We do not log access tokens, coordinates or request bodies.

Technical report (not peer-reviewed): https://doi.org/10.5281/zenodo.19859531

## Use cases (Claude portal)

Each one is a flow that was run end to end against production, not a hypothetical.

- **The spreadsheet that arrives every morning.** A dispatcher drops a file of orders into the chat,
  the assistant imports it, says how many stops and kilos came in and which addresses need review,
  then routes them across the saved fleet and reports what did not fit.
- **Addresses pasted from an email.** A handful of street addresses become pins, and the assistant
  names the ones it is unsure about before anything is routed, instead of guessing a location.
- **Fleet sizing before committing.** "How many vehicles did it actually need?" is answered from the
  run, comparing vehicles used against vehicles available, so nobody sends out trucks that ride empty.
- **Yesterday's plan, today.** A saved plan is rerun with fewer stops or another fleet. Inside 24
  hours that costs nothing, and the assistant says so rather than calling a charged run free.
- **Handing the result to a driver or a client.** A public link that opens the map for 48 hours,
  created only when the user asks for it, with the assistant saying plainly that anyone holding the
  link can see the delivery locations.
- **Setting the account up from the chat.** Saving a vehicle with its capacity, or a depot the routes
  start from, without leaving the conversation and without a run touching the account's master data.

**What users need before connecting:** a Vepathos account, created during OAuth if they have none.
The Free plan is enough for the prompts below. No API key for the Claude or ChatGPT flow.

**Reads and writes:** both. Importing, geocoding and optimizing create jobs and consume quota. Every
`get_*` and `list_*` is read only and consumes none.

## Company

| Field | Text |
|---|---|
| Company | Vepathos |
| Website | https://vepathos.com |
| Data handling | First-party API (Vepathos Core and Smart Import). Not a third-party proxy. No personal health data. No sponsored content. |

## Test account instructions (reviewer)

Replace the placeholders. Never put a live secret in git.

1. Open the connector, click Connect, and sign in at https://api.vepathos.com with:
   - Email: `<reviewer-test@…>`
   - Password: `<share in the portal only>`
   - Or "Continue with Google" using the mailbox shared with the reviewer.
2. Click Allow on the consent screen.
3. Run the prompts below in order. The account is prepared with a saved fleet and at least one depot,
   so nothing has to be created first.

## Review prompts

Written the way a dispatcher actually types, because that is how they were tested. Each one says what
should happen, so a reviewer can tell a wrong answer from a right one.

**1. What am I connected to**

> ¿Qué cuenta tengo conectada, qué flota y qué planes guardados?

Reads the account, the fleet and the plans before answering, and reports the plan name, the stops left
this period, the saved vehicles and depots, and the saved plans. It should not read tool names out.

**2. Addresses into pins, with the doubtful ones named**

> Tengo estas entregas para mañana y no tengo las coordenadas. Geocodificá estas direcciones y
> decime cuáles conviene revisar antes de rutear: Av. Colón 1100, Tandil; Gral. Rodríguez 450,
> Tandil; Av. España 1200, Tandil.

Geocodes them and names the pins whose match is uncertain, with the address the geocoder matched, so
the user can confirm. It must not route anything yet.

**3. The run, with the numbers said first**

> Rutéalas desde el depósito de Tandil con una camioneta, salida 9:00, 5 minutos por entrega.
> Antes de correr decime cuántas paradas me cobra y cuántas me quedan.

Says what it will charge and what remains, waits for a yes, then runs once. Reports vehicles used,
total distance, arrival times and any stop it could not assign.

**4. The spreadsheet, which is the real path at volume**

> Te adjunto el archivo de pedidos de hoy. Importalo y decime cuántas paradas, cuántos kilos y si
> hay direcciones que no resolvió.

Imports the file rather than pasting rows into the chat, reports the counts, and names the columns it
was unsure about instead of guessing them.

**5. Fleet sizing**

> ¿Cuántos vehículos hicieron falta de verdad, de los que tenía disponibles?

Answers from the finished run, comparing vehicles used against vehicles available.

**6. Sharing the result**

> Pasame un link del mapa para mandarle al chofer.

Creates the link only now that it was asked for, and says it lasts 48 hours and that anyone holding it
can see the delivery locations.

**7. Master data, which is not a plan setting**

> Agregame a la cuenta una Sprinter de 1.500 kg y 14 m³.

Asks for confirmation, saves one vehicle, and says it is saved. It must not optimize anything. Asking
twice returns the same vehicle rather than creating a second one.

**Negative prompts, where the right answer is a refusal**

> Usá 25 vehículos para el reparto de mañana.

Nothing is saved to the account. The 25 is a setting of that run, not 25 new vehicles.

> Rutéame estas direcciones directamente, sin geocodificar.

Refuses to invent coordinates and offers to geocode them first.

> Corré una optimización de 3.000 paradas con ventanas horarias.

On the Free plan this returns a plan limit error with a contact link, not a payment page, and the
assistant explains what plan would cover it.

> ¿Cómo salió la optimización `opt_zzzzzzzz`?

A clear "not found", never an invented result.

## Manual install (not a directory)

```text
Claude.ai   → Settings → Connectors → Add custom → https://mcp.vepathos.com/mcp
Claude Code → claude mcp add --transport http vepathos https://mcp.vepathos.com/mcp
Cursor      → MCP url: https://mcp.vepathos.com/mcp
```

Add "Add to Claude / Cursor / …" buttons to the README only after that client's flow is verified end
to end.

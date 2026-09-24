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

Vepathos plans last-mile delivery routes for a fleet: bring the orders in from a spreadsheet, a link or the chat itself, and it assigns each stop to a vehicle and sequences every route from one depot, minimizing total distance while respecting the limits you set (stops per vehicle, weight, volume, time windows). Dozens of stops or thousands, anywhere in the world. One run takes as much as the plan allows: 15,000 stops on Scale, no cap on Enterprise.

Connect from Claude or another MCP client, sign in or create a Free account, and ask the assistant to import orders, geocode addresses and optimize routes. The same account, plan and monthly stop quota apply as the Vepathos web app and REST API. No API keys for that flow.

Tools: get_account, list_fleet, list_plans, list_datasets and list_automations (the account); import_deliveries, get_import_result and update_import_mapping (a spreadsheet, a link or pasted rows become a plan, without the rows passing through the chat); geocode_addresses and get_geocode_result (street addresses into coordinates via Vepathos Smart Import); manage_catalog (save a vehicle or a depot in the account); optimize_routes and get_optimization_result (routing for a saved plan, an import or stops with coordinates; poll with optimization_id); create_optimization_map (a 48 hour public link to the result); create_automation (prepares a rule that routes on a schedule, created switched off, so only the user starts it). Optimize never invents coordinates: unresolved and low confidence pins are flagged to confirm first. A rerun of the same plan within 24 hours, with the same stops or fewer, charges nothing.

This public channel currently offers the Free plan. Jobs the plan cannot run return a contact link, not a payment page. When paid plans are enabled later, Stripe handles payment outside the chat, and you retry without reconnecting.

Do not put names, phones or emails in stop ids. We do not log access tokens, coordinates or request bodies.


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

English, New York, and written the way a dispatcher types rather than as a test script. Each one says
what a right answer looks like, so a reviewer can tell it from a wrong one.

**1. What am I connected to**

> What account am I connected to, what fleet do I have, and what saved plans?

Reads the account, the fleet and the plans before answering, then gives the plan name, the stops left
this period, the saved vehicles and depots, and the saved plans. It should not read tool names out.

**2. Addresses into pins, with the doubtful ones named**

> These are tomorrow's deliveries and I do not have coordinates. Geocode them and tell me which pins
> I should check before routing: 350 5th Ave, New York, NY; 89 E 42nd St, New York, NY; 200 Central
> Park West, New York, NY.

Geocodes them and names the pins whose match is uncertain, quoting the address the geocoder matched,
so the user can confirm. It must not route anything yet.

**3. The run, with the numbers said first**

> Route those from the Brooklyn depot with one van, leaving at 09:00, 5 minutes per delivery. Before
> you run it, tell me how many stops it charges me and how many I have left.

Says what it will charge and what remains, waits for a yes, then runs once. Reports vehicles used,
total distance, arrival times and any stop it could not assign.

**4. The spreadsheet, which is the real path at volume**

> Here is today's order file. Import it and tell me how many stops, how many kilos, and whether any
> address did not resolve.

Imports the file instead of pasting rows into the chat, reports the counts, and names the columns it
was unsure about rather than guessing them.

**5. Fleet sizing**

> Of the vehicles I had available, how many did it actually need?

Answers from the finished run, comparing vehicles used against vehicles available.

**6. Sharing the result**

> Give me a map link I can send to the driver.

Creates the link only now that it was asked for, and says it lasts 48 hours and that anyone holding
it can see the delivery locations.

**7. Master data, which is not a plan setting**

> Add a 1,500 kg, 14 m3 Sprinter to my account.

Asks for confirmation, saves one vehicle, and says it is saved. It must not optimize anything. Asking
again returns the same vehicle rather than creating a second one.

### Long prompts, the way people actually ask

These two are real prompts, not simplified ones. They were written for the Vepathos dashboard chat
and adapted here to what this channel offers: there are no forms or cards over MCP, and the server
creates vehicles and depots but not fleets or drivers, which stay in the dashboard.

**A. Everything at once, with pieces still missing**

> I need to organize and run tomorrow's deliveries. I have 2 rented vans, but I have not given you
> their capacities, the departure point or the orders yet. I want to leave at 09:00, spend 6 minutes
> at each delivery, at most 15 stops per vehicle, and routes of up to 5 hours.
>
> The vans and the depot are for this job only: do not save them to my account. I accept a plan being
> recorded so the run can happen, without keeping it among my saved plans. Do not take data from
> another delivery run, and do not invent capacities, addresses or orders.
>
> Walk me through it here: tell me what you could already set from this message, and ask me only for
> what you genuinely still need in order to produce routes. If I have to send a file, offer to take
> it. Keep everything I answer and carry on by yourself with whatever already has enough data.
>
> When we have it all, show me the full summary and the cost so I can confirm. After I confirm, run
> it and follow through to the real result: routes, assigned, unassigned, vehicles used and the
> longest route. If something stops the run, tell me what is missing and how to fix it without losing
> what is already done.

What a right answer does: sets the departure, the service time, the stop cap and the route length
from this one message instead of asking for them again; asks for the missing pieces one at a time,
starting with the orders, rather than listing every gap at once; keeps the vans and the depot in the
run and never writes them to the account; states the charge and waits; and after the yes, reports the
finished run rather than saying it started one.

**B. Build the account and the plan, keeping both**

> I want to set up and run a delivery round from scratch with the orders in the attached file, and
> keep the resources afterwards.
>
> Create a depot called "QA Central Brooklyn" at 630 Flushing Ave, Brooklyn, NY. Show me the location
> so I can confirm it before you save it. Then create a vehicle called "QA Van 800" with a capacity of
> 800 kg and 6 m3. If either name already exists, tell me before creating a duplicate or changing it.
>
> With those, prepare a plan called "QA Brooklyn Round" from the attached orders, for tomorrow at
> 07:30, and keep it so I can reuse it. Set 8 minutes per delivery, at most 20 stops per vehicle,
> routes of up to 6 hours, respect the delivery windows, and fill vehicles to 90% at most. Do not
> invent capacities or anything missing from the file. If the constraints make it impossible to
> deliver everything, explain the problem and ask me what I would rather change.
>
> Show me the cost and let me confirm before running. Do not replace any other plan.
>
> After it runs, follow through to the final result: how many routes, how many orders assigned and
> unassigned, how many vehicles were used, and how long the longest route is. If there are reasons
> for the unassigned ones, explain them. Finish by telling me which saved plan and which resources
> are now in my account.

What a right answer does: geocodes the depot address and shows the matched location before saving
anything; saves the depot and the vehicle as two separate confirmed writes; imports the file rather
than reading its rows into the chat; says plainly that a fleet and a driver are created in the
Vepathos dashboard, not here, instead of pretending to create them; states the charge and waits; and
closes with the plan id and the two saved resources.

### Negative prompts, where refusing is the right answer

> Use 25 vehicles for tomorrow's round.

Nothing is saved to the account. The 25 is a setting of that run, not 25 new vehicles.

> Just route these street addresses, skip the geocoding.

Refuses to invent coordinates and offers to geocode them first.

> Run an optimization for 3,000 stops with time windows.

On the Free plan this returns a plan limit error with a contact link, not a payment page, and the
assistant explains which plan would cover it.

> How did optimization opt_zzzzzzzz turn out?

A clear "not found", never an invented result.

## Manual install (not a directory)

```text
Claude.ai   → Settings → Connectors → Add custom → https://mcp.vepathos.com/mcp
Claude Code → claude mcp add --transport http vepathos https://mcp.vepathos.com/mcp
Cursor      → MCP url: https://mcp.vepathos.com/mcp
```

Add "Add to Claude / Cursor / …" buttons to the README only after that client's flow is verified end
to end.

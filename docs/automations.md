# Automations over MCP

**Status:** implemented and deployed since 0.7.1. `list_automations` and `create_automation` are two
of the 15 tools production publishes today, always on, no flag. Spans `vepathos-api-doc` (the
channel routes and the owned plan), `vepathos-mcp` (the two tools) and the web client (which uses
its own proxy, not these tools).

## What an automation is

A standing rule: it looks at the orders waiting, and when there are enough it works out the routes.
"Every weekday at 08:00, take whatever my shop left waiting, and send three vans out." Two things
decide whether a slot fires — the clock (or a load line) and a minimum worth routing — and one thing
decides what happens then: `suggest` works the routes out and asks; `auto` routes on its own.

## The one rule that matters

**Nothing in this adapter switches an automation on.** `create_automation` writes `enabled: false`,
the channel refuses to enable on create, and the answer carries `account_url` so the tool can say
where its owner turns it on.

This is not caution for its own sake. A rule that is on spends the account's monthly stops on a
schedule nobody is watching, in an operation the agent cannot see. Consent for that belongs to the
person, on a screen that shows what the rule will do. The tool descriptions repeat it, because some
hosts never show the server instructions, and `create_automation` refuses its own answer if Core ever
reports a newly created rule as already on. Replays report the current state without changing it.

## The rule owns its plan

A run needs a plan: somewhere for the depot, the optimizer's settings, the history and the billing
identity. Asking a person to pick one asked them about a container instead of their deliveries, and
left the schedule at the mercy of a plan they could rename, edit or delete.

So `create_automation` takes `template_plan_id` and **copies** it. A copy, once — not a link.
Editing that plan afterwards does not change the rule; deleting it does not stop it.

The plan the rule owns has no id on the wire. An id in an agent's hands is an id it can pass to
`optimize_routes`, which would spend the rule's stops by hand and move the `workspaceRevision` its next
batch checks against (a run aborts with `plan_edited` when that happens).

## Tools

| Tool | Reads / writes | Notes |
|---|---|---|
| `list_automations` | read | The rules, the connected stores (with `integration_account_id`), and the limits. No arguments. |
| `create_automation` | write | Prepares one, switched off. Idempotent through `operation_id`. |

`create_automation` needs to be told where the batch comes from: `use_plan_stops: true` for a route
that repeats (the template's stops are copied), or `integration_account_id` for a connected store
(bound in the same transaction as the rule and its plan).

`missing` in the answer is what a run would still lack, computed by Core from the workspace it just
wrote. Today that is `depot`, when the copied plan's depot is not one from the account's catalog — a
run refuses an ad-hoc depot, and saying so now beats three `internal_error` slots later.

## Web handoff

The web chat (`/ai`, "T-P4T0") builds the same rule with its own card and its own proxy
(`POST /api/optimization/automations`), not through these tools. Both write the same
`ownedPlan` body and the same `AutomationConfig`, so a rule made in the chat opens in the section and
a rule made by an agent opens in both.

## Language

Answer in the user's language. The rule's own words are the operation's: *pedidos*, *paradas*,
*depósito*, *flota*. "It is prepared and switched off" is the sentence; "it is running" is never.


## E4 · Contract (added in 0.7.1)

`vehicle_type_id` optionally selects a template entry. When omitted, a template with exactly one valid entry selects it automatically. Several entries leave `missing: ["fleet"]` and return `vehicle_options` (`id`, `name`); the user chooses in the dashboard. Replaying an operation does not edit it.

`look` accepts `clock`, `fill`, or `both`; `fill_by` accepts orders, packages, stops, weight, or volume. `min_packages` and `fill_percent` travel in the backend trigger. A fill-only rule uses the scheduler's all-day 15-minute cadence.

Creation always starts off. A replay returns `replayed: true` and the actual current `enabled` state, including a subsequent user activation. An intentionally deleted operation returns HTTP 409 (`automation_attempt_deleted` in the error message). A new user-requested creation needs a new operation id. Do not silently retry deletion errors under another id.

Quota counts only enabled rules; blocked rules release their slot and must pass the current quota and readiness checks before being enabled again. The backend validates a catalog depot and a selected template vehicle on every enable.

The backend opt-in regression `tests/unit/automation-e4-contract.test.ts` invokes this Python serializer and response parser against the real API handler and disposable PostgreSQL. Only authentication is mocked; no provider or production database is called.

# Smoke prompts for real agents

Run each prompt in a real MCP client connected to Vepathos (Claude Code first, then Claude.ai /
ChatGPT Apps and others). Datasets come from `devtools/generate_dataset.py` so results are
reproducible.

```bash
.venv/bin/python -m devtools.generate_dataset --stops 120 --vehicles 5 --out devtools/out/case1.json
```

## Gate (T19) — before any behaviour flag

**No production behaviour flag** (`MCP_CONFIRM_BEFORE_OPTIMIZE`, new tools, quota overlays) ships
without running the ChatGPT staging connector cases below and recording the date in
`docs/compatibility-matrix.md`. Unit/gate suites (even 100+ green tests) are not enough: the 16/09
incident shipped with a green gate and a broken ChatGPT experience.

Staging checklist:

1. Connect ChatGPT Apps connector to staging MCP, with `MCP_IMPORT_TOOLS_ENABLED=true` for the import cases.
2. Cases 1 and 5 below (small optimize + file import).
3. Confirm preflight vs charged run matches `MCP_CONFIRM_BEFORE_OPTIMIZE`.
4. Import path: the first `optimize_routes` is charged and the agent says so; a rerun of the same plan
   within 24 h with the same stops or fewer (other vehicles or `exclude_stop_ids`) is the free retry and
   the agent says that too. When the library is full the agent names the replaced plan, and it offers
   the account link and the public map instead of creating the map unasked.
5. Record client build + date. Only then set `MCP_IMPORT_TOOLS_ENABLED=true` in production.

## Cases

00. **Master data and help** (0.8.0) — staging with `MCP_CATALOG_WRITE_TOOLS_ENABLED=true`
   1. > Agregame una Sprinter de 1.500 kg y 14 m³.
      Asks for the yes, calls `manage_vehicle` create, says it is saved. It does **not** optimize.
   2. > Agregame 3 Sprinter de 1.500 kg.
      ONE call (a vehicle is a type); says how many run is set on each plan.
   3. > Usá 25 vehículos para el reparto de mañana.
      **No** `manage_vehicle` call: 25 travels as `count` on the run.
   4. > La camioneta 4 ahora soporta 12 m³.
      `list_fleet` → `manage_vehicle` update.
   5. > Mañana la Sprinter no sale.
      A plan setting: left out of that run; nothing is saved.
   6. > Usá como depósito el de Barracas.
      `list_fleet` depots → its coordinates as `depot`; nothing is saved.
   7. > Guardá un depósito nuevo en Av. San Martín 700.
      `geocode_addresses` → says the matched address → after the yes, `manage_depot` create.
   8. Repeat prompt 1: `already_existed`, and no duplicate in the dashboard.
   9. In Claude Code or Claude Desktop, pick the prompt `vepathos_help`: it inspects the account and
      explains by task, without tool names. Pick `vepathos_tools`: the list with technical names.

0. **Automations** (0.7.0)
   > What automations do I have? / Route my Mercado Libre orders every weekday at 8.
   The agent reads `list_automations` (and the stores it returns), prepares one with
   `create_automation`, says it is switched OFF, gives the dashboard link, and never claims it is
   running. Sending the same `operation_id` twice must not leave two rules.

1. **Basic fleet**
   > I have 120 deliveries and 5 vans. Optimize the deliveries minimizing total distance.
   Dataset: 120 stops, 5 vans. Expect `optimize_routes` (or import path if file attached).

2. **Weight capacity at scale**
   > Optimize these 2,500 deliveries across 30 vehicles while respecting weight limits.
   Dataset: 2,500 stops with `weight_kg`, 30 vehicles with `max_weight_kg`. Expect a plan decision
   (success on Growth, or `PLAN_UPGRADE_REQUIRED` on Free that the agent explains). Prefer
   `import_deliveries` → `optimize_routes` rather than pasting rows.

3. **Time windows and unassigned deliveries**
   > Each delivery has a time window. Find feasible routes and tell me which deliveries cannot be assigned.
   Dataset: 300 stops with windows. Expect `route_start_time` handling and a `detail=unassigned` read.

4. **Fleet sizing (8k — import path)**
   > I have 8,000 orders in this spreadsheet. How many vehicles did the optimizer actually need?
   Expect `import_deliveries` → `get_import_result` → `optimize_routes`, not inline `stops[]`.
   See `docs/large-payloads.md`. Report `vehicles_used` against `vehicles_available`.

5. **Import attachment**
   > Attach a CSV of deliveries and plan routes from our depot at …
   Expect fileParams → `import_deliveries`, summary without all rows, then `optimize_routes`.

## What to record per run

| Criterion | Pass when |
|---|---|
| Tool selection | Large files use import/dataset tools; small plans may use `optimize_routes` |
| Argument quality | Valid on the first or second attempt; units respected (kg, m³, HH:MM) |
| Async handling | Follows up with `get_import_result` / `get_optimization_result` |
| Result use | Summarizes vehicles used, distance and duration; pages stops only when asked |
| Plan errors | Explains `PLAN_UPGRADE_REQUIRED` / `QUOTA_EXCEEDED` in plain language |
| Tokens | Output tokens per call and total conversation tokens |
| Duplicates | No duplicate optimizations after retries (check Core) |

Record results in `docs/compatibility-matrix.md` (Tested column) with date and client version.

# Smoke prompts for real agents

Run each prompt in a real MCP client connected to Vepathos (Claude Code first, then Claude.ai and
others). Datasets come from `devtools/generate_dataset.py` so results are reproducible.

```bash
.venv/bin/python -m devtools.generate_dataset --stops 120 --vehicles 5 --out devtools/out/case1.json
```

## Cases

1. **Basic fleet**
   > I have 120 deliveries and 5 vans. Optimize the deliveries minimizing total distance.
   Dataset: 120 stops, 5 vans.

2. **Weight capacity at scale**
   > Optimize these 2,500 deliveries across 30 vehicles while respecting weight limits.
   Dataset: 2,500 stops with `weight_kg`, 30 vehicles with `max_weight_kg`. Expect a plan decision
   (success on Growth, or `PLAN_UPGRADE_REQUIRED` on Free that the agent explains).

3. **Time windows and unassigned deliveries**
   > Each delivery has a time window. Find feasible routes and tell me which deliveries cannot be assigned.
   Dataset: 300 stops with windows. Expect `route_start_time` handling and a `detail=unassigned` read.

4. **Fleet sizing**
   > I have 8,000 orders. How many vehicles did the optimizer actually need?
   Expect the agent to hit practical payload limits (see `docs/large-payloads.md`), split or explain, and
   report `vehicles_used` against `vehicles_available`.

## What to record per run

| Criterion | Pass when |
|---|---|
| Tool selection | The agent calls `optimize_delivery_routes` (not a maps or directions tool) |
| Argument quality | Valid on the first or second attempt; units respected (kg, m³, HH:MM) |
| Async handling | The agent follows up with `get_optimization_result` using `optimization_id` |
| Result use | Summarizes vehicles used, distance and duration; pages stops only when asked |
| Plan errors | Explains `PLAN_UPGRADE_REQUIRED` / `QUOTA_EXCEEDED` in plain language and shows the upgrade link |
| Tokens | Output tokens per call and total conversation tokens |
| Duplicates | No duplicate optimizations after retries (check Core) |

Record results in `docs/compatibility-matrix.md` (Tested column) with date and client version.

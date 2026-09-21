# Testing

| Layer | Location | Runs against | Command |
|---|---|---|---|
| Unit | `tests/unit` | Pure functions: schemas, rules, mapping, errors, rendering, token budget, auth verifiers, redaction, rate limits | `.venv/bin/pytest tests/unit` |
| Contract | `tests/contract/test_vepathos_api_client.py` | `VepathosApiClient` ↔ `docs/core-channel-contract.md` (HTTP mocked with respx): headers, idempotent retries, error mapping, circuit breaker | `.venv/bin/pytest tests/contract` |
| Tools end to end | `tests/contract/test_tools_with_fake_core.py` | MCP SDK client → server → fake Core channel | `.venv/bin/pytest tests/contract` |
| Protocol | `tests/protocol` | Streamable HTTP wire behaviour: 401 + protected resource metadata, legacy `initialize` stateless, `server/discover` (2026-07-28), DNS-rebinding protection, health/ready/metrics | `.venv/bin/pytest tests/protocol` |
| Integration | `tests/integration` | Real local Vepathos stack (api-doc MCP channel + optimizer + worker) | `VEPATHOS_INTEGRATION=1 .venv/bin/pytest -m integration tests/integration` |

Quality gates: `ruff check`, `ruff format --check`, `mypy` (strict), `pytest`, `pip-audit`, `docker build`.

The fake Core (`devtools/fake_core`) is a **test double** of the channel contract. It implements no
routing: routes are a naive split of the input order. Never use it to judge optimization quality.

## MCP Inspector

Inspector v2 (`@modelcontextprotocol/inspector`) against a local server:

```bash
docker compose up --build   # vepathos-mcp + fake Core
npx -y @modelcontextprotocol/inspector@latest --cli http://127.0.0.1:8080/mcp --transport http \
  --header "Authorization: Bearer dev-bearer-token-change-me" --method tools/list --strict --format json
npx -y @modelcontextprotocol/inspector@latest --cli http://127.0.0.1:8080/mcp --transport http \
  --header "Authorization: Bearer dev-bearer-token-change-me" --method tools/call \
  --tool-name optimize_delivery_routes \
  --tool-args-json '{"depot":{"latitude":-34.6037,"longitude":-58.3816},"vehicles":[{"vehicle_id":"van","count":2}],"stops":[{"stop_id":"A1","latitude":-34.61,"longitude":-58.39},{"stop_id":"A2","latitude":-34.62,"longitude":-58.40}]}' \
  --format json
```

Checklist (last run 2026-09-13):

- [x] `tools/list` returns the tools with titles, annotations, input and output schemas; `--strict` reports no portability problems (Inspector 2.6.0, fake Core).
- [x] `tools/call optimize_delivery_routes` completes (inline wait) with summary and routes (fake Core).
- [x] `tools/call get_optimization_result` with `detail=stops` returns ordered stops with arrival times (fake Core).
- [x] Unauthenticated request → `401` + `resource_metadata` (fake Core and real local stack).
- [x] Real local stack discovery (`scripts/smoke-local.sh`, 2026-09-13): adapter `/health` + `/ready` (`core: ok`), Core `/api/mcp/v1/health`, PRM, AS (S256, CIMD, `none`, scope `optimize`), JWKS, DCR 201.
- [ ] Inspector `tools/list` / `tools/call` against the real stack (needs `VEPATHOS_MCP_BEARER` or `scripts/oauth-local.sh` in a browser).
- [ ] OAuth consent in the Inspector web UI against the real local AS.

## Integration against the local Vepathos stack

1. api-doc (worktree with the MCP channel): `npm run db:up`, apply migrations, set
   `MCP_CHANNEL_ENABLED=true`, `MCP_SERVICE_KEYS=<key>`, `OPTIMIZER_BACKEND_URL`, `OPTIMIZER_API_KEY`,
   then `npm run dev`.
2. Optimizer: `route-optimizer-app` local compose (API + worker + Redis + RabbitMQ). First runs for a
   region download map graphs and can take minutes.
3. Accounts: one Free ("Duck") account and one Growth account, each with an MCP developer credential.
4. Run:

Put the two credentials in this repo's `.env` (gitignored; see `.env.example`) and every command
below picks them up, or pass them on the command line:

```bash
set -a && . ./.env && set +a && VEPATHOS_INTEGRATION=1 .venv/bin/pytest -m integration tests/integration -v
```

```bash
VEPATHOS_INTEGRATION=1 \
VEPATHOS_API_BASE_URL=http://localhost:3000 \
VEPATHOS_MCP_SERVICE_KEY=<key> \
VEPATHOS_FREE_CREDENTIAL=<vpt_…:vpt_sk_…> \
VEPATHOS_GROWTH_CREDENTIAL=<vpt_…:vpt_sk_…> \
.venv/bin/pytest -m integration tests/integration -v
```

Scenarios: 10 and 150 stops; weight, volume and time windows; invalid and far-away coordinates; plan
limit (`PLAN_UPGRADE_REQUIRED`); one-time trial (Duck + 80 stops + time windows; the second attempt is
rejected); quota untouched by the trial; idempotent retry without duplicate jobs; backend failure;
unauthorized credential.

## Real agents

Smoke prompts and evaluation criteria: `docs/smoke-prompts.md`.

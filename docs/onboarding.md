# Onboarding

## End users (production target)

```
User finds Vepathos in Claude (or another MCP client)
  → Connect
  → OAuth redirect to api.vepathos.com
  → Continue with Google / Continue with email
      · existing account: sign in
      · no account: a Vepathos account is created on the Free ("Duck") plan
  → Consent: "Allow <client> to optimize routes with your Vepathos account"
  → Back to the conversation, connected
```

No API keys, no JSON configuration, no separate visit to the Vepathos web app.

### First use and the one-time full trial

Free accounts can use Vepathos from MCP normally, within the Free plan. The first time a request
needs something the plan does not include — more than 500 stops, or weight, volume or time-window
constraints — and the request has at most 2,000 stops, Vepathos runs it once as a full-feature
optimization without consuming the monthly quota. Afterwards the normal plan limits apply.

### Upgrading

When a request exceeds the plan, the tool returns `PLAN_UPGRADE_REQUIRED` with an `upgrade_url`.
The assistant can show it, for example: "This optimization exceeds your current Vepathos plan.
[Upgrade Vepathos]". The user completes payment on Vepathos (Stripe Checkout) and returns to the
conversation. The connection keeps working without reconnecting, and resending the same request now
succeeds.

## Developers and headless agents

1. Create a credential in the Vepathos dashboard with the "Use with AI agents (MCP)" option (scope
   `mcp:optimize`).
2. Configure the MCP client with the remote URL and header
   `Authorization: Bearer <client_id>:<client_secret>`.
3. Usage counts against the same account plan as the web app and the REST API.

## Local development

```bash
python3.12 -m venv .venv && .venv/bin/pip install -e ".[dev]"
cp .env.example .env            # AUTH_MODES=service, set MCP_DEV_BEARER_TOKEN and credentials
docker compose up --build       # vepathos-mcp + fake Core (test double) on http://localhost:8080/mcp
```

See `docs/testing.md` for MCP Inspector commands and for running against the real local Vepathos
stack.

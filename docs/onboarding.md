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

When a request exceeds the plan, the tool returns `PLAN_UPGRADE_REQUIRED` with `eligible_plans`
and either `upgrade_url` or `contact_url`.

While paid self-serve is off (`NEXT_PUBLIC_PAID_PLANS_ENABLED` is not `true`), Vepathos only
offers the Free plan on this channel. The assistant receives a `contact_url`, not a Stripe
checkout link. That is fine for internal use and beta. A reviewer who submits a large job will
be told to contact Vepathos, not to pay.

For the public Claude directory, either enable Stripe so reviewers can pay and retry, or state
clearly in the listing and docs that the channel is Free + contact.

When paid plans are enabled, `upgrade_url` points at Billing (`upgrade`, `reason`, `source=mcp`).
Payment is delegated entirely to Stripe (Checkout or a Stripe subscription change). Vepathos
does not charge cards and MCP has no pay tool. After Stripe confirms the plan, the user
returns to the conversation and retries without reconnecting.

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

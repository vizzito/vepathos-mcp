# Publish Vepathos MCP to AI clients and directories

Approved to pursue 2026-09-14. There is **no single marketplace** that turns the server on
everywhere. Each client has its own portal. This page is the order that does not get the
listing rejected.

**Live URL (already):** `https://mcp.vepathos.com/mcp`  
**Registry name:** `com.vepathos/vepathos` (`server.json`)  
**Paste pack for forms:** [docs/directory-listing.md](directory-listing.md)

Until Stripe is on, every public listing **must** say: MCP is **Free + contact**. A reviewer
who submits a large job gets `contact_url`, not Checkout.

---

## What is already true

| Item | Status |
|---|---|
| Production Streamable HTTP + OAuth + API keys | Live |
| GitHub `vizzito/vepathos-mcp` | **Public**, Apache-2.0, `SECURITY.md` |
| `https://vepathos.com` | 200 |
| `https://vepathos.com/privacy` | 200 — **no MCP section yet** |
| `https://vepathos.com/mcp` | **404** |
| Official MCP Registry | **Listed**: `com.vepathos/vepathos`, republished on every version bump |
| Claude Connectors Directory | Not submitted. Claude does **not** know Vepathos |
| ChatGPT Plugins Directory | Not submitted |

Anyone can use it **today** as a **custom / manual** connector if they have the URL.
Directories are how strangers find it.

---

## Order (do not skip)

```
1. Custom connector smoke (Claude or Inspector web)
     geocode + optimize on a test account
2. Public pages
     privacy MCP section + vepathos.com/mcp
3. Official MCP Registry
     DNS TXT + mcp-publisher publish
4. Claude Connectors Directory
     Team/Enterprise portal (not a Pro personal account)
5. ChatGPT plugin (With MCP)
     OpenAI verified publisher + domain challenge
6. Cursor / VS Code / Codex
     mostly ingest the official registry; then verify install
7. README “Add to …” buttons
     only after each flow works
```

Step 1 can happen **tonight**. Steps 3–5 cannot until 2 exists and you click the portals.
This repo cannot submit to Claude or ChatGPT for you.

---

## 1. Tonight — custom connector (required before any directory)

Claude directory review **requires** every tool exercised as a custom connector (and Inspector).
Skipping this is a common rejection.

### Claude.ai (closest to a real customer)

Needs a Claude account that can add **custom connectors** (availability depends on plan).

1. Claude.ai → **Settings → Connectors → Add custom connector**.
2. Name: `Vepathos`. URL: `https://mcp.vepathos.com/mcp`. No Bearer header.
3. Connect → browser opens `api.vepathos.com` → sign in / sign up (use a **test** Vepathos
   account, not ops keys) → **Allow**.
4. Callback must be `https://claude.ai/api/mcp/auth_callback`, **not** `127.0.0.1`.
5. In chat: ask it to list tools, then geocode 2–3 CABA streets, then optimize those pins
   (one depot, one van). Confirm `get_geocode_result` / `get_optimization_result`.

### Inspector web (if Claude custom is blocked on your plan)

Same URL, **web UI** (not `--cli`). DCR + consent. Then the same geocode/optimize.

### Cursor / Claude Code / Codex (manual, no directory)

```bash
# Claude Code
claude mcp add --transport http vepathos https://mcp.vepathos.com/mcp
```

Cursor: Settings → MCP → URL `https://mcp.vepathos.com/mcp` (OAuth in the browser).

Codex: remote URL in `config.toml`, then `codex mcp login` if it asks.

These do **not** put Vepathos in anyone else’s catalog.

---

## 2. Public pages (blocks Claude + ChatGPT review)

Drafts already exist. They are not live.

| Need | Draft | Live |
|---|---|---|
| Privacy MCP section | [docs/privacy-mcp.md](privacy-mcp.md) | Paste into `https://vepathos.com/privacy` |
| Product page | [docs/public-mcp-page.md](public-mcp-page.md) | Publish `https://vepathos.com/mcp` (today **404**) |

State **Free + contact** on the product page. Support email: the one already on vepathos.com.

This lives on the **marketing site**, not in this repo. After it is live, set
`websiteUrl` in `server.json` to `https://vepathos.com/mcp` if you want the registry
to point at docs instead of the homepage.

---

## 3. Official MCP Registry (feeds VS Code and aggregators)

**Done.** `com.vepathos/vepathos` is live on `registry.modelcontextprotocol.io`, namespace under
**domain** auth on `vepathos.com` (not GitHub `io.github.vizzito/…`). The private key lives at
`~/.vepathos/mcp-registry-ed25519.key` (Mac only, 600, never in git). To republish on a version bump:

```bash
mcp-publisher login dns --domain vepathos.com --private-key "$(cat ~/.vepathos/mcp-registry-ed25519.key)" \
  && mcp-publisher publish
```

`server.json` lives at the repo root, so `publish` takes no argument. Bump its `version` to match
`__version__` before running this. The steps below are how the key and the first entry were made;
kept for reference and for rotating the key.

On a machine that will keep the private key **off git**:

```bash
cd /tmp
openssl genpkey -algorithm Ed25519 -out vepathos-mcp-registry.pem
chmod 600 vepathos-mcp-registry.pem

PUBLIC_KEY="$(openssl pkey -in vepathos-mcp-registry.pem -pubout -outform DER | tail -c 32 | base64)"
echo "vepathos.com. IN TXT \"v=MCPv1; k=ed25519; p=${PUBLIC_KEY}\""
```

GoDaddy → DNS → TXT on the **apex** `vepathos.com` (name `@` or blank):

```
v=MCPv1; k=ed25519; p=<PUBLIC_KEY>
```

Wait until `dig +short TXT vepathos.com` shows that string. Then:

```bash
brew install mcp-publisher   # or the official binary

PRIVATE_KEY="$(openssl pkey -in /tmp/vepathos-mcp-registry.pem -noout -text | grep -A3 "priv:" | tail -n +2 | tr -d ' :\n')"
mcp-publisher login dns --domain vepathos.com --private-key "${PRIVATE_KEY}"

cd /path/to/vepathos-mcp
mcp-publisher publish
```

`server.json` already matches schema 2025-12-11 (`description` ≤ 100 chars). Bump `version`
on every contract change and publish again.

Store `vepathos-mcp-registry.pem` in a password manager. **Never commit it.**

HTTP alternative (if you prefer not to touch apex TXT): host
`https://vepathos.com/.well-known/mcp-registry-auth` with the same `v=MCPv1; …` line,
then `mcp-publisher login http`.

Verify:

```bash
curl -sS "https://registry.modelcontextprotocol.io/v0.1/servers?search=vepathos"
```

---

## 4. Claude Connectors Directory

**Hard gate:** Team or Enterprise org + Owner (or Directory role). A personal / Pro account
**cannot** open the submission portal.

Portal: Claude.ai → organization **Settings → Directory** (submission wizard).

| Portal step | Value |
|---|---|
| Connection | Universal URL `https://mcp.vepathos.com/mcp`, Streamable HTTP |
| Auth | OAuth — DCR and/or CIMD (we advertise both) |
| Docs / privacy | `https://vepathos.com/mcp` and `https://vepathos.com/privacy` (**after step 2**) |
| Listing copy | [docs/directory-listing.md](directory-listing.md) |
| Data | First-party API; no health data; no sponsored content |
| Test account | Populated Vepathos user + how to log in (no MFA if you can avoid it) |
| Compliance | Seven acknowledgments |

AS must be reachable from Anthropic (`160.79.104.0/21`) — `api.vepathos.com` is public; no
extra firewall work unless you later lock the AS.

Email for escalations: `mcp-review@anthropic.com`.

After submit, status lives in that same dashboard. Inclusion does **not** change the tools.

---

## 5. ChatGPT / Codex (Plugins Directory)

Manual use: ChatGPT **developer mode** / custom MCP (workspace admin must allow custom apps).

Directory: [OpenAI plugin submission](https://developers.openai.com/plugins/deploy/submission)
→ **With MCP** → Universal URL `https://mcp.vepathos.com/mcp`.

You will need:

- Verified OpenAI publisher identity (individual or business)
- Apps Management: Write
- Domain verify: set `OPENAI_APPS_CHALLENGE` to the portal token in the production `.env`
  (never in git), recreate the adapter, then
  `https://mcp.vepathos.com/.well-known/openai-apps-challenge` must return that token as
  `text/plain` with nothing else in the body
- Reviewer credentials, 5 positive + 3 negative cases (see listing pack)
- Privacy + terms + support URLs

This is a **second** review process. Do it after Claude custom smoke, not instead of it.

---

## 6. Cursor, VS Code, Smithery, others

| Client | How they find you |
|---|---|
| Cursor | Manual URL now; directory/listings often follow the official registry |
| VS Code (Copilot MCP gallery) | Official registry entry |
| Codex | Manual `config.toml` now; ChatGPT plugin listing later |
| Smithery / Glama / Pulse | Most ingest `registry.modelcontextprotocol.io` |

Publishing the registry (step 3) is the highest-leverage “everywhere else” action after
the public pages exist.

---

## 7. What this repo will not do automatically

- Click Claude / OpenAI / Anthropic portals
- Edit `vepathos.com` (privacy + `/mcp` page)
- Put an Ed25519 private key in git
- Turn Stripe on (product decision; listing stays Free + contact until then)
- Enable `MCP_FULL_TRIAL_ENABLED` (still **false** in prod until you ask)

Kill switch remains `MCP_CHANNEL_ENABLED=false` on api-doc.

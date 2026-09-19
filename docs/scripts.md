# Scripts locales (`scripts/`)

Utilidades de este repo. No se despliegan con la imagen de producción salvo
`deploy-api-prod.sh` (se corre en la VM).

## Qué querés lograr con el tunnel

**Entrar al chat de Vepathos / ChatGPT** (OpenAI no puede pegarle a localhost).
No hace falta túnel de `:3000` ni `:3005` si estás en esta máquina.

```bash
./scripts/sync-dev-urls.sh up 8080
```

Eso levanta **solo** MCP `:8080`, escribe `MCP_PUBLIC_URL`, `MCP_RESOURCE_URI` y
`VEPATHOS_MCP_HTTP_URL` (nunca el host de Shopify/API), y se queda en foreground.

UI + Google + Shopify **desde internet**:

```bash
./scripts/sync-dev-urls.sh up 8080 3000 3005
```

(`start` sin flags es lo mismo: 3 tunnels.)

El proceso **queda en esa Terminal**. `Ctrl+C` (o `clean`) mata los tunnels, cierra
ventanas Terminal leftovers y vuelve el env a localhost.

Los quick tunnels cambian de hostname en cada `start`. Por eso el script:

1. Imprime / copia el callback de Google  
2. Abre Cloud Console + la URL del WEB  
3. Vos agregás el redirect URI → Save → esperás ~30s → login

Solo ChatGPT (sin UI pública): `./scripts/sync-dev-urls.sh start --mcp-only`  
(Google queda en `localhost:3000`, solo esta máquina).

| Script | Para qué |
|---|---|
| [`sync-dev-urls.sh`](#sync-dev-urlssh) | Tunnels + sync de `.env` (localhost por defecto) |
| [`smoke-local.sh`](#smoke-localsh) | Smoke discovery/auth contra adapter local |
| [`smoke-prod.sh`](#smoke-prodsh) | Smoke contra URL pública |
| [`oauth-local.sh`](#oauth-localsh) | Loop OAuth local (consent → token → initialize) |
| [`inspect.sh`](#inspectsh) | MCP Inspector CLI con API key |
| [`deploy-api-prod.sh`](#deploy-api-prodsh) | Deploy adapter en api-prod + smoke |

---

## `sync-dev-urls.sh`

Sincroniza URLs entre repos hermanos. Por defecto **todo localhost**; `start`
levanta Cloudflare quick tunnels y **se queda en foreground**.

| Puerto | Repo | En `start` (default) |
|---|---|---|
| `:8080` | vepathos-mcp | Tunnel |
| `:3000` | vepathos-api-doc | Tunnel (Google OAuth) |
| `:3005` | vepathos-router-client | Tunnel (chat/UI) |
| `:8100` | vepathos-smart-import | Loopback |

**Archivos que toca:** `vepathos-mcp/.env`, `vepathos-api-doc/.env.local`,
`vepathos-router-client/.env.local`, `vepathos-smart-import/.env` (CORS),
estado/logs en `vepathos-mcp/.dev-tunnels/`.

No reinicia servicios: te dice cuáles reiniciar.

### Comandos

```bash
./scripts/sync-dev-urls.sh local
./scripts/sync-dev-urls.sh clean   # = stop: mata tunnels + cierra Terminal leftovers

# Internet: chat/UI + Google + MCP (queda hasta Ctrl+C)
./scripts/sync-dev-urls.sh start
# → registra el callback que imprime, reiniciá servicios, abrí la URL WEB
# → Ctrl+C cierra todo y vuelve a localhost

./scripts/sync-dev-urls.sh start --mcp-only  # solo ChatGPT
./scripts/sync-dev-urls.sh start --terminal  # opcional: logs en ventanas Terminal.app
./scripts/sync-dev-urls.sh status
```

### Login Google desde internet

Google OAuth corre en **API** (`:3000`), no en la WEB. En cada `start` el host
trycloudflare es nuevo — hay que agregar en
[Google Cloud → Credentials](https://console.cloud.google.com/apis/credentials):

`https://<API-TUNNEL>/api/auth/callback/google`

(no uses el host WEB `:3005` ahí). Dejá también
`http://localhost:3000/api/auth/callback/google`.

El script copia ese URI, lo guarda en `.dev-tunnels/google-callback.txt` y abre
la consola + la URL **WEB**. Después **reiniciá api-doc + router** (vars
`NEXT_PUBLIC_*`).

---

## `smoke-local.sh`

Smoke contra adapter local (`:8080`): `/health`, `401` + resource metadata.  
No llama optimize.

```bash
./scripts/smoke-local.sh
```

---

## `smoke-prod.sh`

Igual contra `ADAPTER_URL` (default `https://mcp.vepathos.com`), chequea `__version__`.

```bash
ADAPTER_URL=https://mcp.vepathos.com ./scripts/smoke-prod.sh
```

---

## `oauth-local.sh`

Loop OAuth local: PRM → AS → JWKS → DCR → browser consent → token → `initialize`.

```bash
./scripts/oauth-local.sh
```

---

## `inspect.sh`

Inspector CLI con API key.

```bash
export VEPATHOS_MCP_BEARER='vpt_…:vpt_sk_…'
./scripts/inspect.sh tools/list
```

---

## `deploy-api-prod.sh`

En la VM de api-prod, post `git pull`:

```bash
cd ~/vepathos-deploy/vepathos-mcp && git pull origin develop && scripts/deploy-api-prod.sh
```

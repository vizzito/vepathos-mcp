# B5. Casos de ajustes del plan y la compuerta

## Reglas de esta sesión

Trabajás en `/Users/martinvizzolini/workspace/vepathos-router-client` (Next.js + Playwright).
Esta sesión hace UNA tarea: la de esta ficha. No hagas más que lo que pide.

Reglas duras:

- El repo tiene ~185 archivos sin commitear de Martín. Prohibido: `git commit`, `git stash`, `git add`, `git checkout` o `git restore` sobre archivos, crear ramas, `git push`. De git, solo lectura.
- No corras formateadores ni linters con `--fix` sobre archivos que no creaste vos.
- No edites archivos existentes salvo los que la ficha nombra explícitamente. No toques `package.json`.
- Nada de `.claude/` ni `.codex/` a git.
- Código y comentarios en inglés, con la densidad de comentarios del archivo vecino. Cada test abre con un comentario que dice POR QUÉ existe, no qué hace.
- Las aserciones son sobre requests, atributos DOM (`data-question`, `data-status`, `data-step`, `data-phase`, `data-code`) y el ledger de escrituras. NUNCA sobre el texto del asistente: este repo ya se quemó con eso (`lib/ai/__tests__/prompts/evaluate.ts:10-13` y `:59-66`). Si comparás números, compará dígitos: `1.240` y `1,240` son el mismo número.
- Si algo no es testeable desde la UI con la infraestructura que hay, no lo simules: decilo en el handoff, explicá por qué y dejalo afuera. Un verde que no mide nada es peor que un hueco.
- Si un test existente está rojo antes de tus cambios, anotalo y NO lo arregles.

Especificación (leé las secciones que cite la ficha):
`/Users/martinvizzolini/workspace/vepathos-mcp/docs/chat-ui-scenarios.md`
Donde esa doc y esta ficha no coincidan, gana la ficha: fue verificada después contra el código.

Antes de empezar: leé todos los handoffs que haya en `/Users/martinvizzolini/workspace/vepathos-mcp/devtools/out/ui_tasks/` (creá el directorio si no existe; está fuera del repo y en `.gitignore`).

Al terminar: escribí `/Users/martinvizzolini/workspace/vepathos-mcp/devtools/out/ui_tasks/B5.md` con: qué hiciste, archivos creados o editados, comandos que corriste con su última línea de resultado, desvíos respecto de la ficha y por qué, y dudas abiertas. Cerrá el handoff con la salida de `git status --short | wc -l` y de `git status --short -- e2e e2e-live scripts`. Nunca pegues `git diff` de archivos ajenos.

## Hechos ya verificados contra el código (2026-09-21)

No los redescubras. Si alguno ya no coincide con lo que ves, decilo en el handoff y seguí con lo que diga el código.

Qué es esto

- No es una suite de pass/fail: es un arnés de medición, espejo de `/Users/martinvizzolini/workspace/vepathos-mcp/devtools/agent_eval.py`. El modelo es no determinista; se corre cada escenario N veces y se reporta `pasó k/N`.
- Forma elegida: un script suelto, `e2e-live/run.ts`, que se corre con `npx tsx e2e-live/run.ts ...` y usa Playwright como librería (`import { chromium } from "@playwright/test"`), con el estilo de `scripts/prompt-live.ts`. `tsx` ya es devDependency. No es un `*.spec.ts`.
- Por qué no puede dispararse por accidente: CI solo corre `npm run ci` (typecheck + `scripts/run-unit-tests.mjs`, que junta solo `**/__tests__/*.test.ts`), nunca Playwright; y `playwright.config.ts` tiene `testDir: "./e2e"`, así que `e2e-live/` es invisible para `npx playwright test`. No nombres nada `*.test.ts` ni lo pongas bajo un `__tests__/`.
- No arranca servidor: apunta a un `npm run dev` que Martín tiene corriendo con su `.env.local` real. Default de `--base-url`: el valor de `AUTH_URL`.
- NO llames a `mockAi`, ni a `setup()`, ni importes `e2e/support/fixtures.ts` (trae un fixture automático `driverMirror` que tira error contra un server real y mockea `/api/pricing/catalog`), ni `e2e/support/constants.ts` (su `TEST_AUTH_SECRET` no firma nada válido acá), ni `e2e/support/test-server-env.ts` (apunta `APIDOC_URL` a un puerto muerto).
- Observá sin interceptar: `page.on("request")` y `page.on("response")`. No cambies ni un body. Prohibido `page.route` con `route.fetch()` sobre `/api/ai/chat`: bufferea el stream y rompe la UI.

Entorno (nombres de variables; NUNCA imprimas sus valores)

- Ya están en `.env.local`: `OPENAI_API_KEY`, `OPENAI_AI_MODEL`, `OPENAI_AI_REASONING_EFFORT`, `VEPATHOS_MCP_DIRECT_URL`, `VEPATHOS_MCP_HTTP_URL`, `APIDOC_URL`, `AUTH_SECRET`, `AUTH_URL`.
- El modelo se LEE, no se asume: `openaiAiModel()` en `lib/ai/config.ts:32-35` (default `gpt-5.6-luna`), esfuerzo en `openaiReasoningEffort()` (`:46-52`).
- Carga de env sin dependencias: copiar el lazo de `scripts/prompt-live.ts:48-60` (lee `.env.local` y `.env`, no pisa lo que ya está en `process.env`).
- La cuenta: el chat no lleva una credencial MCP de env, la acuña el servidor para el usuario logueado (`lib/ai/mcp-session.ts:37-55`, `defaultMinter.token() ?? .apiKey()`, implementados en `lib/ai/mint-mcp-bearer.ts`). `token()` hace `POST /api/dashboard/ai/mcp-token` contra api-doc; si da 404/405 cae a `apiKey()`, que REVOCA las API keys activas llamadas "Vepathos AI Planner" y crea una nueva (`mint-mcp-bearer.ts:70-100`). Si Martín tiene el chat abierto con esa misma cuenta, se la reemplaza. Hay que avisarlo antes de correr.
- La cookie de sesión tiene que estar firmada con el `AUTH_SECRET` real y llevar el id de un usuario que exista en el Core al que apunta `APIDOC_URL`. Forma de la cookie: `e2e/support/session.ts:20-43` (`encode` de `next-auth/jwt`, salt y nombre `vepathos-router.session-token`, claims `id`, `sub`, `email`, `name`, `plan`, `lastUserCheck`).

El cable

- `POST /api/ai/chat` responde `text/event-stream`, un JSON por frame, `data: {json}\n\n`, sin nombres de evento (`lib/ai/openai-request.ts:86-88`). El stream CIERRA por turno después del evento `done` (`app/api/ai/chat/route.ts:595-604`), así que `await response.text()` resuelve una vez por turno. Parseo: la regla de `lib/ai/sse-client.ts:12-24` (partir en `\n\n`, quedarse con las líneas `data:`, `JSON.parse`). Un turno puede tardar hasta 300 s.
- Eventos tipados: `AiServerEvent` en `lib/ai/events.ts:279-304`: `delta`, `activity {id, kind, phase}`, `card`, `ask {callId, ask}`, `approval {approval: {approvalRequestId, preflight}}`, `plan-update`, `manage-resource`, `operation`, `reconnect`, `error {code}`, `done`. `AiActivityKind` (`:24-33`): `connect|account|fleet|geocode|plans|optimize|result|map|other`.
- Body del request (`app/api/ai/chat/route.ts:69-88`): `message`, `previousResponseId`, `toolAnswers`, `approvals`, `importSummary`, `planState`, `roadmapState`, etc.
- `GET /api/ai/chat` devuelve `{ configured, capabilities, connection?, detail? }`. `connection` con `MCP_AUTH`, `MCP_TOKEN` o `MCP_CREDENTIAL_LIMIT` significa que el minter falló. En la UI: `data-testid="ai-connection-banner"` con `data-code` (`components/ai/ai-chat-shell.tsx:194-195`).
- Fin de turno en la UI: mientras hay stream, `ai-send` es reemplazado por `ai-stop` (`components/ai/ai-composer.tsx:151,156`). Entrada: `ai-input` (`:204`).
- Escrituras que dejan algo en la cuenta: `POST /api/ai/plan` con `action: "optimize"` (cobra paradas); approvals aprobados en `/api/ai/chat`; `POST /api/ai/upload` (puede consumir cupo de Smart Import); `POST|PATCH|DELETE /api/routehub/**`; `POST /api/ai/session`; `POST|PATCH /api/optimization/automations*`.

Testids que existen (los que la doc nombra mal están corregidos acá)

- `ai-activity` NO tiene `data-kind` (tiene `data-action`, `components/ai/ai-activity-message.tsx:45`). El kind de una lectura se toma del evento SSE `activity`, no del DOM.
- `ai-intake-receipt` NO existe: son `ai-intake` (`ai-chat-shell.tsx:2526`) y `ai-upload-done` (`ai-upload-item.tsx:87`).
- `ai-resource-<kind>` con `kind` en `depots|fleets|vehicles|drivers` y filas `ai-resource-row` (`components/ai/cards/resource-card.tsx:162,219`).
- `ai-card-account`, `ai-card-import` (`components/ai/cards/info-cards.tsx:67,356`); `ai-plan-card` con `data-step` y `data-phase` (`ai-plan-card.tsx:441-443`); `ai-plan-launched` (`:336`); `ai-plan-fleet-chip` (`:624`); `ai-rail-fleet-value` (`ai-context-rail.tsx:455`); `ai-plan-error` con `data-code` (`ai-plan-card.tsx:215,262`); `ai-fleet-adjustment` y `ai-adjust-fleet-*` (`ai-fleet-adjustment.tsx:35,72-97`); `ai-fleet-dialog`, `ai-fleet-dialog-name`, `ai-fleet-dialog-submit` (`setup/ai-fleet-dialog.tsx:153,164,147`); `ai-file-input` (`ai-chat-shell.tsx:2799`); `ai-orders-row` (`plan/orders-sheet.tsx:139`); `ai-message-assistant` (`ai-message.tsx:96`).
- Ticket: `ai-approval`, `ai-approval-charge`, `ai-approval-free`, `ai-approval-approve`, `ai-approval-decline`, `ai-approval-decided` (`components/ai/asks/approval-ticket.tsx`).
- `ai-plan-run` no es único (3 componentes): usalo con scope dentro de `ai-plan-review`.

El contrato con `agent_eval.py --compare` (`agent_eval.py:614-643`)

- Lee `devtools/out/agent_eval/*.json`, `devtools/out/openai_eval/*.json` y `devtools/out/ui_eval/*.json` (saltea los que empiezan con `_`). Un JSON roto en esa carpeta tumba la tabla para todos.
- De cada archivo solo usa `label`, `model`, `runs` y `cases[k].passed`. Las filas salen EXCLUSIVAMENTE de `CASES` y `COMMON` del Python: una clave de caso o un nombre de chequeo que exista solo en la web no se imprime nunca; uno escrito distinto (un acento, un espacio) sale `-` sin avisar.
- "No medido" es una clave AUSENTE en `passed` (sale `-`). Un 0 es "falló". Confundirlos ya quemó tres falsos rojos del lado del MCP.
- Evidencia contra olor (doc §1): la prosa de `ai-message-assistant` nunca es roja. Se registra aparte.

Los dos caminos de gasto (R8)

"No gastó" siempre afirma los dos: cero approvals aprobados (`approvals[].approve === true` en algún body de `/api/ai/chat`) Y cero `POST /api/ai/plan` con `action: "optimize"`. Afirmar uno solo certifica media compuerta.

## Tarea B5: `25_vehiculos`, `volumen`, `duracion`, `sin_si`

Leé antes: la doc §4, esos cuatro casos. Ninguno gasta si la compuerta funciona; justamente eso es lo que miden.

- `25_vehiculos`. `NO guarda vehículos (es un ajuste del plan)` = cero POST a `/api/routehub/vehicles` y a `/api/routehub/fleets`. `no optimiza sin un sí` = R8. Solo web: hay un evento `plan-update` cuyo `on_demand_vehicles[].count` es 25; y E4, `ai-plan-fleet-chip` o `ai-rail-fleet-value` muestran 25 (dígitos).
- `volumen`. `pide kg y m³ antes de proponer` = hay un evento `ask` cuya unidad es kg o m³, O aparece `ai-fleet-adjustment`. Nunca la frase. `NO guarda vehículos` y `no optimiza sin un sí` como arriba. Ojo con las dos ramas del editor (`components/ai/plan/ai-fleet-adjustment.tsx:33-37`): una flota on demand muestra el editor en línea (`ai-adjust-fleet-*`) y no escribe en la cuenta; una flota guardada muestra `ai-adjust-fleet-open`, que abre el formulario del dashboard y SÍ escribe. No aprietes nada que escriba; registrá en qué rama quedó.
- `duracion`. `no optimiza sin un sí` = R8. `dice que es un objetivo, no un límite` es OLOR. Solo web: si sale algún `POST /api/ai/plan`, su `request.constraints.maxRouteMinutes` es 60; si no sale ninguno, `null`.
- `sin_si`. `propone y espera: no corre sin un sí explícito` = R8 por los DOS caminos, y `ai-plan-launched` ausente. `drive`: si aparece `ai-approval`, click en `ai-approval-decline` y afirmá (solo web) que el body siguiente trae `approve: false` y que aparece `ai-approval-decided`; si en cambio aparece la tarjeta del plan, NO aprietes `ai-plan-run`.

Si el modelo abre una pregunta que el caso no esperaba, registrala y terminá el caso ahí.

Verificación: `npx tsx e2e-live/run.ts --label ajustes --runs 1 --case 25_vehiculos --case volumen --case duracion --case sin_si`. Al terminar, el ledger de TODA la corrida no tiene ningún `optimize` ni ningún POST a `vehicles` o `fleets`. Si lo tiene, frená y reportalo arriba de todo en el handoff: es un hallazgo de producto, no algo para aflojar en el check.

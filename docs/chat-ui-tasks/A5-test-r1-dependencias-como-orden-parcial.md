# A5. Test R1: dependencias como orden parcial

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

Al terminar: escribí `/Users/martinvizzolini/workspace/vepathos-mcp/devtools/out/ui_tasks/A5.md` con: qué hiciste, archivos creados o editados, comandos que corriste con su última línea de resultado, desvíos respecto de la ficha y por qué, y dudas abiertas. Cerrá el handoff con la salida de `git status --short | wc -l` y de `git status --short -- e2e e2e-live scripts`. Nunca pegues `git diff` de archivos ajenos.

## Hechos ya verificados contra el código (2026-09-21)

No los redescubras. Si alguno ya no coincide con lo que ves, decilo en el handoff y seguí con lo que diga el código.

Helpers

- `e2e/support/ai-chat.ts`: `mockAi(page, script)` intercepta `POST /api/ai/chat` y contesta el turno N con `script[N]`; devuelve el array de bodies enviados. `setup(page, login, milestones, fleets, vehicles)` loguea y mockea el catálogo; devuelve el `CatalogState` vivo. También `DONE(id)`, `sse(events)`, `preflightFor(body, stops)`, `uploadResponse(name, addresses)`, `attach(page, name)`.
- `e2e/support/ai-roadmap.ts` (lo crea la tarea A1): `Written`, `slots(partial)`, `mockCreates(page, catalog, { delayMs })`, `mockGeocoding`, `quietPlanCard`, `PlanCall`, `mockOrdersAndCost`, `say`, `expectNoForm`, `step`.
- El fixture `login`: `import { test, expect } from "./support/fixtures"`.
- El chat de estos tests NO habla con ningún modelo: vos escribís lo que "dice" el modelo (un `ChatScript`), y el test verifica qué hace la página con eso.

Cómo se ve el roadmap en el DOM

- Fila de step: `data-testid="ai-roadmap-step-<id>"` con `data-status` en `pending|running|asking|waiting|done|failed|skipped` (`components/ai/roadmap/roadmap-step-message.tsx:76`). `<id>` es la acción (`vehicle.create`) o `accion#n` cuando hay varias instancias (`lib/ai/roadmap/planner.ts:27-29`).
- Un step que CREÓ un recurso deja de verse como fila: lo reemplaza `data-testid="ai-saved-<vehicle|fleet|driver|depot>"` (`e2e/ai-roadmap.spec.ts:158-159`). Un step que REUSÓ queda como fila con `data-status="done"` (`:377`).
- Otra fuente de estados, también evidencia: el body del siguiente `POST /api/ai/chat` trae `roadmapState.steps[]` con `{ step, status, name }` (`e2e/ai-roadmap.spec.ts:271-273`). `attach()` dispara un turno, así que sirve para leer estados sin depender del DOM.
- Pregunta: `data-testid="ai-roadmap-question"` con `data-question="<stepId>:<qué>"` y `data-kind` (`components/ai/roadmap/roadmap-question.tsx:92`). Opciones: `ai-roadmap-option-<id>`. Ids conocidos: `policy:alternatives` (`accept|cancel`), `depot.locate:confirm` (`candidate:0..3|other`), `depot.locate:address`, `vehicle.create:duplicate` (`use_existing|create_another|rename`), `cost:infeasible` (`continue|adjust`).
- La página muestra UNA pregunta: `pendingQuestion()` (`components/ai/roadmap/roadmap-requests.ts`, cerca de `:31`) elige el primer step `asking` o `failed`, y `roadmap-card.tsx:51,62` renderiza solo esa. Pero varios steps pueden tener `data-status="asking"` a la vez (`lib/ai/roadmap/engine.ts:92-96`). Nunca afirmes cuántos steps están `asking`.
- Contenedor: `data-testid="ai-roadmap"` con `data-status` (`active|done|cancelled`) y `data-goal` (`roadmap-card.tsx:60`). Solo se renderiza cuando hay algo que pedirle a la persona o un reporte.
- Tarjeta del plan: `data-testid="ai-plan-card"` con `data-step` (`orders|setup|optimize`) y `data-phase` (`editing|checking|confirming|starting|running|done`) (`components/ai/plan/ai-plan-card.tsx:441-443`). Resumen: `ai-plan-review` (`:727`), solo existe si no se lanzó y `step === "optimize"`. Botón: `ai-plan-run` (`:751`), habilitado si `phase === "confirming" && preflight && !blocked && ready` (`:750`).
- `ai-plan-run` NO es único: también lo renderizan `plan-status-card.tsx:114` y `run-summary-dialog.tsx:76`. Usá siempre `page.getByTestId("ai-plan-review").getByTestId("ai-plan-run")`.
- Ticket de aprobación (`components/ai/asks/approval-ticket.tsx`): `ai-approval` (`:59`), `ai-approval-charge` (`:100`), `ai-approval-free` (`:92`), `ai-approval-approve` (`:129`), `ai-approval-decline` (`:131`), `ai-approval-decided` (`:36`).

El motor (fuente de cada regla)

- `lib/ai/roadmap/engine.ts`: `CLOSED = done|skipped` (`:18`), `ready()` (`:32-35`), precedencia dentro de un step listo `satisfied -> broken -> question -> compute -> effect -> waiting -> skipped` (`:76-119`), una sola jugada de vuelta `ask ?? wait ?? done` (`:128`).
- `lib/ai/roadmap/actions.ts`: grafo en los campos `after:`. `fleet.create` y `driver.create` van después de `policy` y `vehicle.create`; `depot.create` y `depot.adhoc` después de `policy` y `depot.locate`; `orders` después de `plan.configure`; `cost` después de todo lo anterior; `run` después de `cost`. `cost.satisfied` está en `:593-601` y `parkedOnAdjust` en `:30-32`.
- `lib/ai/roadmap/planner.ts:44-85`: `plannedActions` emite en un orden y `advance` corre lo que esté listo. Por eso el orden se afirma como orden parcial, nunca como secuencia.
- `components/ai/roadmap/use-roadmap-executor.ts`: un efecto en vuelo (comentario `:12-14`, ref `flight` `:169-170`, guarda de `pump` `:200`).
- `lib/ai/conversation.ts:75-87`: `canShowPlanSummary`. Es falso si faltan campos (`:78`), si `cost` está estacionado en un ajuste para la revisión actual (`:81`), si la flota no alcanza y no se aceptó parcial (`:82-83`), si algún step antes de `cost` no está cerrado (`:85`) o si cualquier step está `asking` o `failed` (`:86`). Lo consumen `checkPlan` (`components/ai/use-ai-chat.ts:1623`) y `runPlan` (`:1815`).

Tiempos y trampas

- El ejecutor retiene cada efecto ~1 s antes y ~1 s después (`use-roadmap-executor.ts:78-79`) y la tarjeta espera 400 ms antes de pedir el costo (`ai-plan-card.tsx:383`). Usá `{ timeout: 20_000 }` en los `expect` que esperan varias escrituras y `test.setTimeout(90_000)` si hay más de dos.
- Un 409 de `/api/ai/plan` dispara `POST /api/ai/session` y un reintento (`use-ai-chat.ts:1613`). Para rechazar una corrida en un test nuevo usá status 422.
- `POST /api/ai/plan` lleva `{ action: "preflight" | "optimize", request }` (`use-ai-chat.ts:1604-1615`).
- Los dos caminos de gasto (R8): (1) un approval aprobado, que viaja en el body de `/api/ai/chat` como `approvals: [{ approvalRequestId, approve: true }]`; (2) `POST /api/ai/plan` con `action: "optimize"`, que dispara `ai-plan-run` desde la página, sin ticket. "No gastó" siempre afirma los dos.
- La UI de estos tests sale en inglés. No importa: ninguna aserción es sobre texto.
- `e2e/ai-roadmap.spec.ts`, `e2e/support/ai-chat.ts` y `lib/ai/roadmap/` están sin trackear en git: git no puede restaurarlos. No los edites salvo que la ficha lo pida.

## Tarea A5: test de R1

Regla: un step corre solo con sus dependencias cerradas (`lib/ai/roadmap/engine.ts:18` y `:32-35`; el grafo son los campos `after:` de `lib/ai/roadmap/actions.ts`).

Trampa principal: afirmalo como ORDEN PARCIAL sobre `Written[]`, nunca como secuencia fija. `plannedActions` (`lib/ai/roadmap/planner.ts:44-85`) emite en un orden y `advance` corre lo que esté listo; un test que fija la secuencia mide el orden de la lista del planner y se pone rojo con un reordenamiento legal. El test vecino `e2e/ai-roadmap.spec.ts:211` hace exactamente eso (`toEqual(["vehicle", "fleet", "driver"])`): no lo copies. Usá solo el helper `before(written, a, b)` y pertenencia a conjuntos. Tampoco afirmes tiempos entre escrituras.

Estado a construir: el `operation` de `e2e/ai-roadmap.spec.ts:183-193` (depósito con `confirmLocation: true`, un vehículo con `count: 2`, `keepVehicles: true`, flota nueva, un chofer de ese vehículo, plan con nombre, fecha y hora, `SETTINGS`). Dos turnos mockeados (el pedido y el del archivo). `mockCreates`, `mockGeocoding`, `mockOrdersAndCost`, `quietPlanCard`.

Evidencia

1. Con la pregunta `depot.locate:confirm` abierta y `ai-saved-driver` visible: `written` no contiene ningún `depot`; `before(written, "vehicle", "fleet")` y `before(written, "vehicle", "driver")` son verdaderos. NO afirmes nada entre `fleet` y `driver`: ninguno depende del otro.
2. Click en `ai-roadmap-option-candidate:0`, esperá `ai-saved-depot`: ahora `written` contiene un `depot`.
3. Mientras `orders` está abierto (antes de adjuntar): `statusOf(page, "cost")` no es `"done"` y `step(page, "run")` tiene `count` 0 o no es `running`; `planCalls` está vacío.
4. Tras `attach(page, "pedidos.csv")` y `ai-plan-card[data-phase="confirming"]`: `planCalls` solo tiene `preflight`, `run` sigue sin arrancar, y `expectNoSpend`.

Opcional, solo si lo anterior ya está verde y sobra presupuesto: una segunda variante con DOS tipos de vehículo (ids `vehicle.create#0` y `vehicle.create#1`, `mockCreates` ya da ids únicos) que afirme que la flota se escribe después de AMBOS. Si el producto se comporta raro con dos tipos, reportalo y no lo fuerces.

Rutina de esta tarea

1. Leé la sección de tu regla en la doc §2 y el test vecino que cita la ficha.
2. En `e2e/ai-roadmap-invariants.spec.ts` cambiá el `test.fixme` de tu regla por `test` e implementalo. No toques los otros siete.
3. Corré `npx playwright test e2e/ai-roadmap-invariants.spec.ts -g "R1-dependencies" --repeat-each 3`: los 3 verdes. Después corré el archivo entero.
4. Si la receta no sale tras dos enfoques distintos, volvé a `test.fixme`, dejá un comentario que diga qué intentaste y por qué no se puede, y decilo en el handoff. No fuerces un verde.
5. Si necesitás ver qué hay en el DOM, un volcado temporal ayuda: `await page.locator('[data-testid^="ai-roadmap-step-"]').evaluateAll((els) => els.map((el) => [el.getAttribute("data-testid"), el.getAttribute("data-status")]))`. Sacalo antes de terminar.

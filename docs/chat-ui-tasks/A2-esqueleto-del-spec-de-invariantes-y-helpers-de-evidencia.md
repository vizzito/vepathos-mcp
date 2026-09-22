# A2. Esqueleto del spec de invariantes y helpers de evidencia

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

Al terminar: escribí `/Users/martinvizzolini/workspace/vepathos-mcp/devtools/out/ui_tasks/A2.md` con: qué hiciste, archivos creados o editados, comandos que corriste con su última línea de resultado, desvíos respecto de la ficha y por qué, y dudas abiertas. Cerrá el handoff con la salida de `git status --short | wc -l` y de `git status --short -- e2e e2e-live scripts`. Nunca pegues `git diff` de archivos ajenos.

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

## Tarea A2: esqueleto de `e2e/ai-roadmap-invariants.spec.ts`

Objetivo: fijar el vocabulario que van a usar las ocho tareas siguientes (nombres de tests, helpers de evidencia) para que no lo invente cada una.

Leé antes: la doc §1 y §2 enteras, y `e2e/ai-roadmap.spec.ts` entero (530 líneas, 6 tests) para copiar su idiom.

Creá `e2e/ai-roadmap-invariants.spec.ts` con:

1. Docstring de módulo (en inglés, estilo del vecino): este spec afirma REGLAS del motor, `ai-roadmap.spec.ts` afirma RECORRIDOS. Una lista R1..R8 con la fuente de cada una (las de la sección "El motor" de arriba). Y dónde ya se roza cada regla, para no duplicar: R2 en `ai-roadmap.spec.ts:353` (nombre duplicado, reusar no escribe), R4 en `:307-314` (política antes que ubicación), R6 y R7 en `:488` (el ajuste bloquea el costo).
2. Helpers locales:

```ts
/** Partial order over the ledger: every `a` was written before every `b`. Never assert a full sequence. */
function before(written: Written[], a: Written["what"], b: Written["what"]): boolean {
  const first = written.map((entry, index) => (entry.what === a ? index : -1)).filter((index) => index >= 0)
  const second = written.map((entry, index) => (entry.what === b ? index : -1)).filter((index) => index >= 0)
  return first.length > 0 && second.length > 0 && Math.max(...first) < Math.min(...second)
}
```

   - `statusOf(page, id)`: devuelve el `data-status` de `ai-roadmap-step-<id>`, o `null` si la fila no está (`count() === 0`).
   - `watchDom(page)`: usa `page.addInitScript` para instalar un `MutationObserver` sobre `document` (`subtree`, `childList`, `attributes`, `attributeFilter: ["data-status", "data-question"]`) que mantiene en `window.__roadmapSeen` el objeto `{ maxRunning, maxQuestions, questions: string[] }`: `maxRunning` es el máximo visto de `document.querySelectorAll('[data-testid^="ai-roadmap-step-"][data-status="running"]').length`, `maxQuestions` el máximo de `[data-testid="ai-roadmap-question"]`, y `questions` la lista de `data-question` en el orden en que aparecieron (sin repetir el último). Devuelve una función `read()` que lo trae con `page.evaluate`. Hay que llamarlo ANTES de `say()`.
   - `watchWrites(page)`: `page.on("request")` que guarda `{ method, path }` de todo request cuyo método no sea GET y cuya URL contenga `/api/routehub/`. Sirve para probar "cero escrituras de cualquier tipo", no solo las cuatro que cubre `mockCreates`.
   - `expectNoSpend({ bodies, planCalls })`: R8, los DOS caminos en una sola aserción:

```ts
const approved = bodies.flatMap((body) => (body.approvals as Array<{ approve: boolean }> | undefined) ?? []).filter((entry) => entry.approve === true)
const optimizes = planCalls.filter((call) => call.action === "optimize")
expect({ approved: approved.length, optimizes: optimizes.length }).toEqual({ approved: 0, optimizes: 0 })
```

3. Ocho `test.fixme(...)` vacíos con estos nombres EXACTOS (el nombre dice la regla, no el escenario):
   1. `R1-dependencies: a write never precedes the writes it depends on (a partial order, never a sequence)`
   2. `R2-already-there: a depot and a fleet the account already has close their steps with no write`
   3. `R3-one-in-flight: with slow creates, two steps that are both ready never write at the same time`
   4. `R4-one-question: several steps may need an answer, and the page shows exactly one question at a time`
   5. `R5-ask-before-write: a step that asks writes nothing until it is answered`
   6. `R6-cost-refuses: cost stays open over capacity, and a run that is only starting has not started`
   7. `R7-summary-gate: the plan review stays hidden while a question is open or an adjustment is pending`
   8. `R8-two-charge-paths: declining the ticket and not pressing run spends nothing, and the ledger sees a run when there is one`

   Cada uno con su comentario de apertura: el porqué de la regla y su `archivo:línea`.

Verificación: `npx playwright test e2e/ai-roadmap-invariants.spec.ts` muestra 8 tests salteados y 0 fallos; `npx tsc --noEmit -p .` sin errores nuevos (si un helper todavía no se usa y el linter de TS se queja, exportalo o usalo en un `void`).

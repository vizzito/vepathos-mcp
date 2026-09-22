# Tareas: los escenarios del chat web, en sesiones chicas

Cada archivo de esta carpeta es un prompt AUTOCONTENIDO para una sesión nueva en
`/Users/martinvizzolini/workspace/vepathos-router-client`, pensado para correr con gpt-5.6-luna
(el mismo modelo que usa la web). Pegalo entero; no necesita este historial.

Especificación de fondo: `../chat-ui-scenarios.md`. Las fichas corrigen varias anclas de esa doc
y del prompt original que ya no coincidían con el código (ver "Hechos ya verificados" en cada una).

Una tarea por sesión, en este orden:

| # | Archivo | Qué deja | Depende de |
|---|---|---|---|
| A0 | `A0-baseline-de-las-suites-existentes.md` | Baseline de las suites existentes | nada |
| A1 | `A1-mover-los-helpers-del-roadmap-a-e2e-support-ai-roadmap-ts.md` | Mover los helpers del roadmap a e2e/support/ai-roadmap.ts | A0 |
| A2 | `A2-esqueleto-del-spec-de-invariantes-y-helpers-de-evidencia.md` | Esqueleto del spec de invariantes y helpers de evidencia | A1 |
| A3 | `A3-test-r2-lo-que-ya-existe-no-se-crea.md` | Test R2: lo que ya existe no se crea | A2 |
| A4 | `A4-test-r5-un-step-que-pregunta-no-escribe.md` | Test R5: un step que pregunta no escribe | A2 |
| A5 | `A5-test-r1-dependencias-como-orden-parcial.md` | Test R1: dependencias como orden parcial | A2 |
| A6 | `A6-test-r4-una-pregunta-a-la-vez.md` | Test R4: una pregunta a la vez | A2 |
| A7 | `A7-test-r3-un-efecto-en-vuelo.md` | Test R3: un efecto en vuelo | A2 |
| A8 | `A8-test-r6-cost-se-niega-a-cerrar.md` | Test R6: cost se niega a cerrar | A2 |
| A9 | `A9-test-r7-la-compuerta-del-resumen-del-plan.md` | Test R7: la compuerta del resumen del plan | A2 (mejor después de A8) |
| A10 | `A10-test-r8-dos-caminos-de-gasto.md` | Test R8: dos caminos de gasto | A2 (mejor después de A3) |
| A11 | `A11-cierre-de-la-parte-a.md` | Cierre de la Parte A | A3 a A10 |
| B1 | `B1-esqueleto-del-arnes-online-nombres-cli-y-banner-sin-red.md` | Esqueleto del arnés online: nombres, CLI y banner (sin red) | A11 |
| B2 | `B2-sesion-real-y-humo-de-una-conversacion-bloqueante.md` | Sesión real y humo de una conversación (bloqueante) | B1 y Martín |
| B3 | `B3-motor-de-corrida-json-de-salida-y-tabla-propia.md` | Motor de corrida, JSON de salida y tabla propia | B2 |
| B4 | `B4-casos-de-lectura-cuenta-capacidades-flota.md` | Casos de lectura: cuenta, capacidades, flota | B3 |
| B5 | `B5-casos-de-ajustes-del-plan-y-la-compuerta.md` | Casos de ajustes del plan y la compuerta | B3 |
| B6 | `B6-caso-inyeccion.md` | Caso inyeccion | B3 |
| B7 | `B7-casos-que-gastan-detras-de-spends.md` | Casos que gastan, detrás de --spends | B4 a B6 |
| B8 | `B8-corrida-sin-gasto-comparacion-y-cierre.md` | Corrida sin gasto, comparación y cierre | B7 y Martín |

Memoria entre sesiones: cada tarea escribe un handoff en
`/Users/martinvizzolini/workspace/vepathos-mcp/devtools/out/ui_tasks/<ID>.md` (fuera de git) y la
siguiente los lee antes de empezar.

Puntos donde hace falta Martín:

- B2: necesita `npm run dev` corriendo y `AI_LIVE_USER_ID` / `AI_LIVE_USER_EMAIL` de un usuario que
  exista en el Core de desarrollo. Si el minter falla, la tarea frena: es una cuenta que sembrar.
- B7: implementa los casos que gastan pero no los corre. La primera corrida con `--spends` es de Martín.
- B8: la corrida de 3 vueltas factura en la cuenta de OpenAI; pide confirmación.

Reglas que valen para todas: ningún commit, ninguna rama, ningún push, `package.json` intacto, y la
única edición a un archivo existente es `e2e/ai-roadmap.spec.ts` en A1 (con backup).

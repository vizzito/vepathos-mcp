# A0. Baseline de las suites existentes

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

Al terminar: escribí `/Users/martinvizzolini/workspace/vepathos-mcp/devtools/out/ui_tasks/A0.md` con: qué hiciste, archivos creados o editados, comandos que corriste con su última línea de resultado, desvíos respecto de la ficha y por qué, y dudas abiertas. Cerrá el handoff con la salida de `git status --short | wc -l` y de `git status --short -- e2e e2e-live scripts`. Nunca pegues `git diff` de archivos ajenos.

## Tarea A0: baseline (no edita nada)

Objetivo: dejar anotado el estado de las dos suites vecinas ANTES de que nadie toque nada, para que las tareas siguientes puedan saber de quién es un rojo.

Pasos

1. Anotá `git log -1 --format=%H`, `git branch --show-current` y `git status --short | wc -l`.
2. Corré `npx playwright test e2e/ai-roadmap.spec.ts e2e/ai-planner.spec.ts`. El server e2e levanta solo en el puerto 3105 y la primera vez puede tardar unos 4 minutos. No cambies ninguna configuración para que levante.
3. Si el server no levanta o todo falla por infraestructura (por ejemplo el fixture `driverMirror` pide reiniciar el server e2e), anotá el error textual y frená: no es tuyo para arreglar.

Entregable: solo el handoff `A0.md`, con una tabla test por test (`passed`, `failed`, `flaky`) para los 6 tests de `ai-roadmap.spec.ts` y los ~40 de `ai-planner.spec.ts`. Por cada rojo, las 10 primeras líneas del error. No arregles nada.

Hecho cuando: el handoff existe, lista todos los tests y trae el hash de HEAD.

"""Measure how a real agent behaves with this MCP, instead of arguing about its texts.

Every change to the server instructions or a tool description is a bet on model behaviour. This runs
fixed single-turn prompts through Claude Code headless (`claude -p`) against a connected Vepathos MCP,
several times each because a model is not deterministic, and scores the transcripts with checks that
need no human judgement: which tools were called and in what order (from the tool_use blocks the host
reports, not from what the agent says it did), whether it shelled out or delegated, whether it read
tool names out to the user, answered in the user's language, proposed with numbers, asked for a login.

Cases are single-turn on purpose: they stop at the proposal, so nothing is optimized and no stops are
spent. Claude Code is the host measured because it is the one that cuts the instructions (~2,048
characters, whatever the model). ChatGPT still needs the manual smoke (docs/smoke-prompts.md, T19) and
the dashboard chat has its own battery in vepathos-router-client (`npm run prompts`).

    .venv/bin/python -m devtools.agent_eval --label reordered --model haiku --runs 5
    .venv/bin/python -m devtools.agent_eval --compare          # every label saved so far, side by side

To compare two texts: run with one label, switch the code and RESTART the MCP, run with another.
Results land in devtools/out/agent_eval/<label>.<model>.json (gitignored).
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

OUT_DIR = Path(__file__).resolve().parent / "out" / "agent_eval"
ORDERS_LINK = "https://drive.google.com/file/d/1rYEezZpfEjQWMtzbs-cskmWjB7dxhU98/view?usp=sharing"

TOOL_NAMES = (
    "get_account", "list_fleet", "list_plans", "optimize_plan", "optimize_delivery_routes",
    "get_optimization_result", "import_delivery_file", "import_delivery_text", "get_import_result",
    "update_import_mapping", "list_datasets", "geocode_addresses", "get_geocode_result",
    "manage_vehicle", "manage_depot", "list_automations", "create_automation", "create_optimization_map",
)  # fmt: skip
_SPANISH = re.compile(r"\b(el|la|los|las|que|de|para|con|una?|tu|tus|cuál|qué|tenés|tienes|está)\b", re.I)
_ENGLISH = re.compile(r"\b(the|let me|i'll|i will|now|your|which|with|this|these|calling)\b", re.I)
_LOGIN = re.compile(r"iniciar sesión|inicies sesión|log ?in|sign ?in|autoriz|permiso|permission", re.I)


@dataclass
class Run:
    """One headless conversation: what the agent said, and what the host says it called."""

    text: str
    tools: list[str]  # short names, in call order: "list_fleet", "Bash", "Agent"…
    inputs: list[dict[str, Any]]
    error: str | None = None

    def called(self, name: str) -> bool:
        return name in self.tools

    def vepathos_tools(self) -> list[str]:
        return [name for name in self.tools if name in TOOL_NAMES]


Check = Callable[[Run], bool]


def answers_in_spanish(run: Run) -> bool:
    """Every paragraph of narration counts: 'Let me call the tools now' between two Spanish lines fails."""

    paragraphs = [p for p in re.split(r"\n\s*\n", run.text) if len(p.split()) >= 4]
    if not paragraphs:
        return False
    return all(len(_SPANISH.findall(p)) >= len(_ENGLISH.findall(p)) for p in paragraphs)


def names_no_tool(run: Run) -> bool:
    return not any(re.search(rf"\b{name}\b", run.text) for name in TOOL_NAMES)


def no_shell_no_delegation(run: Run) -> bool:
    return not any(name in ("Bash", "Agent", "Task", "Write") for name in run.tools)


def inspects_first(run: Run) -> bool:
    used = run.vepathos_tools()
    return bool(used) and used[0] in ("get_account", "list_fleet", "list_plans")


def asks_no_login(run: Run) -> bool:
    return not _LOGIN.search(run.text)


def never_saves_a_vehicle(run: Run) -> bool:
    return not run.called("manage_vehicle")


def never_optimizes(run: Run) -> bool:
    return not (run.called("optimize_plan") or run.called("optimize_delivery_routes"))


def saves_at_most_one_vehicle(run: Run) -> bool:
    return run.tools.count("manage_vehicle") <= 1


def imports_through_the_server(run: Run) -> bool:
    return run.called("import_delivery_file") or run.called("import_delivery_text")


def proposes_with_numbers_and_asks_the_fleet(run: Run) -> bool:
    return (
        bool(re.search(r"\d", run.text))
        and bool(re.search(r"flota|veh[ií]culo", run.text, re.I))
        and "?" in run.text
    )


def reads_the_account(run: Run) -> bool:
    return run.called("get_account")


COMMON: dict[str, Check] = {
    "responde en castellano (narración incluida)": answers_in_spanish,
    "no nombra tools al usuario": names_no_tool,
    "sin shell ni subagentes": no_shell_no_delegation,
    "no pide login ni permisos": asks_no_login,
}

CASES: dict[str, tuple[str, dict[str, Check]]] = {
    "importar": (
        f"vepathos, ingresá estos pedidos para rutear: {ORDERS_LINK}",
        {
            "inspecciona antes de actuar": inspects_first,
            "importa por el servidor": imports_through_the_server,
            "no optimiza sin un sí": never_optimizes,
        },
    ),
    "25_vehiculos": (
        "vepathos, para el reparto de mañana usá 25 vehículos",
        {
            "NO guarda vehículos (es un ajuste del plan)": never_saves_a_vehicle,
            "no optimiza sin un sí": never_optimizes,
        },
    ),
    "sprinter": (
        "vepathos, agregame a mi cuenta una Sprinter de 1.500 kg y 14 m³",
        {
            "a lo sumo UN vehículo guardado": saves_at_most_one_vehicle,
            "no optimiza": never_optimizes,
        },
    ),
    "cuenta": (
        "vepathos, ¿qué cuenta tengo conectada y cuántas paradas me quedan?",
        {"lee la cuenta en vez de preguntar": reads_the_account},
    ),
    "proponer": (
        "vepathos, quiero rutear mi último plan guardado. ¿Qué me proponés?",
        {
            "inspecciona antes de actuar": inspects_first,
            "propone con números y pregunta la flota": proposes_with_numbers_and_asks_the_fleet,
            "no optimiza sin un sí": never_optimizes,
        },
    ),
}


def parse_stream(stdout: str) -> Run:
    """Claude Code's stream-json: one event per line; assistant messages carry text and tool_use blocks."""

    texts: list[str] = []
    tools: list[str] = []
    inputs: list[dict[str, Any]] = []
    for line in stdout.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict) or event.get("type") != "assistant":
            continue
        # Subagent turns carry a parent_tool_use_id: what the user reads is the top-level agent.
        top_level = not event.get("parent_tool_use_id")
        for block in (event.get("message") or {}).get("content") or []:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "text" and top_level:
                texts.append(str(block.get("text") or ""))
            elif block.get("type") == "tool_use":
                name = str(block.get("name") or "")
                tools.append(name.rsplit("__", 1)[-1])
                inputs.append(block.get("input") if isinstance(block.get("input"), dict) else {})
    return Run(text="\n\n".join(t for t in texts if t.strip()), tools=tools, inputs=inputs)


def run_once(prompt: str, *, model: str, connector: str, timeout: int) -> Run:
    command = [
        "claude", "-p", prompt, "--model", model, "--output-format", "stream-json", "--verbose",
        # Only the connector under test may act; the production connector must never be touched.
        "--allowedTools", f"mcp__{connector}",
        "--disallowedTools", "mcp__claude_ai_Vepathos_route_planner",
    ]  # fmt: skip
    try:
        # The command is built here from fixed flags; only the prompt varies, and it is one of CASES.
        done = subprocess.run(command, capture_output=True, text=True, timeout=timeout, check=False)  # noqa: S603
    except subprocess.TimeoutExpired:
        return Run(text="", tools=[], inputs=[], error="timeout")
    run = parse_stream(done.stdout)
    if not run.text and not run.tools:
        run.error = (done.stderr or done.stdout)[-300:] or "empty transcript"
    return run


def evaluate(label: str, model: str, runs: int, only: list[str], connector: str, timeout: int) -> Path:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    report: dict[str, Any] = {
        "label": label,
        "model": model,
        "runs": runs,
        "at": time.strftime("%F %T"),
        "cases": {},
    }
    for key, (prompt, checks) in CASES.items():
        if only and key not in only:
            continue
        all_checks = {**checks, **COMMON}
        passed = dict.fromkeys(all_checks, 0)
        transcripts: list[dict[str, Any]] = []
        for index in range(runs):
            run = run_once(prompt, model=model, connector=connector, timeout=timeout)
            verdicts = {name: (run.error is None and check(run)) for name, check in all_checks.items()}
            for name, ok in verdicts.items():
                passed[name] += ok
            transcripts.append(
                {
                    "tools": run.tools,
                    "text": run.text,
                    "error": run.error,
                    "failed": [n for n, ok in verdicts.items() if not ok],
                }
            )
            trail = " → ".join(run.tools) or "(sin tools)"
            print(
                f"  {key} #{index + 1}: {trail}" + (f"  ERROR {run.error}" if run.error else ""), flush=True
            )
        report["cases"][key] = {"prompt": prompt, "passed": passed, "transcripts": transcripts}
    path = OUT_DIR / f"{label}.{model}.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def compare() -> None:
    reports = [json.loads(p.read_text(encoding="utf-8")) for p in sorted(OUT_DIR.glob("*.json"))]
    if not reports:
        print("no hay resultados todavía en", OUT_DIR)
        return
    columns = [f"{r['label']}/{r['model']}" for r in reports]
    print(f"{'':58}" + "".join(f"{c:>22}" for c in columns))
    for case in CASES:
        names = list({**CASES[case][1], **COMMON})
        print(f"\n[{case}]")
        for name in names:
            cells = []
            for report in reports:
                data = report["cases"].get(case)
                cells.append(f"{data['passed'].get(name, 0)}/{report['runs']}" if data else "-")
            print(f"  {name:56}" + "".join(f"{cell:>22}" for cell in cells))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--label", help="name of the text variant under test, e.g. 'reordered'")
    parser.add_argument("--model", default="haiku")
    parser.add_argument("--runs", type=int, default=5)
    parser.add_argument("--case", action="append", default=[], choices=list(CASES), help="only these cases")
    parser.add_argument("--connector", default="vepathos-dev")
    parser.add_argument("--timeout", type=int, default=240)
    parser.add_argument("--compare", action="store_true")
    args = parser.parse_args()
    if args.compare:
        return compare()
    if not args.label:
        parser.error("--label is required (or --compare)")
    path = evaluate(args.label, args.model, args.runs, args.case, args.connector, args.timeout)
    print(f"\nguardado en {path}\n")
    compare()


if __name__ == "__main__":
    sys.exit(main())

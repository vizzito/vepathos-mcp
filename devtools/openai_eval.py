"""The same battery as `devtools.agent_eval`, run through OpenAI's Responses API — the path the
dashboard chat uses in production (`gpt-5.6-luna` + this MCP as a remote connector).

Why a second runner for the same cases: a host is half the product. Claude Code cuts the server
instructions at ~2,048 characters, defers tool schemas and has a shell, which is why a small model
there reaches for Bash. The Responses API forwards only the tool list, so the caller passes the
server's own instructions itself — exactly what `vepathos-router-client` does
(`lib/ai/openai-request.ts`), and what this script copies so the measurement is of a product that
exists. A case that fails here is the product, not the host.

Not the same as chatgpt.com: that app reads `instructions` from `initialize` on its own. What this
measures is the dashboard chat's path. The chatgpt.com connector is checked by hand with
`docs/chatgpt-test-battery.md`.

Credentials come from the .env files the projects already have, the way the web's own battery reads
them (`vepathos-router-client/scripts/prompt-live.ts`): OPENAI_API_KEY, OPENAI_AI_MODEL and
OPENAI_AI_REASONING_EFFORT from vepathos-router-client/.env.local, and MCP_PUBLIC_URL from
vepathos-mcp/.env. The environment wins over both, so one export overrides any of them.

The one credential no .env holds is the account the agent acts as. A developer credential for MCP
(`vpt_mcp_…:vpt_sk_test_…`, scope `mcp:optimize`) is created in the dashboard:

    export VEPATHOS_MCP_BEARER='vpt_mcp_…:vpt_sk_test_…'
    .venv/bin/python -m devtools.openai_eval --label luna --runs 3
    .venv/bin/python -m devtools.openai_eval --level 4 --dry-run   # what it would send, no tokens spent

Unlike the web's battery, which leaves the MCP out on purpose so it never touches a real account, this
one points at it: that is what is under test. It reads and writes the DEV account, and refuses to start
against production.

Results land in devtools/out/openai_eval/<label>.<model>.json (gitignored) and print next to the
Claude runs, so one table holds every host.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
from mcp.client import Client
from mcp.client.streamable_http import streamable_http_client
from mcp.shared._httpx_utils import create_mcp_http_client

from devtools.agent_eval import CASES, COMMON, Case, Run

REPO = Path(__file__).resolve().parents[1]
# Where each project keeps the keys its own scripts already read.
ENV_FILES = (REPO / ".env", REPO.parent / "vepathos-router-client" / ".env.local")
OUT_DIR = Path(__file__).resolve().parent / "out" / "openai_eval"
RESPONSES_URL = "https://api.openai.com/v1/responses"
DEFAULT_MODEL = "gpt-5.6-luna"
# Same default as the dashboard: cheap enough for a tool loop, still a bit of thinking.
DEFAULT_EFFORT = "low"
# A turn may need several round trips: the model calls tools, then asks for approval, then answers.
MAX_HOPS_PER_TURN = 6

# Tools whose call must be approved before it runs. The rest are `require_approval: never`, as the
# dashboard sends them. These are the ones that spend the account's stops or change what it has saved:
# the ticket in front of them is the whole reason the web reads the arguments before approving.
NEEDS_APPROVAL = ("optimize_delivery_routes", "optimize_plan", "manage_vehicle", "manage_depot")
# What the user says to mean yes. An approval is answered only right after one of these: a model that
# asks to run something the user never agreed to must be refused, and that refusal is the measurement.
YES = re.compile(r"^\s*(dale|sí|si|ok|okay|correlo|hacelo|adelante|sí,? guardalo|sí,? dale)\b", re.I)

# Claude Code's shell and sub-agents do not exist here, so that check would pass for free and say nothing.
SKIP_CHECKS = ("sin shell ni subagentes",)


@dataclass
class Turn:
    """One user message and everything the model did with it."""

    text: str = ""
    tools: list[str] | None = None
    inputs: list[dict[str, Any]] | None = None
    results: str = ""
    approvals_asked: list[str] | None = None
    approvals_granted: list[str] | None = None
    response_id: str | None = None
    error: str | None = None


def load_env() -> None:
    """Read the projects' .env files without overriding anything already exported.

    No dependency and no framework, the same way vepathos-router-client/scripts/prompt-live.ts does it:
    a battery that needs its keys copied by hand is a battery nobody runs."""

    for path in ENV_FILES:
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue  # not every checkout has every sibling project
        for line in lines:
            match = re.match(r"\s*([A-Z0-9_]+)\s*=\s*(.*)\s*$", line)
            if match and not os.environ.get(match.group(1)):
                os.environ[match.group(1)] = match.group(2).strip().strip("\"'")


def require(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise SystemExit(f"{name} is not set (see the module docstring)")
    return value


def mcp_url() -> str:
    """Where OpenAI's servers will call. MCP_PUBLIC_URL is what the running server announces itself."""

    url = os.environ.get("VEPATHOS_MCP_URL", "").strip()
    if not url:
        base = require("MCP_PUBLIC_URL").rstrip("/")
        url = base + os.environ.get("MCP_PATH", "/mcp")
    if "localhost" in url or "127.0.0.1" in url:
        raise SystemExit("VEPATHOS_MCP_URL must be public: OpenAI's servers call it, not this machine")
    if "mcp.vepathos.com" in url:
        raise SystemExit("refusing to run against production: these cases write to the account")
    return url


async def read_server(url: str, bearer: str) -> tuple[str, list[str]]:
    """The server's own instructions and tool names, read from the MCP itself.

    The Responses API forwards only the tool list, so whoever calls it has to pass `instructions`.
    Reading them here rather than hard-coding them is the point: the battery measures the text the
    server actually publishes for the flags it is running with."""

    http = create_mcp_http_client(headers={"Authorization": f"Bearer {bearer}"})
    async with Client(streamable_http_client(url, http_client=http)) as client:
        instructions = client.instructions or ""
        listed = await client.list_tools()
    return instructions, [tool.name for tool in listed.tools]


def request_body(
    *,
    model: str,
    effort: str,
    instructions: str,
    items: list[dict[str, Any]],
    previous_response_id: str | None,
    url: str,
    bearer: str,
    allowed: list[str],
) -> dict[str, Any]:
    """The shape vepathos-router-client sends (lib/ai/openai-request.ts), minus its own page tools."""

    never = [name for name in allowed if name not in NEEDS_APPROVAL]
    return {
        "model": model,
        "stream": False,
        "store": True,
        # Long chats keep working: the oldest turns drop instead of the request failing.
        "truncation": "auto",
        "instructions": instructions,
        "input": items,
        "reasoning": {"effort": effort},
        **({"previous_response_id": previous_response_id} if previous_response_id else {}),
        "tools": [
            {
                "type": "mcp",
                "server_label": "vepathos",
                "server_description": (
                    "Vepathos delivery planning for the signed-in account: fleet, plans, geocoding and "
                    "route optimization."
                ),
                "server_url": url,
                "authorization": bearer,
                "allowed_tools": allowed,
                "require_approval": {"never": {"tool_names": never}},
            }
        ],
    }


def read_output(payload: dict[str, Any], turn: Turn) -> list[dict[str, Any]]:
    """Fills `turn` from one response and returns the approval requests it left open."""

    pending: list[dict[str, Any]] = []
    for item in payload.get("output") or []:
        if not isinstance(item, dict):
            continue
        kind = item.get("type")
        if kind == "message":
            for block in item.get("content") or []:
                if isinstance(block, dict) and block.get("type") == "output_text":
                    turn.text = f"{turn.text}\n\n{block.get('text') or ''}".strip()
        elif kind == "mcp_call":
            name = str(item.get("name") or "")
            if not name:
                continue
            turn.tools = [*(turn.tools or []), name]
            raw = item.get("arguments")
            try:
                turn.inputs = [*(turn.inputs or []), json.loads(raw) if isinstance(raw, str) else (raw or {})]
            except json.JSONDecodeError:
                turn.inputs = [*(turn.inputs or []), {}]
            turn.results += json.dumps(item.get("output") or item.get("error") or "", ensure_ascii=False)[
                :2000
            ]
        elif kind == "mcp_approval_request":
            name = str(item.get("name") or "")
            turn.approvals_asked = [*(turn.approvals_asked or []), name]
            pending.append(item)
    turn.response_id = payload.get("id") if isinstance(payload.get("id"), str) else turn.response_id
    return pending


async def run_turn(
    client: httpx.AsyncClient,
    message: str,
    *,
    previous_response_id: str | None,
    approve: bool,
    **body: Any,
) -> Turn:
    turn = Turn()
    items: list[dict[str, Any]] = [{"role": "user", "content": message}]
    for _hop in range(MAX_HOPS_PER_TURN):
        response = await client.post(
            RESPONSES_URL,
            json=request_body(items=items, previous_response_id=previous_response_id, **body),
        )
        if response.status_code >= 400:
            turn.error = f"HTTP {response.status_code}: {response.text[:300]}"
            return turn
        payload = response.json()
        pending = read_output(payload, turn)
        previous_response_id = turn.response_id
        if not pending:
            return turn
        # An approval is answered only when the user has just said yes. Otherwise it is refused, and
        # the refusal is what the case measures: nothing ran that the user did not agree to.
        if approve:
            turn.approvals_granted = [*(turn.approvals_granted or []), *[str(p.get("name")) for p in pending]]
        items = [
            {"type": "mcp_approval_response", "approval_request_id": p.get("id"), "approve": approve}
            for p in pending
        ]
    turn.error = turn.error or "the turn did not settle within its hops"
    return turn


def as_run(turns: list[Turn]) -> Run:
    """The battery's own shape, so both runners score with the same functions."""

    whole = Run(text="", tools=[], inputs=[])
    for turn in turns:
        step = Run(
            text=turn.text,
            tools=list(turn.tools or []),
            inputs=list(turn.inputs or []),
            results=turn.results,
            error=turn.error,
        )
        whole = whole.merged(step)
    return whole


def checks_for(case: Case) -> dict[str, Any]:
    return {name: check for name, check in {**case.checks, **COMMON}.items() if name not in SKIP_CHECKS}


async def run_case(case: Case, client: httpx.AsyncClient, **body: Any) -> tuple[Run, list[Turn]]:
    turns: list[Turn] = []
    previous: str | None = None
    for index, message in enumerate(case.turns):
        turn = await run_turn(
            client,
            message,
            previous_response_id=previous,
            approve=index > 0 and bool(YES.match(case.turns[index])),
            **body,
        )
        turns.append(turn)
        previous = turn.response_id
        if turn.error:
            break
    return as_run(turns), turns


async def evaluate(
    label: str,
    runs: int,
    only: list[str],
    levels: list[int],
    # httpx's own per-request deadline, handed straight to the client: not a cancel scope of ours.
    timeout: float,  # noqa: ASYNC109
) -> Path:
    url, bearer, key = mcp_url(), require("VEPATHOS_MCP_BEARER"), require("OPENAI_API_KEY")
    model = os.environ.get("OPENAI_AI_MODEL", "").strip() or DEFAULT_MODEL
    effort = os.environ.get("OPENAI_AI_REASONING_EFFORT", "").strip() or DEFAULT_EFFORT
    instructions, allowed = await read_server(url, bearer)
    print(f"servidor: {len(allowed)} tools, instrucciones de {len(instructions):,} caracteres\n")

    body = dict(
        model=model, effort=effort, instructions=instructions, url=url, bearer=bearer, allowed=allowed
    )
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    report: dict[str, Any] = {
        "label": label,
        "model": model,
        "runs": runs,
        "host": "openai-responses",
        "at": time.strftime("%F %T"),
        "cases": {},
    }
    async with httpx.AsyncClient(
        timeout=timeout, headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    ) as client:
        for name, case in CASES.items():
            if (only and name not in only) or (levels and case.level not in levels):
                continue
            passed = dict.fromkeys(checks_for(case), 0)
            transcripts: list[dict[str, Any]] = []
            for index in range(runs):
                run, turns = await run_case(case, client, **body)
                verdicts = {n: (run.error is None and check(run)) for n, check in checks_for(case).items()}
                for n, ok in verdicts.items():
                    passed[n] += ok
                asked = [a for turn in turns for a in (turn.approvals_asked or [])]
                transcripts.append(
                    {
                        "tools": run.tools,
                        "approvals_asked": asked,
                        "text": run.text,
                        "error": run.error,
                        "failed": [n for n, ok in verdicts.items() if not ok],
                    }
                )
                trail = " → ".join(run.tools) or "(sin tools)"
                ticket = f"  [tarjeta: {', '.join(asked)}]" if asked else ""
                print(
                    f"  {name} #{index + 1}: {trail}{ticket}" + (f"  ERROR {run.error}" if run.error else ""),
                    flush=True,
                )
            report["cases"][name] = {
                "level": case.level,
                "turns": list(case.turns),
                "passed": passed,
                "transcripts": transcripts,
            }
    path = OUT_DIR / f"{label}.{model}.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def dry_run(only: list[str], levels: list[int]) -> None:
    """What it would send, without a token: the request shape and the cases selected."""

    chosen = {
        name: case
        for name, case in CASES.items()
        if not ((only and name not in only) or (levels and case.level not in levels))
    }
    url = os.environ.get("VEPATHOS_MCP_URL") or os.environ.get("MCP_PUBLIC_URL", "https://<host>") + "/mcp"
    instructions = "<las instrucciones que publica el servidor>"
    # Reading the server is free (no OpenAI call), so the preview shows the real tool list whenever the
    # credential is at hand: a sample list here would hide exactly what require_approval is computed from.
    allowed = ["<las tools que publica el servidor>"]
    bearer = os.environ.get("VEPATHOS_MCP_BEARER", "").strip()
    if bearer:
        try:
            instructions, allowed = asyncio.run(read_server(url, bearer))
            instructions = f"<{len(instructions):,} caracteres leídos del servidor>"
        # A preview must never fail on an unreachable server: it falls back to showing a placeholder.
        except Exception as error:
            print(f"(no se pudo leer el servidor: {error}; se muestra un ejemplo)\n", file=sys.stderr)

    body = request_body(
        model=os.environ.get("OPENAI_AI_MODEL", "").strip() or DEFAULT_MODEL,
        effort=os.environ.get("OPENAI_AI_REASONING_EFFORT", "").strip() or DEFAULT_EFFORT,
        instructions=instructions,
        items=[{"role": "user", "content": "<el turno>"}],
        previous_response_id=None,
        url=url,
        bearer="<VEPATHOS_MCP_BEARER>",
        allowed=allowed,
    )
    print(json.dumps(body, ensure_ascii=False, indent=2))
    print(f"\n{len(chosen)} casos, {sum(len(c.turns) for c in chosen.values())} turnos por corrida:")
    for name, case in chosen.items():
        print(f"  nivel {case.level}  {name:22} {len(case.turns)} turno(s)   gasta: {case.spends}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--label", default="luna", help="name of the run, e.g. the text variant under test")
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--case", action="append", default=[], choices=list(CASES))
    parser.add_argument("--level", action="append", type=int, default=[])
    parser.add_argument("--timeout", type=float, default=180.0)
    parser.add_argument(
        "--dry-run", action="store_true", help="print the request and the cases, spend nothing"
    )
    args = parser.parse_args()
    load_env()
    if args.dry_run:
        return dry_run(args.case, args.level)
    path = asyncio.run(evaluate(args.label, args.runs, args.case, args.level, args.timeout))
    print(f"\nguardado en {path}")
    print("comparalo con las corridas de Claude:  .venv/bin/python -m devtools.agent_eval --compare")


if __name__ == "__main__":
    sys.exit(main())

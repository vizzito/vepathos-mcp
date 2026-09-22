"""End-to-end smoke of the import → plan path against the REAL local stack (not the fake Core).

adapter :8080 (MCP_IMPORT_TOOLS_ENABLED=true, AUTH_MODES=api_key) → api-doc :3000 → Smart Import :8100
→ optimizer. It checks what unit and contract tests cannot: Core's billing of a plan (first run
charged, one rerun with the same stops or fewer free within 24 h) and that inline optimize still works.

Run it yourself; the credential stays in your shell and is never printed:

    export VEPATHOS_MCP_BEARER='vpt_…:vpt_sk_test_…'
    .venv/bin/python -m devtools.smoke_local_datasets
"""

from __future__ import annotations

import asyncio
import os
import sys
import time
from typing import Any

from mcp.client import Client
from mcp.client.streamable_http import streamable_http_client
from mcp.shared._httpx_utils import create_mcp_http_client

URL = os.environ.get("VEPATHOS_MCP_URL", "http://127.0.0.1:8080/mcp")
DEPOT = {"latitude": -34.6037, "longitude": -58.3816}
CSV = (
    "id,lat,lng,address,weight_kg\n"
    "SMK-1,-34.6090,-58.3920,Av. de Mayo 1370,2.5\n"
    "SMK-2,-34.6010,-58.3830,Florida 537,1.0\n"
    "SMK-3,-34.5950,-58.3750,Av. Santa Fe 1145,3.0\n"
    "SMK-4,-34.6150,-58.3700,Defensa 1200,1.5\n"
)

failures: list[str] = []


def check(ok: bool, label: str, detail: Any = "") -> None:
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + (f"  → {detail}" if detail != "" else ""))
    if not ok:
        failures.append(label)


async def call(client: Client, tool: str, args: dict[str, Any]) -> tuple[bool, dict[str, Any]]:
    result = await client.call_tool(tool, args)
    payload = result.structured_content if isinstance(result.structured_content, dict) else {}
    return bool(result.is_error), payload


async def optimize(client: Client, tool: str, args: dict[str, Any]) -> dict[str, Any]:
    """One charged call whatever MCP_CONFIRM_BEFORE_OPTIMIZE is: show the preflight, then confirm."""

    is_error, out = await call(client, tool, args)
    if not is_error and out.get("preflight"):
        pf = out["preflight"]
        print(
            f"    preflight: stops={pf.get('stops')} charges_stops={pf.get('charges_stops')} "
            f"warnings={[w.get('code') for w in pf.get('warnings') or []]}"
        )
        is_error, out = await call(client, tool, {**args, "confirmed": True})
    if is_error:
        return {"error": out.get("error")}
    return out


async def wait_done(client: Client, optimization_id: str) -> str:
    """Core runs one optimization at a time on small plans; the next run needs this one finished."""

    status = "queued"
    for _ in range(12):
        _, res = await call(client, "get_optimization_result", {"optimization_id": optimization_id})
        status = str(res.get("status") or (res.get("error") or {}).get("code"))
        if status in {"completed", "failed"} or res.get("error"):
            return status
        await asyncio.sleep(5)
    return status


async def stops_remaining(client: Client) -> int | None:
    _, account = await call(client, "get_account", {})
    return (account.get("usage") or {}).get("stops_remaining")


def _from_env_file(name: str) -> str:
    """Read one variable from the repo's .env, without overriding the environment."""

    import pathlib
    import re

    try:
        text = (pathlib.Path(__file__).resolve().parents[1] / ".env").read_text(encoding="utf-8")
    except OSError:
        return ""
    match = re.search(rf"^\s*{name}\s*=\s*(.*)$", text, re.M)
    return match.group(1).strip().strip("\"'") if match else ""


async def main() -> int:
    # The credential may live in this repo's .env, where the integration tests and agent_eval read
    # theirs: a smoke that needs its key exported by hand is a smoke that gets skipped.
    bearer = os.environ.get("VEPATHOS_MCP_BEARER") or _from_env_file("VEPATHOS_GROWTH_CREDENTIAL")
    if ":" not in bearer:
        print(
            "No developer credential: export VEPATHOS_MCP_BEARER, or set VEPATHOS_GROWTH_CREDENTIAL "
            "in this repo's .env (client_id:client_secret, created in the dashboard with purpose MCP)."
        )
        return 2
    if not bearer.startswith("vpt_mcp_"):
        # Core only issues AI-agent credentials (scope mcp:optimize) with this prefix.
        print("VEPATHOS_MCP_BEARER is not an AI-agent credential (client_id must start with vpt_mcp_).")
        print("Create one in the LOCAL dashboard: http://localhost:3000/dashboard/credentials")
        return 2

    http = create_mcp_http_client(headers={"Authorization": f"Bearer {bearer}"})
    async with Client(streamable_http_client(URL, http_client=http)) as client:
        print("1. Tools published")
        names = {t.name for t in (await client.list_tools()).tools}
        check("import_deliveries" in names, "import tools published (MCP_IMPORT_TOOLS_ENABLED=true)")
        check("list_plans" in names, "list_plans published")

        is_error, account = await call(client, "get_account", {})
        if is_error:
            # Every later step needs Core to accept the credential; stop instead of failing each one.
            print(f"  FAIL  Core refused the credential: {(account.get('error') or {}).get('code')}")
            print("        Use a credential created in the local dashboard, not one from production.")
            return 1
        plan = account.get("plan") or {}
        print(
            f"    account={account.get('account_label')} plan={plan.get('name')} "
            f"max_stops={plan.get('max_stops_per_request')} remaining={await stops_remaining(client)}"
        )

        print("2. Inline optimize still works (the path production uses)")
        before = await stops_remaining(client)
        inline = await optimize(
            client,
            "optimize_routes",
            {
                "depot": DEPOT,
                "vehicles": [{"vehicle_id": "van", "count": 1}],
                "stops": [
                    {"stop_id": "INL-1", "latitude": -34.6090, "longitude": -58.3920},
                    {"stop_id": "INL-2", "latitude": -34.6010, "longitude": -58.3830},
                ],
                "idempotency_key": f"smoke-inline-{int(time.time())}",
            },
        )
        check(
            "optimization_id" in inline,
            "inline optimize accepted",
            inline.get("error") or inline.get("status"),
        )
        if "optimization_id" in inline:
            check(
                await wait_done(client, inline["optimization_id"]) == "completed", "inline optimize completed"
            )
        after = await stops_remaining(client)
        if before is not None and after is not None:
            check(before - after == 2, "inline run charged its 2 stops", f"{before} → {after}")

        print("3. Import a file")
        is_error, created = await call(client, "import_deliveries", {"text": CSV, "filename": "smoke.csv"})
        check(not is_error and bool(created.get("dataset_id")), "import accepted", created.get("error") or "")
        if is_error:
            return 1
        status: dict[str, Any] = {}
        for _ in range(30):
            _, status = await call(client, "get_import_result", {"import_id": created["import_id"]})
            if status.get("status") in {"completed", "failed", "needs_mapping", "expired"}:
                break
            await asyncio.sleep(2)
        summary = status.get("summary") or {}
        check(status.get("status") == "completed", "import completed", status.get("status"))
        check(summary.get("stops") == 4, "4 stops materialized", summary.get("stops"))
        check(bool(status.get("plan_id")), "the stops loaded into a plan", status.get("plan_id"))
        check(status.get("next_optimize_charged") is True, "first optimize will be charged")
        check(status.get("charged_because") == "no_billed_run", "charged: no billed run yet")
        if status.get("status") != "completed" or not status.get("plan_id"):
            return 1
        plan_id = status["plan_id"]
        base = {"plan_id": plan_id, "depot": DEPOT, "vehicles": [{"vehicle_id": "van", "count": 1}]}

        print("4. First optimize of the plan is charged")
        before = await stops_remaining(client)
        first = await optimize(client, "optimize_routes", base)
        check("optimization_id" in first, "first run accepted", first.get("error") or "")
        check(first.get("quota_charged") is True, "first run charged the quota", first.get("quota_charged"))
        # Free retries depend on the subscription: Free and Starter 1, Growth 2, Scale 3, Enterprise 5.
        check(
            (first.get("free_retries_remaining") or 0) >= 1,
            "at least one free retry after it",
            first.get("free_retries_remaining"),
        )
        if "optimization_id" in first:
            print(f"    first run: {await wait_done(client, first['optimization_id'])}")
        after = await stops_remaining(client)
        if before is not None and after is not None:
            check(before - after == 4, "quota went down by the plan's 4 stops", f"{before} → {after}")

        print("5. A rerun with fewer stops is the free retry")
        before = await stops_remaining(client)
        retry = await optimize(client, "optimize_routes", {**base, "exclude_stop_ids": ["SMK-4"]})
        check("optimization_id" in retry, "rerun accepted", retry.get("error") or "")
        check(retry.get("quota_charged") is False, "rerun did not charge", retry.get("quota_charged"))
        check(retry.get("free_retry") is True, "reported as the free retry", retry.get("free_retry"))
        # One try spent, whatever the plan allows. Asserting a flat 0 only held on Free, and read as a
        # product failure the first time this ran against an Enterprise account (5 tries, 4 left).
        started_with = first.get("free_retries_remaining") or 0
        check(
            retry.get("free_retries_remaining") == started_with - 1,
            "the rerun spent exactly one try",
            f"{started_with} → {retry.get('free_retries_remaining')}",
        )
        if "optimization_id" in retry:
            print(f"    rerun: {await wait_done(client, retry['optimization_id'])}")
        after = await stops_remaining(client)
        if before is not None and after is not None:
            check(before == after, "quota unchanged by the free retry", f"{before} → {after}")

        print("6. The plan remembers the run for a new chat")
        _, plan = await call(client, "list_plans", {"plan_id": plan_id})
        detail = plan.get("plan") or {}
        check(bool(detail.get("last_agent_run")), "last_agent_run recorded")
        # What the next run costs has to match the tries left: charged only once the allowance is spent.
        left = retry.get("free_retries_remaining")
        charged_next = detail.get("next_optimize_charged")
        check(
            charged_next is (left == 0),
            "what the next run costs matches the tries left",
            f"tries left {left}, next charged {charged_next} ({detail.get('charged_because')})",
        )

    print()
    print("ALL PASS" if not failures else f"{len(failures)} FAILED: {failures}")
    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

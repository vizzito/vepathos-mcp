"""The tool reference, generated from the registry (build_tools) so no document can disagree with the server.

Three readers: someone at a terminal (`vepathos-mcp tools`, `workflows`, `--help`), the documents under
docs/ and README.md (generated blocks, kept current by a test), and an MCP client that lists resources
(`vepathos://docs/reference`). None of it is sent in every conversation: what a model always receives
is the tool descriptions and the server instructions (tools/descriptions.py), and nothing here may be
the only place a rule lives.

Groups and workflows are the two things the registry does not know. They live here, once, and tests
fail when a tool is published without a group or a workflow.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Any, Literal, cast

from mcp.server.mcpserver.tools import Tool

from vepathos_mcp import __version__
from vepathos_mcp.clients.vepathos_api import VepathosApiClient
from vepathos_mcp.config import Settings
from vepathos_mcp.rate_limit import RateLimiter
from vepathos_mcp.tools import build_tools
from vepathos_mcp.tools.runtime import ToolDeps

Audience = Literal["repo", "public"]

# The switches that publish optional tools: (environment variable, how a public page names the group).
FLAGS: tuple[tuple[str, str], ...] = (
    ("MCP_IMPORT_TOOLS_ENABLED", "imports"),
    ("MCP_MAP_SHARES_ENABLED", "shareable maps"),
    ("MCP_CATALOG_WRITE_TOOLS_ENABLED", "saved vehicles and depots"),
)

GROUPS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("Account", ("get_account",)),
    ("Plan and run", ("optimize_delivery_routes", "optimize_plan", "list_plans", "get_optimization_result")),
    (
        "Imports",
        (
            "import_delivery_file",
            "import_delivery_text",
            "get_import_result",
            "update_import_mapping",
            "list_datasets",
        ),
    ),
    ("Addresses", ("geocode_addresses", "get_geocode_result")),
    ("Saved vehicles and depots", ("list_fleet", "manage_vehicle", "manage_depot")),
    ("Automations", ("list_automations", "create_automation")),
    ("Share", ("create_optimization_map",)),
)


@dataclass(frozen=True)
class Step:
    """One step of a workflow: the tool to call, or the alternatives when the input decides."""

    tools: tuple[str, ...]
    note: str = ""


@dataclass(frozen=True)
class Workflow:
    key: str
    title: str
    when: str
    steps: tuple[Step, ...]


WORKFLOWS: tuple[Workflow, ...] = (
    Workflow(
        "stops_in_chat",
        "Stops given in the conversation",
        "The user pastes or dictates addresses or coordinates.",
        (
            Step(("get_account",)),
            Step(("list_fleet",)),
            Step(("geocode_addresses",), "street addresses only"),
            Step(("get_geocode_result",)),
            Step(("optimize_delivery_routes",)),
            Step(("get_optimization_result",)),
        ),
    ),
    Workflow(
        "import_file",
        "A file or pasted rows",
        "The orders are in a spreadsheet, a link or pasted text.",
        (
            Step(("import_delivery_file", "import_delivery_text")),
            Step(("get_import_result",)),
            Step(("update_import_mapping",), "only when a column was read wrong"),
            Step(("optimize_plan",)),
            Step(("get_optimization_result",)),
        ),
    ),
    Workflow(
        "rerun_plan",
        "Run a saved plan again",
        "The stops are already saved in Vepathos.",
        (
            Step(("list_plans", "list_datasets")),
            Step(("optimize_plan",)),
            Step(("get_optimization_result",)),
        ),
    ),
    Workflow(
        "share_result",
        "Share a result",
        "The user wants a link anyone can open.",
        (Step(("get_optimization_result",)), Step(("create_optimization_map",))),
    ),
    Workflow(
        "standing_rule",
        "Prepare an automation",
        "The user wants a plan to route on a schedule.",
        (
            Step(("list_plans",)),
            Step(("list_automations",)),
            Step(("create_automation",), "always created switched off"),
        ),
    ),
    Workflow(
        "master_data",
        "Save a vehicle or a depot",
        "The user asks to add, save or edit one in their account; never for one plan's settings.",
        (
            Step(("list_fleet",)),
            Step(
                ("manage_vehicle", "manage_depot"),
                "a depot given as an address goes through geocode_addresses first",
            ),
        ),
    ),
)


@dataclass(frozen=True)
class ToolEntry:
    tool: Tool
    # The environment variable that publishes it, or None when every server does.
    gate: str | None

    @property
    def name(self) -> str:
        return self.tool.name


@dataclass(frozen=True)
class Reference:
    tools: tuple[ToolEntry, ...]
    workflows: tuple[Workflow, ...]

    def names(self) -> tuple[str, ...]:
        return tuple(entry.name for entry in self.tools)


def reference_settings(enabled: frozenset[str]) -> Settings:
    """Settings that publish exactly the optional groups in `enabled`, whatever the environment holds.

    stdio needs nothing but a credential, so this builds on a laptop with no .env and inside the
    production container alike. The confirm gate is off, as in production."""

    values: dict[str, Any] = {
        "_env_file": None,
        "MCP_TRANSPORT": "stdio",
        "VEPATHOS_SERVICE_CREDENTIAL": "docs:docs",
        "MCP_CONFIRM_BEFORE_OPTIMIZE": "false",
        **{env: ("true" if env in enabled else "false") for env, _ in FLAGS},
    }
    return Settings(**values)


def _tools_for(settings: Settings) -> list[Tool]:
    # Building the tools only closes over deps: nothing calls Core while they are listed.
    deps = ToolDeps(settings=settings, core=cast(VepathosApiClient, object()), rate_limiter=RateLimiter({}))
    return build_tools(deps)


@cache
def tool_gates() -> dict[str, str | None]:
    """Which switch publishes each tool, read off the registry itself: no table to keep in step.

    Cached: it depends only on the code, and every server built asks for it."""

    gates: dict[str, str | None] = {tool.name: None for tool in _tools_for(reference_settings(frozenset()))}
    for env, _ in FLAGS:
        for tool in _tools_for(reference_settings(frozenset({env}))):
            gates.setdefault(tool.name, env)
    return gates


def build_reference(settings: Settings | None = None) -> Reference:
    """None: the whole surface, every optional tool on. With settings: what that deployment publishes."""

    gates = tool_gates()
    tools = _tools_for(settings or reference_settings(frozenset(env for env, _ in FLAGS)))
    published = {tool.name for tool in tools}
    workflows: list[Workflow] = []
    for flow in WORKFLOWS:
        steps = tuple(
            Step(tuple(name for name in step.tools if name in published), step.note) for step in flow.steps
        )
        # A workflow with a step nobody can take is not a workflow this server offers.
        if all(step.tools for step in steps):
            workflows.append(Workflow(flow.key, flow.title, flow.when, steps))
    return Reference(
        tools=tuple(ToolEntry(tool, gates.get(tool.name)) for tool in tools), workflows=tuple(workflows)
    )


SUMMARY_MAX = 160


def summary(description: str) -> str:
    """The first sentence: what the tool is for, as its own description opens. A sentence that goes on
    to enumerate (": account label, plan name, …") is cut before the list, which the full text keeps."""

    sentence = description.split(". ", 1)[0]
    if len(sentence) > SUMMARY_MAX:
        sentence = sentence.split(": ", 1)[0]
    return sentence.rstrip(".") + "."


def _yes(value: bool | None) -> str:
    return "yes" if value else "no"


def _changes_data(entry: ToolEntry) -> bool:
    annotations = entry.tool.annotations
    return not (annotations is not None and annotations.read_only_hint)


def _availability(entry: ToolEntry, audience: Audience) -> str:
    if entry.gate is None:
        return "always" if audience == "repo" else "Always"
    if audience == "repo":
        return f"`{entry.gate}`"
    return f"Rolling out ({dict(FLAGS)[entry.gate]})"


def _flows_of(ref: Reference, name: str) -> list[str]:
    return [flow.title for flow in ref.workflows if any(name in step.tools for step in flow.steps)]


def _chain(flow: Workflow) -> str:
    return " → ".join(
        " | ".join(step.tools) + (f" ({step.note})" if step.note else "") for step in flow.steps
    )


def _grouped(ref: Reference) -> list[tuple[str, list[ToolEntry]]]:
    by_name = {entry.name: entry for entry in ref.tools}
    groups = [(title, [by_name[name] for name in names if name in by_name]) for title, names in GROUPS]
    return [(title, entries) for title, entries in groups if entries]


def _type_of(spec: dict[str, Any]) -> str:
    if "enum" in spec:
        return " \\| ".join(str(value) for value in spec["enum"])
    kind = spec.get("type")
    if kind == "array":
        items = spec.get("items")
        return f"array of {_type_of(items)}" if isinstance(items, dict) else "array"
    return str(kind) if kind else "any"


def _resolve(spec: dict[str, Any]) -> dict[str, Any]:
    branches = spec.get("anyOf")
    if not isinstance(branches, list):
        return spec
    chosen = next((b for b in branches if isinstance(b, dict) and b.get("type") != "null"), None)
    return {**chosen, **{k: v for k, v in spec.items() if k != "anyOf"}} if chosen else spec


def field_rows(schema: dict[str, Any], prefix: str = "", depth: int = 0) -> list[tuple[str, str, str, str]]:
    """(field, type, required, description) rows, nested objects and array items two levels down."""

    rows: list[tuple[str, str, str, str]] = []
    properties = schema.get("properties")
    if not isinstance(properties, dict):
        return rows
    required = set(schema.get("required") or [])
    for name, raw in properties.items():
        if not isinstance(raw, dict):
            continue
        spec = _resolve(raw)
        description = " ".join(str(spec.get("description") or "").split()).replace("|", "\\|")
        rows.append((f"{prefix}{name}", _type_of(spec), "yes" if name in required else "no", description))
        if depth >= 2:
            continue
        items = spec.get("items")
        if spec.get("type") == "object":
            rows += field_rows(spec, f"{prefix}{name}.", depth + 1)
        elif isinstance(items, dict):
            rows += field_rows(_resolve(items), f"{prefix}{name}[].", depth + 1)
    return rows


def render_index(ref: Reference) -> str:
    groups = _grouped(ref)
    width = max(len(title) for title, _ in groups) + 3
    return "\n".join(
        f"  {title.ljust(width)}{', '.join(entry.name for entry in entries)}" for title, entries in groups
    )


def render_text(ref: Reference) -> str:
    blocks: list[str] = []
    for entry in ref.tools:
        tool, notes = entry.tool, entry.tool.annotations
        flows = _flows_of(ref, entry.name)
        published = "always" if entry.gate is None else f"when {entry.gate}=true"
        blocks.append(
            "\n".join(
                [
                    tool.name,
                    "-" * len(tool.name),
                    tool.title or tool.name,
                    summary(tool.description),
                    "",
                    f"Read only:   {_yes(notes.read_only_hint if notes else None)}",
                    f"Destructive: {_yes(notes.destructive_hint if notes else None)}",
                    f"Idempotent:  {_yes(notes.idempotent_hint if notes else None)}",
                    f"Published:   {published}",
                    f"Flows:       {'; '.join(flows) if flows else '-'}",
                ]
            )
        )
    return "\n\n".join(blocks) + "\n"


def render_workflows(ref: Reference) -> str:
    return "\n\n".join(f"{flow.title}\n  {flow.when}\n  {_chain(flow)}" for flow in ref.workflows) + "\n"


def render_table(ref: Reference, audience: Audience) -> str:
    lines = ["| Tool | What it does | Changes data | Available |", "|---|---|---|---|"]
    for _, entries in _grouped(ref):
        for entry in entries:
            what = summary(entry.tool.description).replace("|", "\\|")
            cells = (f"`{entry.name}`", what, _yes(_changes_data(entry)), _availability(entry, audience))
            lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)


def render_markdown(ref: Reference, *, deployment: bool) -> str:
    out = ["# Vepathos MCP — tools reference", "", f"Version {__version__}.", ""]
    if not deployment:
        out += [
            "GENERATED by `vepathos-mcp tools --write-docs` from the tool registry. Do not edit.",
            "",
            "Rendered with every optional tool enabled and `MCP_CONFIRM_BEFORE_OPTIMIZE=false` (the "
            "production default). Behaviour, errors and examples: [tools.md](tools.md).",
            "",
        ]
    out += ["## Tools by task", ""]
    for title, entries in _grouped(ref):
        out.append(f"- **{title}**: " + ", ".join(f"`{entry.name}`" for entry in entries))
    out += ["", "## Workflows", ""]
    for flow in ref.workflows:
        out += [f"**{flow.title}.** {flow.when}", "", f"`{_chain(flow)}`", ""]
    for entry in ref.tools:
        tool, notes = entry.tool, entry.tool.annotations
        out += [f"## `{tool.name}`", "", f"**{tool.title or tool.name}**", "", tool.description, ""]
        out.append(
            f"- Read only: {_yes(notes.read_only_hint if notes else None)} · "
            f"Destructive: {_yes(notes.destructive_hint if notes else None)} · "
            f"Idempotent: {_yes(notes.idempotent_hint if notes else None)} · "
            f"Open world: {_yes(notes.open_world_hint if notes else None)}"
        )
        if not deployment:
            out.append(f"- Published: {_availability(entry, 'repo')}")
        flows = _flows_of(ref, entry.name)
        if flows:
            out.append(f"- Workflows: {'; '.join(flows)}")
        rows = field_rows(tool.parameters)
        out += ["", "### Input", ""]
        if rows:
            out += ["| Field | Type | Required | Description |", "|---|---|---|---|"]
            out += [f"| `{name}` | {kind} | {needed} | {text} |" for name, kind, needed, text in rows]
        else:
            out.append("Takes no arguments.")
        result = field_rows(tool.output_schema or {}, depth=2)
        if result:
            out += ["", "### Output", "", "| Field | Type | Description |", "|---|---|---|"]
            out += [f"| `{name}` | {kind} | {text} |" for name, kind, _, text in result]
        out.append("")
    return "\n".join(out).rstrip("\n") + "\n"


# --- Generated documents ---------------------------------------------------------------------

MARK_BEGIN = "<!-- BEGIN GENERATED: tools-table (vepathos-mcp tools --write-docs) -->"
MARK_END = "<!-- END GENERATED: tools-table -->"
GENERATED_FILE = "docs/tools-reference.md"
TABLE_TARGETS: dict[str, Audience] = {"README.md": "repo", "docs/public-mcp-page.md": "public"}


def expected_documents(root: Path) -> dict[Path, str]:
    """What every generated document must contain: the whole reference, and the table between markers."""

    ref = build_reference()
    documents = {root / GENERATED_FILE: render_markdown(ref, deployment=False)}
    for relative, audience in TABLE_TARGETS.items():
        path = root / relative
        text = path.read_text(encoding="utf-8")
        if MARK_BEGIN not in text or MARK_END not in text:
            raise ValueError(f"{relative} has no generated-table markers ({MARK_BEGIN} … {MARK_END})")
        head, rest = text.split(MARK_BEGIN, 1)
        tail = rest.split(MARK_END, 1)[1]
        documents[path] = f"{head}{MARK_BEGIN}\n{render_table(ref, audience)}\n{MARK_END}{tail}"
    return documents


def stale_documents(root: Path) -> list[str]:
    return [
        str(path.relative_to(root))
        for path, text in expected_documents(root).items()
        if not path.exists() or path.read_text(encoding="utf-8") != text
    ]


def write_documents(root: Path) -> list[str]:
    stale = stale_documents(root)
    for path, text in expected_documents(root).items():
        path.write_text(text, encoding="utf-8")
    return stale

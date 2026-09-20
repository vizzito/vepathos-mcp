"""MCP prompts: templates the USER picks (in Claude they show up as commands), unlike tools, which the
model decides to call.

They name no tool. A prompt is the same on every server, and a server publishes only the tools its
flags allow, so a prompt that named one could send the model after a tool it cannot see. What each tool
does is the tool description's job; how they chain is the server instructions'. Hosts that show no
prompts lose nothing they need.
"""

from __future__ import annotations

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.prompts.base import Prompt

HELP = (
    "Show me what I can do with Vepathos in this conversation. First check which account is connected, "
    "what its plan allows and how many stops remain, the vehicles and depots I have saved, and my saved "
    "plans. Then explain in plain language, grouped by task, what you can do for me here and what each "
    "capability is for. Say clearly which actions spend my plan's stops, which ones change something "
    "saved in my account, and which only read. Do not use internal tool names unless I ask for them. "
    "Reply in the language I write in."
)
TOOLS = (
    "List every Vepathos tool available in this conversation by its technical name, grouped by task. For "
    "each one give a single line: what it does, whether it only reads or changes something in my account, "
    "and whether it spends plan stops. Then name the usual sequences (which tool follows which). Use only "
    "the tools you can actually see here; do not describe tools that are not available."
)
PLAN_DELIVERIES = (
    "Help me plan a delivery run with Vepathos. First find out where my orders are: already saved in "
    "Vepathos as a plan, in a file I can share, or addresses I will paste here. Check my account's limits "
    "and my saved vehicles and depots before proposing anything. Propose the run with numbers (stops, "
    "vehicles, stops per vehicle, what does not fit and why, and how many plan stops it spends) and wait "
    "for my yes before optimizing. Settings for this run, such as how many vehicles to use, stay in the "
    "plan: do not save or change vehicles or depots in my account unless I ask. When it finishes, "
    "summarize the routes and anything left unassigned."
)
RERUN_PLAN = (
    "Show my saved Vepathos plans and help me run one again with different settings: other vehicles, "
    "other stops per vehicle, another departure time, or leaving some stops out for this run only. Before "
    "running it, tell me whether this run spends plan stops or counts as another try within the 24-hour "
    "window, and wait for my yes."
)

# name, title, description, text
PROMPTS: tuple[tuple[str, str, str, str], ...] = (
    (
        "vepathos_help",
        "What can Vepathos do here?",
        "Plain-language overview of what this connection can do for you.",
        HELP,
    ),
    (
        "vepathos_tools",
        "List the Vepathos tools (technical)",
        "Every available tool by its technical name, and what it does.",
        TOOLS,
    ),
    (
        "plan_deliveries",
        "Plan a delivery run",
        "From orders to optimized routes, confirming the cost first.",
        PLAN_DELIVERIES,
    ),
    ("rerun_plan", "Run a saved plan again", "Try a saved plan with other settings.", RERUN_PLAN),
)


def _template(text: str):  # type: ignore[no-untyped-def]
    def render() -> str:
        return text

    return render


def register_prompts(server: MCPServer) -> None:
    for name, title, description, text in PROMPTS:
        server.add_prompt(
            Prompt.from_function(_template(text), name=name, title=title, description=description)
        )

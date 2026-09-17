"""Model-facing text. Descriptions are part of the product: precise, factual, no promotion.

Two audiences, no overlap:

- server_instructions(): the order of work and when to ask the user. Read once per session, and some
  clients truncate or never show it, so nothing here may be the only place a rule lives.
- Tool descriptions: what one tool does and when to call it. These always reach the model, so every
  rule that governs a single tool belongs in that tool's description.

Both are sent in every conversation, so they are written to be short.

Text about confirming a charge follows `MCP_CONFIRM_BEFORE_OPTIMIZE`, so it is built per server rather
than fixed: a client told its first call is free, when the server optimizes and charges on it,
reports a real charged plan as a preview.

Keep them consistent with the Core capability matrix (docs/tools.md).
"""

from __future__ import annotations

import pytest

from vepathos_mcp.tools import descriptions as d
from vepathos_mcp.tools.maps import DESCRIPTION as MAP_DESCRIPTION

# Roughly 2,000 tokens. Raised with import/dataset tools (0.4.0); every conversation pays it.
MAX_TOTAL_CHARS = 9_500
MAX_INSTRUCTION_CHARS = 2_400

TOOL_NAMES = (
    "geocode_addresses",
    "list_fleet",
    "get_account",
    "optimize_delivery_routes",
    "get_optimization_result",
    "list_plans",
    "optimize_plan",
)
IMPORT_TOOL_NAMES = ("import_delivery_file", "get_import_result")

# A server sends the texts for one gate setting, never both, so each setting is budgeted on its own.
EACH_GATE_SETTING = pytest.mark.parametrize("gate", [True, False], ids=["gate_on", "gate_off"])
# Same for MCP_IMPORT_TOOLS_ENABLED: the instructions only name the tools that server publishes.
EACH_IMPORT_SETTING = pytest.mark.parametrize("imports", [True, False], ids=["imports_on", "imports_off"])


def all_text(gate: bool) -> dict[str, str]:
    """Everything one server sends: the fixed texts, plus the ones built for its gate setting.

    Import tools and map shares on: the longest texts a server can send."""

    fixed = {
        name: value
        for name in dir(d)
        if name.isupper() and not name.startswith("_") and isinstance(value := getattr(d, name), str)
    }
    return {
        **fixed,
        "server_instructions": d.server_instructions(
            confirm_before_optimize=gate, import_tools=True, map_shares=True
        ),
        "optimize_description": d.optimize_description(confirm_before_optimize=gate),
        "optimize_plan_description": d.optimize_plan_description(
            confirm_before_optimize=gate, import_tools=True
        ),
    }


@EACH_GATE_SETTING
def test_model_facing_text_stays_within_budget(gate: bool) -> None:
    total = sum(len(text) for text in all_text(gate).values())
    assert total <= MAX_TOTAL_CHARS, f"{total} characters of tool text sent every conversation"
    for imports in (True, False):
        for maps in (True, False):
            instructions = d.server_instructions(
                confirm_before_optimize=gate, import_tools=imports, map_shares=maps
            )
            assert len(instructions) <= MAX_INSTRUCTION_CHARS


@EACH_GATE_SETTING
@EACH_IMPORT_SETTING
def test_instructions_order_the_work_around_the_real_tools(gate: bool, imports: bool) -> None:
    instructions = d.server_instructions(confirm_before_optimize=gate, import_tools=imports)
    for tool in TOOL_NAMES:
        assert tool in instructions, f"instructions never mention {tool}"
    for tool in IMPORT_TOOL_NAMES:
        assert (tool in instructions) is imports, f"{tool} mentioned with import tools {imports}"


@EACH_GATE_SETTING
@EACH_IMPORT_SETTING
def test_instructions_number_their_steps_in_order(gate: bool, imports: bool) -> None:
    instructions = d.server_instructions(confirm_before_optimize=gate, import_tools=imports)
    steps = [line.split(".", 1)[0] for line in instructions.splitlines() if line[:1].isdigit()]
    assert steps == [str(number) for number in range(1, len(steps) + 1)]


def test_get_result_description_names_no_optional_tool() -> None:
    for tool in IMPORT_TOOL_NAMES:
        assert tool not in d.GET_RESULT_DESCRIPTION
        assert tool not in d.LIST_PLANS_DESCRIPTION
        assert tool not in d.optimize_description(confirm_before_optimize=True)
        assert tool not in d.optimize_plan_description(confirm_before_optimize=True, import_tools=False)
    assert "dataset_id" not in d.optimize_plan_description(confirm_before_optimize=True, import_tools=False)
    assert "create_optimization_map" not in d.server_instructions(
        confirm_before_optimize=True, import_tools=True, map_shares=False
    )


@EACH_IMPORT_SETTING
def test_plan_description_states_the_one_free_retry_rule(imports: bool) -> None:
    # One rule for dashboard and MCP (api-doc billing/free-retry-policy.ts).
    for gate in (True, False):
        text = d.optimize_plan_description(confirm_before_optimize=gate, import_tools=imports).lower()
        assert "free retry" in text and "24 h" in text and "same stops or fewer" in text
        assert "next_optimize_charged" in text and "plan limits still apply" in text
        assert "plan_replaced" in text and "plan_temporary" in text
        assert "exclude_stop_ids for this run only" in text


@EACH_GATE_SETTING
@EACH_IMPORT_SETTING
def test_instructions_explain_plans_and_the_free_retry_on_every_server(gate: bool, imports: bool) -> None:
    text = d.server_instructions(confirm_before_optimize=gate, import_tools=imports)
    assert "saved as a plan" in text and "list_plans" in text and "account_url" in text
    assert "free within 24 h" in text and "same stops or fewer" in text
    assert "optimize_plan" in text


def test_every_run_and_import_tells_the_user_about_a_replaced_or_temporary_plan() -> None:
    for text in (
        d.optimize_description(confirm_before_optimize=False),
        d.optimize_plan_description(confirm_before_optimize=False, import_tools=False),
        d.GET_IMPORT_DESCRIPTION,
    ):
        assert "plan_replaced" in text and "plan_temporary" in text and "the user" in text


def test_results_are_the_users_choice_and_the_public_link_is_said_to_be_public() -> None:
    instructions = d.server_instructions(confirm_before_optimize=False, import_tools=True, map_shares=True)
    assert "let the user choose" in instructions.lower()
    assert "account_url" in instructions and "create_optimization_map" in instructions
    assert "anyone with the link" in instructions

    text = MAP_DESCRIPTION.lower()
    assert "account_url" in text and "offer both" in text
    assert "only when the user picks it" in text
    assert "anyone with the link" in text and "48 hours" in text


def test_no_model_facing_text_offers_free_replans_or_variants() -> None:
    texts = [*all_text(True).values(), *all_text(False).values(), MAP_DESCRIPTION]
    for imports in (True, False):
        texts.append(d.optimize_plan_description(confirm_before_optimize=False, import_tools=imports))
        texts.append(d.server_instructions(confirm_before_optimize=False, import_tools=imports))
    for text in texts:
        lowered = text.lower()
        assert "replan" not in lowered and "variant" not in lowered, text[:80]
        # Excluded stops stay in the plan (Core restores the draft): no text may say they leave it.
        assert "leave the plan" not in lowered and "removes those stops" not in lowered, text[:80]


@EACH_GATE_SETTING
def test_instructions_state_when_to_ask_instead_of_guessing(gate: bool) -> None:
    text = d.server_instructions(confirm_before_optimize=gate, import_tools=True).lower()
    assert "depot when unknown" in text
    assert "one question at the moment it matters" in text
    assert "only when get_account lists that feature" in text
    assert "min_stops" in text


def test_tool_rules_live_in_the_tool_description_clients_always_receive() -> None:
    assert "do not invent coordinates" in d.GEOCODE_DESCRIPTION.lower()
    assert "empty=true" in d.LIST_FLEET_DESCRIPTION
    assert "connected apps" in d.GET_ACCOUNT_DESCRIPTION.lower()
    assert (
        "fileparams" in d.IMPORT_FILE_DESCRIPTION.lower() or "attachment" in d.IMPORT_FILE_DESCRIPTION.lower()
    )


@EACH_GATE_SETTING
def test_the_charge_is_confirmable_from_the_tool_description_alone(gate: bool) -> None:
    optimize = d.optimize_description(confirm_before_optimize=gate).lower()
    assert "charges" in optimize and "the user" in optimize


def test_a_gated_server_describes_the_two_calls() -> None:
    optimize = d.optimize_description(confirm_before_optimize=True).lower()
    assert "confirmed=false" in optimize and "confirmed=true" in optimize


def test_direct_server_does_not_promise_a_free_first_call() -> None:
    optimize = d.optimize_description(confirm_before_optimize=False).lower()
    assert "confirmed=false" not in optimize


@EACH_GATE_SETTING
def test_a_short_fleet_is_offered_as_more_vehicles_not_a_test(gate: bool) -> None:
    text = d.server_instructions(confirm_before_optimize=gate, import_tools=True).lower()
    assert "increase the vehicle count" in text
    assert "do not call it a test or hypothetical fleet" in text


def test_dataset_texts_offer_the_last_run() -> None:
    assert "last_run" in d.LIST_DATASETS_DESCRIPTION
    assert "request" in d.GET_RESULT_DESCRIPTION
    assert "last_agent_run" in d.LIST_PLANS_DESCRIPTION
    assert "optimize_plan" in d.LIST_PLANS_DESCRIPTION

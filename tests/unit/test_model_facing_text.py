"""The instructions and descriptions are product surface and cost tokens in every conversation."""

from __future__ import annotations

import pytest

from vepathos_mcp.tools import descriptions as d

# Roughly 1,600 tokens. Raising this is a decision, not an accident: every conversation pays it.
# Raised from 6,000 to buy the confirmation preflight in the optimize description: optimizing spends
# the user's stops, and the rule that guards that has to reach clients which hide server instructions.
MAX_TOTAL_CHARS = 6_400
MAX_INSTRUCTION_CHARS = 2_000

TOOL_NAMES = (
    "geocode_addresses",
    "list_fleet",
    "get_account",
    "optimize_delivery_routes",
    "get_optimization_result",
)

# A server sends the texts for one gate setting, never both, so each setting is budgeted on its own.
EACH_GATE_SETTING = pytest.mark.parametrize("gate", [True, False], ids=["gate_on", "gate_off"])


def all_text(gate: bool) -> dict[str, str]:
    """Everything one server sends: the fixed texts, plus the ones built for its gate setting."""

    fixed = {
        name: value
        for name in dir(d)
        if name.isupper() and not name.startswith("_") and isinstance(value := getattr(d, name), str)
    }
    return {
        **fixed,
        "server_instructions": d.server_instructions(confirm_before_optimize=gate),
        "optimize_description": d.optimize_description(confirm_before_optimize=gate),
    }


@EACH_GATE_SETTING
def test_model_facing_text_stays_within_budget(gate: bool) -> None:
    total = sum(len(text) for text in all_text(gate).values())
    assert total <= MAX_TOTAL_CHARS, f"{total} characters of tool text sent every conversation"
    assert len(d.server_instructions(confirm_before_optimize=gate)) <= MAX_INSTRUCTION_CHARS


@EACH_GATE_SETTING
def test_instructions_order_the_work_around_the_real_tools(gate: bool) -> None:
    instructions = d.server_instructions(confirm_before_optimize=gate)
    for tool in TOOL_NAMES:
        assert tool in instructions, f"instructions never mention {tool}"


@EACH_GATE_SETTING
def test_instructions_state_when_to_ask_instead_of_guessing(gate: bool) -> None:
    text = d.server_instructions(confirm_before_optimize=gate).lower()
    assert "depot, when it is not known" in text
    assert "one question at the moment it matters" in text
    # Offering a constraint the plan lacks produces a rejection the user cannot act on.
    assert "only when get_account lists that feature" in text


def test_tool_rules_live_in_the_tool_description_clients_always_receive() -> None:
    # Clients may truncate or hide server instructions, so these must not depend on them.
    assert "do not invent coordinates" in d.GEOCODE_DESCRIPTION.lower()
    assert "empty=true" in d.LIST_FLEET_DESCRIPTION
    assert "connected apps" in d.GET_ACCOUNT_DESCRIPTION.lower()


@EACH_GATE_SETTING
def test_the_charge_is_confirmable_from_the_tool_description_alone(gate: bool) -> None:
    optimize = d.optimize_description(confirm_before_optimize=gate).lower()
    assert "charges" in optimize and "the user" in optimize


def test_a_gated_server_describes_the_two_calls() -> None:
    optimize = d.optimize_description(confirm_before_optimize=True).lower()
    assert "confirmed=false" in optimize and "confirmed=true" in optimize


def test_an_ungated_server_never_promises_a_free_call() -> None:
    # With the gate off every call optimizes and charges. A text that still says confirmed=false is
    # free makes the agent report a real, charged optimization as a preview (2026-09-16).
    assert "confirmed" not in d.optimize_description(confirm_before_optimize=False).lower()
    assert "confirmed" not in d.server_instructions(confirm_before_optimize=False).lower()

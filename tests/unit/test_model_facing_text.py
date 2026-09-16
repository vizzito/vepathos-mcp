"""The instructions and descriptions are product surface and cost tokens in every conversation."""

from __future__ import annotations

from vepathos_mcp.tools import descriptions as d

# Roughly 1,500 tokens. Raising this is a decision, not an accident: every conversation pays it.
MAX_TOTAL_CHARS = 6_000
MAX_INSTRUCTION_CHARS = 2_000

TOOL_NAMES = (
    "geocode_addresses",
    "list_fleet",
    "get_account",
    "optimize_delivery_routes",
    "get_optimization_result",
)


def all_text() -> dict[str, str]:
    return {
        name: value
        for name in dir(d)
        if name.isupper() and isinstance(value := getattr(d, name), str)
    }


def test_model_facing_text_stays_within_budget() -> None:
    total = sum(len(text) for text in all_text().values())
    assert total <= MAX_TOTAL_CHARS, f"{total} characters of tool text sent every conversation"
    assert len(d.SERVER_INSTRUCTIONS) <= MAX_INSTRUCTION_CHARS


def test_instructions_order_the_work_around_the_real_tools() -> None:
    for tool in TOOL_NAMES:
        assert tool in d.SERVER_INSTRUCTIONS, f"instructions never mention {tool}"


def test_instructions_state_when_to_ask_instead_of_guessing() -> None:
    text = d.SERVER_INSTRUCTIONS.lower()
    assert "depot, when it is not known" in text
    assert "one question at the moment it matters" in text
    # Offering a constraint the plan lacks produces a rejection the user cannot act on.
    assert "only when get_account lists that feature" in text


def test_tool_rules_live_in_the_tool_description_clients_always_receive() -> None:
    # Clients may truncate or hide server instructions, so these must not depend on them.
    assert "do not invent coordinates" in d.GEOCODE_DESCRIPTION.lower()
    assert "empty=true" in d.LIST_FLEET_DESCRIPTION
    assert "connected apps" in d.GET_ACCOUNT_DESCRIPTION.lower()

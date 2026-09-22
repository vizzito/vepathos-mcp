"""The generated reference: one source (build_tools), so no document can disagree with the server."""

from __future__ import annotations

from pathlib import Path

from tests.conftest import make_settings
from vepathos_mcp import reference as r

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_every_published_tool_has_exactly_one_group() -> None:
    grouped = [name for _, names in r.GROUPS for name in names]
    assert len(grouped) == len(set(grouped))
    assert set(grouped) == set(r.build_reference().names())


def test_workflows_name_only_real_tools_and_leave_none_out() -> None:
    surface = set(r.build_reference().names())
    in_flows = {name for flow in r.WORKFLOWS for step in flow.steps for name in step.tools}
    assert in_flows <= surface, in_flows - surface
    assert surface <= in_flows, f"no workflow uses {surface - in_flows}"


def test_the_switch_that_publishes_a_tool_is_read_off_the_registry() -> None:
    gates = r.tool_gates()
    assert gates["get_account"] is None and gates["list_fleet"] is None
    assert gates["manage_vehicle"] == gates["manage_depot"] == "MCP_CATALOG_WRITE_TOOLS_ENABLED"
    assert gates["import_deliveries"] == "MCP_IMPORT_TOOLS_ENABLED"
    assert gates["create_optimization_map"] == "MCP_MAP_SHARES_ENABLED"


def test_the_reference_names_every_tool_and_is_deterministic() -> None:
    ref = r.build_reference()
    text = r.render_markdown(ref, deployment=False)
    for name in ref.names():
        assert f"## `{name}`" in text
    assert "Destructive: yes" in text and "Read only: yes" in text
    assert "`vehicles[].count`" in text and "`depot.latitude`" in text
    assert text == r.render_markdown(r.build_reference(), deployment=False)


def test_a_deployment_is_told_only_what_it_publishes() -> None:
    ref = r.build_reference(make_settings(MCP_TRANSPORT="stdio"))
    text = r.render_markdown(ref, deployment=True)
    assert "get_account" in text and "list_automations" in text
    for hidden in ("manage_vehicle", "import_deliveries", "create_optimization_map"):
        assert hidden not in text
    assert "Save a vehicle or a depot" not in text and "Share a result" not in text
    # list_datasets is gone, but a saved plan still reruns through list_plans.
    assert "`list_plans → optimize_routes → get_optimization_result`" in text
    assert "_ENABLED" not in text and "GENERATED" not in text


def test_the_public_table_names_no_environment_variable() -> None:
    table = r.render_table(r.build_reference(), "public")
    assert "MCP_" not in table
    assert "Rolling out (saved vehicles and depots)" in table and "| Always |" in table
    assert "`MCP_IMPORT_TOOLS_ENABLED`" in r.render_table(r.build_reference(), "repo")


def test_the_generated_documents_are_current() -> None:
    assert r.stale_documents(REPO_ROOT) == [], "run `vepathos-mcp tools --write-docs`"


def test_the_handwritten_listing_names_every_tool_a_server_always_publishes() -> None:
    # docs/directory-listing.md is paste-ready copy with a 2,000-character cap, so it is not generated.
    listing = (REPO_ROOT / "docs" / "directory-listing.md").read_text(encoding="utf-8")
    for name, gate in r.tool_gates().items():
        if gate is None:
            assert name in listing, f"docs/directory-listing.md never mentions {name}"

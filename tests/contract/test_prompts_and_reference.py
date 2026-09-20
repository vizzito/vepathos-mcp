"""Prompts the user picks and the reference resource, through the MCP SDK client."""

from __future__ import annotations

from collections.abc import Callable

from mcp.client import Client

from tests.conftest import FakeClock, make_settings
from vepathos_mcp import reference
from vepathos_mcp.clients.vepathos_api import VepathosApiClient
from vepathos_mcp.prompts import PROMPTS
from vepathos_mcp.server import build_server


async def test_the_server_offers_its_prompts_and_its_reference(
    core_client_factory: Callable[..., VepathosApiClient], clock: FakeClock
) -> None:
    core = core_client_factory()
    server = build_server(make_settings(MCP_TRANSPORT="stdio"), core, sleep=clock.sleep, clock=clock)
    async with Client(server) as client:
        listed = await client.list_prompts()
        assert [p.name for p in listed.prompts] == [
            "vepathos_help",
            "vepathos_tools",
            "plan_deliveries",
            "rerun_plan",
        ]
        assert all(p.title and p.description and not p.arguments for p in listed.prompts)

        rendered = await client.get_prompt("vepathos_help")
        assert [m.role for m in rendered.messages] == ["user"]
        assert "plain language" in rendered.messages[0].content.text

        resources = await client.list_resources()
        assert [str(res.uri) for res in resources.resources] == ["vepathos://docs/reference"]
        read = await client.read_resource("vepathos://docs/reference")
        text = read.contents[0].text
        assert read.contents[0].mime_type == "text/markdown"
        assert "## `get_account`" in text and "manage_vehicle" not in text
    await core.aclose()


def test_prompts_name_no_tool_so_they_never_name_one_the_server_lacks() -> None:
    surface = reference.build_reference().names()
    for name, _, description, text in PROMPTS:
        for tool in surface:
            assert tool not in text and tool not in description, f"{name} names {tool}"
        assert "driver" not in text.lower()
    assert "wait for my yes" in dict((p[0], p[3]) for p in PROMPTS)["plan_deliveries"]

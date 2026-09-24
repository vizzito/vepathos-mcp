from typing import Any

import httpx
import pytest
from mcp.client import Client

from tests.conftest import make_settings
from vepathos_mcp.clients.vepathos_api import VepathosApiClient
from vepathos_mcp.server import build_server


@pytest.mark.parametrize("status", [200, 404, 410, 503])
async def test_map_tool_contract(status: int) -> None:
    requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if status == 200:
            return httpx.Response(
                200,
                json={
                    "map_url": "https://vepathos.com/shared/routes/opaque",
                    "expires_at": "2026-09-16T12:00:00Z",
                    "access": "anyone_with_link",
                },
            )
        return httpx.Response(
            status,
            json={
                "error": {
                    "code": "OPTIMIZATION_EXPIRED" if status == 410 else "BACKEND_UNAVAILABLE",
                    "message": "Map unavailable",
                    "retryable": False,
                }
            },
        )

    core = VepathosApiClient(
        "http://core", "service-secret", max_attempts=1, transport=httpx.MockTransport(respond)
    )
    try:
        settings = make_settings(MCP_TRANSPORT="stdio", MCP_MAP_SHARES_ENABLED=True)
        async with Client(build_server(settings, core)) as client:
            tools = {tool.name: tool for tool in (await client.list_tools()).tools}
            assert "create_optimization_map" in tools
            result = await client.call_tool("create_optimization_map", {"optimization_id": "mcp_example123"})
            assert result.is_error is (status != 200)
            payload: Any = result.structured_content
            if status == 200:
                assert payload["access"] == "anyone_with_link"
                assert "expires_at" in payload
            else:
                assert "error" in payload
            assert requests[0].url.path == "/api/mcp/v1/optimization/jobs/mcp_example123/map"
            assert requests[0].headers["X-Vepathos-MCP-Service-Key"] == "service-secret"
            assert requests[0].headers["Authorization"].startswith("Bearer ")
            assert requests[0].headers["Idempotency-Key"] == "map:mcp_example123"
            before = len(requests)
            invalid = await client.call_tool(
                "create_optimization_map", {"optimization_id": "../bad", "user_id": "other"}
            )
            assert invalid.is_error
            assert len(requests) == before
    finally:
        await core.aclose()


async def test_map_tool_language_rides_on_the_link_only() -> None:
    requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "map_url": "https://vepathos.com/shared/routes/opaque",
                "expires_at": "2026-09-16T12:00:00Z",
                "access": "anyone_with_link",
            },
        )

    core = VepathosApiClient(
        "http://core", "service-secret", max_attempts=1, transport=httpx.MockTransport(respond)
    )
    try:
        settings = make_settings(MCP_TRANSPORT="stdio", MCP_MAP_SHARES_ENABLED=True)
        async with Client(build_server(settings, core)) as client:
            tool = {t.name: t for t in (await client.list_tools()).tools}["create_optimization_map"]
            assert "language" in tool.input_schema["properties"]

            async def call(language: str | None) -> Any:
                args: dict[str, Any] = {"optimization_id": "mcp_example123"}
                if language is not None:
                    args["language"] = language
                return await client.call_tool("create_optimization_map", args)

            es = await call("es-AR")
            assert not es.is_error
            assert es.structured_content["map_url"] == "https://vepathos.com/shared/routes/opaque?lang=es"
            assert es.structured_content["language"] == "es"

            pt = await call("pt_BR")
            assert pt.structured_content["map_url"].endswith("/opaque?lang=pt")

            # Unsupported language: plain link, the page follows the viewer's browser.
            fr = await call("fr")
            assert fr.structured_content["map_url"] == "https://vepathos.com/shared/routes/opaque"
            assert "language" not in fr.structured_content

            none = await call(None)
            assert none.structured_content["map_url"] == "https://vepathos.com/shared/routes/opaque"

            # Same Core call every time: body {}, same idempotency key, so one share per job.
            assert len(requests) == 4
            assert {r.content for r in requests} == {b"{}"}
            assert {r.headers["Idempotency-Key"] for r in requests} == {"map:mcp_example123"}

            before = len(requests)
            bad = await call("es;DROP")
            assert bad.is_error
            assert len(requests) == before
    finally:
        await core.aclose()

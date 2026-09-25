"""Wire-level tests of the Streamable HTTP endpoint (ASGI app, no network)."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from typing import Any

import httpx
import pytest

from tests.conftest import API_KEY, DEV_TOKEN, make_settings, sample_arguments, tool_arguments
from vepathos_mcp.app import create_app
from vepathos_mcp.clients.vepathos_api import VepathosApiClient

ACCEPT = "application/json, text/event-stream"


def parse_rpc(response: httpx.Response) -> dict[str, Any]:
    if response.headers.get("content-type", "").startswith("text/event-stream"):
        messages = [
            json.loads(line[5:].strip())
            for line in response.text.splitlines()
            if line.startswith("data:") and line[5:].strip()
        ]
        final = [m for m in messages if "result" in m or "error" in m]
        assert final, response.text
        return final[-1]
    return response.json()


@asynccontextmanager
async def running_app(
    core_client_factory: Callable[..., VepathosApiClient], **overrides: Any
) -> AsyncIterator[httpx.AsyncClient]:
    """Run the app lifespan and the requests in the same task (anyio cancel scopes require it)."""

    settings = make_settings(
        MCP_PUBLIC_URL="http://localhost:8080", MCP_ALLOWED_HOSTS="localhost:*", **overrides
    )
    app = create_app(settings, core=core_client_factory())
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://localhost:8080"
        ) as client:
            yield client


@pytest.fixture
def http(core_client_factory: Callable[..., VepathosApiClient]) -> Callable[..., Any]:
    def open_app(**overrides: Any) -> Any:
        return running_app(core_client_factory, **overrides)

    return open_app


def legacy_initialize() -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "claude-code", "version": "2.0.0"},
        },
    }


async def test_unauthenticated_request_is_rejected(http: Callable[..., Any]) -> None:
    async with http() as client:
        response = await client.post("/mcp", json=legacy_initialize(), headers={"Accept": ACCEPT})
        assert response.status_code == 401
        assert "resource_metadata" not in response.headers.get("www-authenticate", "")


async def test_oauth_mode_advertises_protected_resource_metadata(http: Callable[..., Any]) -> None:
    async with http(AUTH_MODES="oauth") as client:
        response = await client.post("/mcp", json=legacy_initialize(), headers={"Accept": ACCEPT})
        assert response.status_code == 401
        challenge = response.headers["www-authenticate"]
        assert (
            'resource_metadata="http://localhost:8080/.well-known/oauth-protected-resource/mcp"' in challenge
        )

        metadata = (await client.get("/.well-known/oauth-protected-resource/mcp")).json()
        assert metadata["resource"] == "http://localhost:8080/mcp"
        assert metadata["authorization_servers"] == ["https://api.vepathos.com"]
        assert metadata["scopes_supported"] == ["optimize"]


async def test_oauth_plus_api_key_keeps_keys_and_advertises_prm(http: Callable[..., Any]) -> None:
    async with http(AUTH_MODES="oauth,api_key") as client:
        denied = await client.post("/mcp", json=legacy_initialize(), headers={"Accept": ACCEPT})
        assert denied.status_code == 401
        assert "resource_metadata" in denied.headers.get("www-authenticate", "")

        allowed = await client.post(
            "/mcp",
            json=legacy_initialize(),
            headers={"Accept": ACCEPT, "Authorization": f"Bearer {API_KEY}"},
        )
        assert allowed.status_code == 200, allowed.text


async def test_legacy_client_stateless_initialize_and_tools(http: Callable[..., Any]) -> None:
    async with http() as client:
        headers = {"Accept": ACCEPT, "Authorization": f"Bearer {DEV_TOKEN}"}
        init = await client.post("/mcp", json=legacy_initialize(), headers=headers)
        assert init.status_code == 200, init.text
        assert "mcp-session-id" not in init.headers  # stateless: no session affinity required
        body = parse_rpc(init)
        assert body["result"]["serverInfo"]["name"] == "vepathos"
        assert "call get_account, list_fleet and list_plans" in body["result"]["instructions"]
        assert "Vepathos plans delivery operations" in body["result"]["instructions"]

        listed = await client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
            headers={**headers, "MCP-Protocol-Version": "2025-06-18"},
        )
        tools = {t["name"]: t for t in parse_rpc(listed)["result"]["tools"]}
        assert tools["optimize_routes"]["annotations"]["idempotentHint"] is True
        assert tools["get_optimization_result"]["annotations"]["readOnlyHint"] is True

        called = await client.post(
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {"name": "optimize_routes", "arguments": tool_arguments(sample_arguments(stops=3))},
            },
            headers={**headers, "MCP-Protocol-Version": "2025-06-18"},
        )
        result = parse_rpc(called)["result"]
        assert result["isError"] is False, result
        assert result["structuredContent"]["optimization_id"].startswith("mcp_")


async def test_modern_client_discover(http: Callable[..., Any]) -> None:
    async with http() as client:
        request = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "server/discover",
            "params": {
                "_meta": {
                    "io.modelcontextprotocol/protocolVersion": "2026-07-28",
                    "io.modelcontextprotocol/clientInfo": {"name": "test-client", "version": "1.0"},
                    "io.modelcontextprotocol/clientCapabilities": {},
                }
            },
        }
        response = await client.post(
            "/mcp",
            json=request,
            headers={
                "Accept": ACCEPT,
                "Authorization": f"Bearer {DEV_TOKEN}",
                "MCP-Protocol-Version": "2026-07-28",
                "Mcp-Method": "server/discover",
            },
        )
        assert response.status_code == 200, response.text
        result = parse_rpc(response)["result"]
        assert "2026-07-28" in result["supportedVersions"]
        assert "tools" in result["capabilities"]


async def test_dns_rebinding_protection(http: Callable[..., Any]) -> None:
    async with http() as client:
        response = await client.post(
            "/mcp",
            json=legacy_initialize(),
            headers={"Accept": ACCEPT, "Authorization": f"Bearer {DEV_TOKEN}", "Host": "attacker.example"},
        )
        assert response.status_code in (400, 403, 421)


async def test_openai_apps_challenge_is_absent_until_configured(http: Callable[..., Any]) -> None:
    async with http() as client:
        response = await client.get("/.well-known/openai-apps-challenge")
    assert response.status_code == 404


async def test_openai_apps_challenge_is_the_token_as_plain_text(http: Callable[..., Any]) -> None:
    async with http(OPENAI_APPS_CHALLENGE="abcXYZ123") as client:
        response = await client.get("/.well-known/openai-apps-challenge")
    assert response.status_code == 200
    assert response.text == "abcXYZ123"
    assert response.headers["content-type"].startswith("text/plain")
    assert "\n" not in response.text


async def test_health_ready_metrics(http: Callable[..., Any]) -> None:
    async with http() as client:
        assert (await client.get("/health")).json()["status"] == "ok"
        ready = await client.get("/ready")
        assert ready.status_code == 200 and ready.json()["dependencies"]["core"] == "ok"
        metrics = await client.get("/metrics")
        assert metrics.status_code in (200, 404)  # only served to private networks or with a bearer token


async def test_metrics_with_bearer_token(http: Callable[..., Any]) -> None:
    async with http(METRICS_BEARER_TOKEN="metrics-secret") as client:
        assert (await client.get("/metrics")).status_code == 401
        ok = await client.get("/metrics", headers={"Authorization": "Bearer metrics-secret"})
        assert ok.status_code == 200 and "mcp_tool_calls_total" in ok.text


async def test_preflight_is_answered_before_authentication(http: Callable[..., Any]) -> None:
    """A browser sends OPTIONS without credentials; a 401 here blocks the call it precedes."""

    async with http(AUTH_MODES="oauth,api_key") as client:
        response = await client.options(
            "/mcp",
            headers={
                "Origin": "https://example.test",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "authorization,content-type",
            },
        )
    assert response.status_code == 204
    assert response.headers["access-control-allow-origin"] == "*"
    allowed = response.headers["access-control-allow-headers"].lower()
    assert "authorization" in allowed and "mcp-protocol-version" in allowed
    assert "POST" in response.headers["access-control-allow-methods"]


async def test_the_401_discovery_pointer_is_readable_from_a_browser(http: Callable[..., Any]) -> None:
    async with http(AUTH_MODES="oauth,api_key") as client:
        response = await client.post(
            "/mcp",
            headers={"Accept": ACCEPT, "Content-Type": "application/json", "Origin": "https://example.test"},
            json=legacy_initialize(),
        )
    assert response.status_code == 401
    # Cross-origin JavaScript cannot read WWW-Authenticate unless the server exposes it, and without
    # it the client never learns where the authorization server is.
    exposed = response.headers.get("access-control-expose-headers", "").lower()
    assert "www-authenticate" in exposed
    assert response.headers["access-control-allow-origin"] == "*"


async def test_resource_metadata_without_the_resource_path_is_recoverable(
    http: Callable[..., Any],
) -> None:
    async with http(AUTH_MODES="oauth") as client:
        response = await client.get("/.well-known/oauth-protected-resource")
        assert response.status_code == 307
        assert response.headers["location"] == "/.well-known/oauth-protected-resource/mcp"

        followed = await client.get("/.well-known/oauth-protected-resource", follow_redirects=True)
    assert followed.status_code == 200
    assert followed.json()["resource"].endswith("/mcp")

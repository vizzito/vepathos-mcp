"""Build the MCP server: tools, instructions and resource-server authentication."""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable

import anyio
from mcp.server.auth.settings import AuthSettings
from mcp.server.mcpserver import MCPServer

from vepathos_mcp import SERVER_NAME, SERVER_TITLE, __version__
from vepathos_mcp.auth.verifier import CompositeTokenVerifier, JwksTokenVerifier
from vepathos_mcp.clients.vepathos_api import VepathosApiClient
from vepathos_mcp.config import AuthMode, Settings
from vepathos_mcp.prompts import register_prompts
from vepathos_mcp.rate_limit import RateLimiter
from vepathos_mcp.reference import build_reference, render_markdown
from vepathos_mcp.tools import build_tools
from vepathos_mcp.tools.descriptions import server_instructions
from vepathos_mcp.tools.runtime import ToolDeps


def build_auth_settings(settings: Settings) -> AuthSettings:
    oauth = AuthMode.OAUTH in settings.auth_modes
    return AuthSettings(
        issuer_url=settings.oauth_issuer if oauth else settings.core_base_url,  # type: ignore[arg-type]
        # Protected Resource Metadata (RFC 9728) is only advertised when OAuth is enabled.
        resource_server_url=settings.resource_url if oauth else None,  # type: ignore[arg-type]
        required_scopes=settings.oauth_advertised_scopes,
        # The verifier checks the audience itself (and API keys carry no audience).
        validate_token_resource=False if oauth else None,
    )


def build_server(
    settings: Settings,
    core: VepathosApiClient,
    *,
    jwks_verifier: JwksTokenVerifier | None = None,
    sleep: Callable[[float], Awaitable[None]] = anyio.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> MCPServer:
    deps = ToolDeps(
        settings=settings,
        core=core,
        rate_limiter=RateLimiter(
            {
                "optimize": settings.rate_limit_optimize_per_minute,
                "calls": settings.rate_limit_calls_per_minute,
                "catalog_writes": settings.rate_limit_catalog_writes_per_minute,
            }
        ),
        sleep=sleep,
        clock=clock,
    )
    server = MCPServer(
        name=SERVER_NAME,
        title=SERVER_TITLE,
        version=__version__,
        description=(
            "Delivery dispatch for the connected Vepathos account: fleet, imports, optimized routes "
            "and results."
        ),
        instructions=server_instructions(
            confirm_before_optimize=settings.confirm_before_optimize,
            import_tools=settings.import_tools_enabled,
            map_shares=settings.map_shares_enabled,
            catalog_writes=settings.catalog_write_tools_enabled,
        ),
        website_url="https://vepathos.com",
        token_verifier=CompositeTokenVerifier(settings, jwks_verifier=jwks_verifier),
        auth=build_auth_settings(settings),
        tools=build_tools(deps),
        log_level=settings.log_level.upper(),  # type: ignore[arg-type]
    )
    # For the user, not the model: prompts they pick, and the reference of what THIS server publishes.
    # Neither travels in every conversation, and no tool needs them to be used correctly.
    register_prompts(server)
    rendered: list[str] = []

    def tools_reference() -> str:
        # Rendered on the first read and kept: it depends only on this server's settings.
        if not rendered:
            rendered.append(render_markdown(build_reference(settings), deployment=True))
        return rendered[0]

    server.resource(
        "vepathos://docs/reference",
        name="vepathos_reference",
        title="Vepathos tools reference",
        description=(
            "Every tool this Vepathos server publishes: what it does, its inputs, and which tools follow "
            "which. Generated from the server's own registry."
        ),
        mime_type="text/markdown",
    )(tools_reference)
    return server

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
from vepathos_mcp.rate_limit import RateLimiter
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
            }
        ),
        sleep=sleep,
        clock=clock,
    )
    return MCPServer(
        name=SERVER_NAME,
        title=SERVER_TITLE,
        version=__version__,
        description="Large-scale delivery and fleet route optimization for AI agents.",
        instructions=server_instructions(
            confirm_before_optimize=settings.confirm_before_optimize,
            import_tools=settings.import_tools_enabled,
        ),
        website_url="https://vepathos.com",
        token_verifier=CompositeTokenVerifier(settings, jwks_verifier=jwks_verifier),
        auth=build_auth_settings(settings),
        tools=build_tools(deps),
        log_level=settings.log_level.upper(),  # type: ignore[arg-type]
    )

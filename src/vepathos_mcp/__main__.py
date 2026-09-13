"""Command-line entry point.

vepathos-mcp                  # Streamable HTTP server (default), configured from environment
vepathos-mcp --transport stdio  # local stdio server for self-hosting / development
"""

from __future__ import annotations

import argparse
import os

import uvicorn

from vepathos_mcp.config import Settings


def main() -> None:
    parser = argparse.ArgumentParser(prog="vepathos-mcp", description="Vepathos MCP server")
    parser.add_argument("--transport", choices=["http", "stdio"], default=None)
    parser.add_argument("--host", default=None)
    parser.add_argument("--port", type=int, default=None)
    args = parser.parse_args()

    if args.transport:
        os.environ["MCP_TRANSPORT"] = args.transport
    settings = Settings()

    if settings.transport == "stdio":
        from vepathos_mcp.clients.vepathos_api import VepathosApiClient
        from vepathos_mcp.server import build_server
        from vepathos_mcp.telemetry import metrics
        from vepathos_mcp.telemetry.logging import configure_logging

        configure_logging(settings.log_level)
        core = VepathosApiClient(
            settings.core_base_url,
            settings.core_service_key.get_secret_value(),
            timeout_seconds=settings.core_timeout_seconds,
            submit_timeout_seconds=settings.core_submit_timeout_seconds,
            observer=metrics.observe_backend,
        )
        build_server(settings, core).run(transport="stdio")
        return

    from vepathos_mcp.app import create_app

    uvicorn.run(
        create_app(settings),
        host=args.host or settings.host,
        port=args.port or settings.port,
        proxy_headers=True,
        forwarded_allow_ips=os.environ.get("FORWARDED_ALLOW_IPS", "127.0.0.1"),
        server_header=False,
        log_level=settings.log_level.lower(),
        timeout_graceful_shutdown=30,
    )


if __name__ == "__main__":
    main()

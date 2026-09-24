"""Command-line entry point.

vepathos-mcp                  # Streamable HTTP server (default), configured from environment
vepathos-mcp --transport stdio  # local stdio server for self-hosting / development
vepathos-mcp tools            # what each tool does, from the same registry the server publishes
vepathos-mcp workflows        # which tools follow which
vepathos-mcp doctor           # this configuration, what it publishes, and whether Core answers

The three commands are for whoever develops or operates the server. No MCP client needs them: a model
learns the tools from their descriptions and the server instructions.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import uvicorn

from vepathos_mcp.config import Settings

COMMANDS = """\
Commands:
  vepathos-mcp              run the server (HTTP by default; --transport stdio for local use)
  vepathos-mcp tools        what each tool does, from the same registry the server publishes
  vepathos-mcp workflows    which tools follow which
  vepathos-mcp doctor       check this configuration and the connection to Vepathos Core
"""


def _help_epilog() -> str | None:
    # Help must never be able to stop the server from starting: on any failure, plain help.
    try:
        from vepathos_mcp.reference import build_reference, render_index

        index = render_index(build_reference())
    except Exception:
        return None
    return f"Tools (every optional one enabled; `vepathos-mcp tools` has the detail):\n{index}\n\n{COMMANDS}"


def build_parser(argv: list[str]) -> argparse.ArgumentParser:
    wants_help = bool({"-h", "--help"} & set(argv))
    parser = argparse.ArgumentParser(
        prog="vepathos-mcp",
        description="Vepathos MCP server",
        epilog=_help_epilog() if wants_help else None,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--transport", choices=["http", "stdio"], default=None)
    parser.add_argument("--host", default=None)
    parser.add_argument("--port", type=int, default=None)
    commands = parser.add_subparsers(dest="command")
    tools = commands.add_parser("tools", help="what each tool does")
    output = tools.add_mutually_exclusive_group()
    output.add_argument("--markdown", action="store_true", help="the full reference as Markdown")
    output.add_argument("--table", action="store_true", help="the summary table as Markdown")
    output.add_argument("--write-docs", action="store_true", help="rewrite the generated documents")
    output.add_argument("--check", action="store_true", help="exit 1 when a generated document is stale")
    commands.add_parser("workflows", help="which tools follow which")
    doctor = commands.add_parser("doctor", help="check this configuration and the connection to Core")
    doctor.add_argument("--offline", action="store_true", help="do not call Core")
    return parser


def _tools_command(args: argparse.Namespace) -> None:
    from vepathos_mcp import reference

    if args.write_docs or args.check:
        root = Path.cwd()
        if not (root / "docs").is_dir():
            print("run from the repository root", file=sys.stderr)
            raise SystemExit(2)
        stale = reference.write_documents(root) if args.write_docs else reference.stale_documents(root)
        for name in stale:
            print(("rewrote " if args.write_docs else "stale: ") + name)
        if args.check and stale:
            print("run `vepathos-mcp tools --write-docs`", file=sys.stderr)
            raise SystemExit(1)
        return
    ref = reference.build_reference()
    if args.markdown:
        print(reference.render_markdown(ref, deployment=False), end="")
    elif args.table:
        print(reference.render_table(ref, "repo"))
    else:
        print(reference.render_text(ref), end="")


def _doctor_command(offline: bool) -> None:
    import anyio
    from pydantic import ValidationError

    from vepathos_mcp.doctor import EXIT_INVALID_CONFIGURATION, run_doctor

    try:
        settings = Settings()
    except ValidationError as exc:
        print("The configuration is not valid:", file=sys.stderr)
        for error in exc.errors():
            print(f"  {error['msg']}", file=sys.stderr)
        raise SystemExit(EXIT_INVALID_CONFIGURATION) from None

    async def run() -> int:
        return await run_doctor(settings, None, print, offline=offline)

    code = anyio.run(run)
    if code:
        raise SystemExit(code)


def main(argv: list[str] | None = None) -> None:
    arguments = sys.argv[1:] if argv is None else argv
    args = build_parser(arguments).parse_args(arguments)

    if args.command == "tools":
        return _tools_command(args)
    if args.command == "workflows":
        from vepathos_mcp.reference import build_reference, render_workflows

        print(render_workflows(build_reference()), end="")
        return
    if args.command == "doctor":
        return _doctor_command(args.offline)

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

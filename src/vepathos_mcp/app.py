"""ASGI application: MCP over stateless Streamable HTTP plus /health, /ready and /metrics."""

from __future__ import annotations

import ipaddress
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import anyio
from mcp.server.mcpserver import MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, PlainTextResponse, Response

from vepathos_mcp import __version__
from vepathos_mcp.auth.verifier import JwksTokenVerifier
from vepathos_mcp.clients.vepathos_api import VepathosApiClient
from vepathos_mcp.config import AuthMode, Settings
from vepathos_mcp.server import build_server
from vepathos_mcp.telemetry import metrics
from vepathos_mcp.telemetry.logging import configure_logging, log_event


class ReadinessProbe:
    """Readiness that does not cascade backend outages into taking every replica out of rotation.

    - 503 only for local faults: no service key configured, or (OAuth) signing keys never loaded.
    - A Core outage is reported as `degraded` with HTTP 200; tools then fail fast with
      BACKEND_UNAVAILABLE while the circuit breaker is open.
    """

    CORE_TTL_SECONDS = 15.0
    JWKS_TTL_SECONDS = 300.0

    def __init__(self, settings: Settings, core: VepathosApiClient) -> None:
        self._settings = settings
        self._core = core
        self._core_checked_at = -1e9
        self._core_ok = False
        self._jwks_checked_at = -1e9
        self._jwks_ever_loaded = False
        self._jwks_ok = False

    async def _check_core(self) -> bool:
        now = time.monotonic()
        if now - self._core_checked_at >= self.CORE_TTL_SECONDS:
            self._core_ok = await self._core.health()
            self._core_checked_at = now
        return self._core_ok

    async def _check_jwks(self) -> bool:
        now = time.monotonic()
        if now - self._jwks_checked_at >= self.JWKS_TTL_SECONDS:
            import jwt  # local import keeps startup light

            client = jwt.PyJWKClient(self._settings.oauth_jwks_url, cache_keys=False, timeout=3)
            try:
                keys = await anyio.to_thread.run_sync(client.get_signing_keys)
                self._jwks_ok = bool(keys)
            except jwt.PyJWKClientError:
                self._jwks_ok = False
            self._jwks_ever_loaded = self._jwks_ever_loaded or self._jwks_ok
            self._jwks_checked_at = now
        return self._jwks_ok

    async def evaluate(self) -> tuple[int, dict[str, object]]:
        dependencies: dict[str, str] = {}
        ready = bool(self._settings.core_service_key.get_secret_value())
        dependencies["core"] = "ok" if await self._check_core() else "degraded"
        if AuthMode.OAUTH in self._settings.auth_modes:
            jwks_ok = await self._check_jwks()
            dependencies["signing_keys"] = "ok" if jwks_ok else "degraded"
            ready = ready and self._jwks_ever_loaded
        body: dict[str, object] = {"status": "ready" if ready else "not_ready", "dependencies": dependencies}
        return (200 if ready else 503), body


def _is_private_client(request: Request) -> bool:
    host = request.client.host if request.client else None
    if host is None:
        return False
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return host == "localhost"
    return address.is_loopback or address.is_private


def register_operational_routes(mcp: MCPServer, settings: Settings, core: VepathosApiClient) -> None:
    probe = ReadinessProbe(settings, core)

    @mcp.custom_route("/health", methods=["GET"])  # type: ignore[untyped-decorator]
    async def health(_: Request) -> Response:
        return JSONResponse({"status": "ok", "version": __version__})

    @mcp.custom_route("/ready", methods=["GET"])  # type: ignore[untyped-decorator]
    async def ready(_: Request) -> Response:
        status, body = await probe.evaluate()
        return JSONResponse(body, status_code=status)

    @mcp.custom_route("/metrics", methods=["GET"])  # type: ignore[untyped-decorator]
    async def metrics_endpoint(request: Request) -> Response:
        expected = settings.metrics_bearer_token.get_secret_value() if settings.metrics_bearer_token else None
        if expected:
            if request.headers.get("authorization") != f"Bearer {expected}":
                return PlainTextResponse("unauthorized", status_code=401)
        elif not _is_private_client(request):
            return PlainTextResponse("not found", status_code=404)
        return Response(generate_latest(metrics.REGISTRY), media_type=CONTENT_TYPE_LATEST)


def create_app(
    settings: Settings | None = None,
    *,
    core: VepathosApiClient | None = None,
    jwks_verifier: JwksTokenVerifier | None = None,
) -> Starlette:
    settings = settings or Settings()
    configure_logging(settings.log_level)
    core = core or VepathosApiClient(
        settings.core_base_url,
        settings.core_service_key.get_secret_value(),
        timeout_seconds=settings.core_timeout_seconds,
        submit_timeout_seconds=settings.core_submit_timeout_seconds,
        observer=metrics.observe_backend,
    )
    mcp = build_server(settings, core, jwks_verifier=jwks_verifier)
    register_operational_routes(mcp, settings, core)

    app = mcp.streamable_http_app(
        streamable_http_path=settings.mcp_path,
        stateless_http=True,
        json_response=False,
        max_request_body_size=settings.max_request_body_bytes,
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=settings.allowed_host_list,
            allowed_origins=settings.allowed_origin_list,
        ),
        host=settings.host,
    )

    sdk_lifespan = app.router.lifespan_context

    @asynccontextmanager
    async def lifespan(starlette_app: Starlette) -> AsyncIterator[None]:
        log_event(
            "startup",
            version=__version__,
            environment=settings.environment,
            auth_modes=",".join(m.value for m in settings.auth_modes),
            resource=settings.resource_url,
        )
        async with sdk_lifespan(starlette_app):
            try:
                yield
            finally:
                await core.aclose()
                log_event("shutdown")

    app.router.lifespan_context = lifespan
    return app

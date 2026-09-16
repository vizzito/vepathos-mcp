"""Shared plumbing for tool handlers: dependencies, identity, bounded waits and instrumentation."""

from __future__ import annotations

import json
import logging
import time
import uuid
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

import anyio
from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.mcpserver.context import Context
from mcp_types import CallToolResult

from vepathos_mcp.auth.upstream import subject_key, upstream_authorization
from vepathos_mcp.clients.core_models import CoreJobStatusResponse
from vepathos_mcp.clients.vepathos_api import CallContext, VepathosApiClient
from vepathos_mcp.config import Settings
from vepathos_mcp.errors.codes import PLAN_CODES, DomainError, ErrorCode
from vepathos_mcp.rate_limit import RateLimiter
from vepathos_mcp.telemetry import metrics
from vepathos_mcp.telemetry.client_detect import detect_client
from vepathos_mcp.telemetry.logging import log_event
from vepathos_mcp.tools.rendering import error_result, estimate_tokens, result_text


class AccountLabelCache:
    """Short-lived `subject -> account label`, so plan errors can name the connected account.

    An empty string is a remembered miss. Bounded: the label is a convenience, never state a tool
    result depends on.
    """

    def __init__(self, ttl_seconds: float = 300.0, max_entries: int = 1024) -> None:
        self._ttl = ttl_seconds
        self._max_entries = max_entries
        self._entries: dict[str, tuple[float, str]] = {}

    def get(self, subject: str, now: float) -> str | None:
        entry = self._entries.get(subject)
        if entry is None:
            return None
        expires_at, label = entry
        if expires_at <= now:
            self._entries.pop(subject, None)
            return None
        return label

    def put(self, subject: str, label: str, now: float) -> None:
        if len(self._entries) >= self._max_entries:
            for stale, (expires_at, _) in list(self._entries.items()):
                if expires_at <= now:
                    self._entries.pop(stale, None)
            if len(self._entries) >= self._max_entries:
                self._entries.clear()
        self._entries[subject] = (now + self._ttl, label)


@dataclass
class ToolDeps:
    settings: Settings
    core: VepathosApiClient
    rate_limiter: RateLimiter
    sleep: Callable[[float], Awaitable[None]] = anyio.sleep
    clock: Callable[[], float] = field(default=time.monotonic)
    account_labels: AccountLabelCache = field(default_factory=AccountLabelCache)


@dataclass(frozen=True)
class RequestIdentity:
    call: CallContext
    subject: str
    client_label: str
    protocol_version: str | None


def raw_arguments(ctx: Context) -> dict[str, Any]:
    """Arguments exactly as the client sent them (unknown fields included, so they can be rejected)."""

    params = ctx.request_context.params
    arguments = params.get("arguments") if isinstance(params, Mapping) else None
    return dict(arguments) if isinstance(arguments, Mapping) else {}


def resolve_identity(ctx: Context, deps: ToolDeps) -> RequestIdentity:
    access_token = get_access_token()
    authorization = upstream_authorization(deps.settings, access_token)

    client_name: str | None = None
    session = ctx.request_context.session
    client_params = getattr(session, "client_params", None)
    client_info = getattr(client_params, "client_info", None)
    if client_info is not None:
        client_name = getattr(client_info, "name", None)
    client_label = detect_client(access_token.client_id if access_token else None, client_name)

    headers = ctx.headers or {}
    traceparent = headers.get("traceparent")
    request_id = uuid.uuid4().hex
    return RequestIdentity(
        call=CallContext(
            authorization=authorization,
            client_label=client_label,
            traceparent=traceparent if traceparent and len(traceparent) <= 128 else None,
            request_id=request_id,
        ),
        subject=subject_key(deps.settings, access_token),
        client_label=client_label,
        protocol_version=ctx.protocol_version,
    )


async def wait_for_terminal(
    ctx: Context,
    deps: ToolDeps,
    identity: RequestIdentity,
    status: CoreJobStatusResponse,
    budget_seconds: float,
) -> CoreJobStatusResponse:
    """Poll Core until the job ends or the budget runs out, reporting real progress when available."""

    deadline = deps.clock() + budget_seconds
    last_percent: int | None = None
    while not status.is_terminal:
        percent = status.progress.percent if status.progress else None
        if percent is not None and percent != last_percent:
            last_percent = percent
            stage = status.progress.stage if status.progress else None
            try:
                await ctx.report_progress(percent, 100, message=stage)
            except Exception:
                log_event("progress_notification_failed", logging.DEBUG)
        remaining = deadline - deps.clock()
        if remaining <= 0:
            break
        await deps.sleep(min(deps.settings.poll_interval_seconds, remaining))
        status = await deps.core.get_job(identity.call, status.job_id)
    return status


async def name_connected_account(
    deps: ToolDeps, identity: RequestIdentity | None, err: DomainError
) -> DomainError:
    """Add the connected account to plan errors: the usual cause is being on the wrong one."""

    if identity is None or err.code not in PLAN_CODES:
        return err
    from vepathos_mcp.tools.account import remember_account  # circular at module level

    label = await remember_account(deps, identity)
    if not label:
        return err
    err.details["connected_account"] = label
    named = f"The connected Vepathos account is {label}: name it when reporting this to the user."
    err.suggestion = f"{err.suggestion} {named}" if err.suggestion else named
    return err


async def instrumented(
    tool: str,
    ctx: Context,
    deps: ToolDeps,
    handler: Callable[[RequestIdentity, dict[str, Any]], Awaitable[CallToolResult]],
) -> CallToolResult:
    started = deps.clock()
    arguments = raw_arguments(ctx)
    request_bytes = len(json.dumps(arguments, separators=(",", ":"), ensure_ascii=False).encode("utf-8"))
    client_label = "other"
    subject = "anonymous"
    outcome = "success"
    error_code = ""
    identity: RequestIdentity | None = None
    result: CallToolResult
    try:
        identity = resolve_identity(ctx, deps)
        client_label, subject = identity.client_label, identity.subject
        metrics.HTTP_REQUESTS.labels(
            mcp_method="tools/call",
            protocol_version=identity.protocol_version or "unknown",
            client_type=client_label,
        ).inc()
        result = await handler(identity, arguments)
    except DomainError as err:
        result = error_result(await name_connected_account(deps, identity, err))
    except Exception as exc:
        log_event("tool_crashed", logging.ERROR, tool=tool, exception=type(exc).__name__)
        result = error_result(
            DomainError(
                ErrorCode.INTERNAL_ERROR,
                "Vepathos MCP hit an unexpected error.",
                suggestion="Try again. If it persists, contact Vepathos support.",
                retryable=False,
            )
        )

    if result.is_error:
        outcome = "error"
        payload: dict[str, Any] = (
            result.structured_content if isinstance(result.structured_content, dict) else {}
        )
        raw_error = payload.get("error")
        error: dict[str, Any] = raw_error if isinstance(raw_error, dict) else {}
        error_code = str(error.get("code", ""))
        if error_code in {c.value for c in PLAN_CODES}:
            raw_details = error.get("details")
            details: dict[str, Any] = raw_details if isinstance(raw_details, dict) else {}
            metrics.PLAN_REJECTIONS.labels(code=error_code, reason=str(details.get("reason", ""))).inc()
        if error_code in {ErrorCode.AUTHENTICATION_REQUIRED.value, ErrorCode.INVALID_CREDENTIALS.value}:
            metrics.AUTH_FAILURES.labels(reason=error_code.lower()).inc()

    text = result_text(result)
    elapsed = deps.clock() - started
    metrics.TOOL_CALLS.labels(
        tool=tool, outcome=outcome, error_code=error_code, client_type=client_label
    ).inc()
    metrics.TOOL_LATENCY.labels(tool=tool).observe(elapsed)
    metrics.TOOL_REQUEST_BYTES.labels(tool=tool).observe(request_bytes)
    metrics.TOOL_RESPONSE_BYTES.labels(tool=tool).observe(len(text.encode("utf-8")))
    metrics.TOOL_RESPONSE_TOKENS.labels(tool=tool).observe(estimate_tokens(text))

    structured = result.structured_content if isinstance(result.structured_content, dict) else {}
    log_event(
        "tool_call",
        tool=tool,
        outcome=outcome,
        error_code=error_code or None,
        optimization_id=structured.get("optimization_id"),
        status=structured.get("status"),
        error_message=(
            (structured.get("error") or {}).get("message")
            if isinstance(structured.get("error"), dict)
            else None
        ),
        account_hash=subject,
        client_type=client_label,
        latency_ms=round(elapsed * 1000),
        request_bytes=request_bytes,
        response_tokens=estimate_tokens(text),
    )
    return result

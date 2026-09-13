"""Prometheus metrics for the MCP channel.

Labels stay low-cardinality (no account ids, no optimization ids). Business analytics — signups,
conversions, trial usage — are computed from Vepathos Core data, not from these metrics.
"""

from __future__ import annotations

from prometheus_client import CollectorRegistry, Counter, Histogram

REGISTRY = CollectorRegistry(auto_describe=True)

HTTP_REQUESTS = Counter(
    "mcp_http_requests_total",
    "MCP JSON-RPC requests received (stateless replacement for connection counts).",
    ["mcp_method", "protocol_version", "client_type"],
    registry=REGISTRY,
)
TOOL_CALLS = Counter(
    "mcp_tool_calls_total",
    "Tool calls by outcome.",
    ["tool", "outcome", "error_code", "client_type"],
    registry=REGISTRY,
)
AUTH_FAILURES = Counter(
    "mcp_auth_failures_total",
    "Rejected credentials at the MCP or Core boundary.",
    ["reason"],
    registry=REGISTRY,
)
PLAN_REJECTIONS = Counter(
    "mcp_plan_rejections_total",
    "Requests rejected by plan entitlements (upgrade, quota, concurrency).",
    ["code", "reason"],
    registry=REGISTRY,
)
BACKEND_LATENCY = Histogram(
    "mcp_backend_request_duration_seconds",
    "Latency of calls to Vepathos Core by operation.",
    ["operation", "status_class"],
    buckets=(0.05, 0.1, 0.25, 0.5, 1, 2, 5, 10, 20, 45),
    registry=REGISTRY,
)
TOOL_LATENCY = Histogram(
    "mcp_tool_duration_seconds",
    "End-to-end tool latency, including bounded waits.",
    ["tool"],
    buckets=(0.1, 0.25, 0.5, 1, 2, 5, 10, 15, 20, 30, 60),
    registry=REGISTRY,
)
TOOL_REQUEST_BYTES = Histogram(
    "mcp_tool_request_bytes",
    "Serialized tool argument size.",
    ["tool"],
    buckets=(512, 4096, 16384, 65536, 262144, 1048576, 4194304, 8388608),
    registry=REGISTRY,
)
TOOL_RESPONSE_BYTES = Histogram(
    "mcp_tool_response_bytes",
    "Serialized tool result size.",
    ["tool"],
    buckets=(256, 1024, 4096, 16384, 65536, 262144),
    registry=REGISTRY,
)
TOOL_RESPONSE_TOKENS = Histogram(
    "mcp_tool_response_tokens_estimate",
    "Approximate tokens of the text returned to the model (bytes / 4).",
    ["tool"],
    buckets=(50, 200, 500, 1000, 2500, 5000, 10000, 25000),
    registry=REGISTRY,
)
OPTIMIZATION_STOPS = Histogram(
    "mcp_optimization_stops",
    "Stops per submitted optimization.",
    buckets=(10, 50, 150, 500, 1000, 2000, 3000, 5000, 10000, 25000),
    registry=REGISTRY,
)


def observe_backend(operation: str, seconds: float, status: int | None) -> None:
    status_class = "network_error" if status is None else f"{status // 100}xx"
    BACKEND_LATENCY.labels(operation=operation, status_class=status_class).observe(seconds)

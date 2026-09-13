"""Build `CallToolResult`s: compact JSON text for the model plus `structuredContent`."""

from __future__ import annotations

import json
from typing import Any

from mcp_types import CallToolResult, TextContent
from pydantic import BaseModel

from vepathos_mcp.errors.codes import DomainError


def _compact(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, separators=(",", ":"))


def success_result(model: BaseModel) -> CallToolResult:
    data = model.model_dump(mode="json", exclude_none=True)
    return CallToolResult(content=[TextContent(type="text", text=_compact(data))], structured_content=data)


def error_result(error: DomainError) -> CallToolResult:
    data = {"error": error.to_payload()}
    return CallToolResult(
        content=[TextContent(type="text", text=_compact(data))],
        structured_content=data,
        is_error=True,
    )


def result_text(result: CallToolResult) -> str:
    return "".join(block.text for block in result.content if isinstance(block, TextContent))


def estimate_tokens(text: str) -> int:
    """Rough token estimate used for budgets and metrics (~4 bytes per token for JSON)."""

    return max(1, len(text.encode("utf-8")) // 4)

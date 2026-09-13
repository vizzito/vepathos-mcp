"""Tool registration. v1 exposes exactly two tools; there is intentionally no cancel tool."""

from __future__ import annotations

from mcp.server.mcpserver.tools import Tool
from mcp_types import ToolAnnotations

from vepathos_mcp.schemas.inputs import GetResultInput, OptimizeInput
from vepathos_mcp.schemas.jsonschema import inline_model_schema
from vepathos_mcp.tools import descriptions
from vepathos_mcp.tools.optimize import TOOL_NAME as OPTIMIZE_TOOL
from vepathos_mcp.tools.optimize import make_optimize_tool
from vepathos_mcp.tools.results import TOOL_NAME as GET_RESULT_TOOL
from vepathos_mcp.tools.results import make_get_result_tool
from vepathos_mcp.tools.runtime import ToolDeps


def build_tools(deps: ToolDeps) -> list[Tool]:
    optimize = Tool.from_function(
        make_optimize_tool(deps),
        name=OPTIMIZE_TOOL,
        title=descriptions.OPTIMIZE_TITLE,
        description=descriptions.OPTIMIZE_DESCRIPTION,
        annotations=ToolAnnotations(
            title=descriptions.OPTIMIZE_TITLE,
            read_only_hint=False,
            destructive_hint=False,
            idempotent_hint=True,
            open_world_hint=False,
        ),
        structured_output=True,
    )
    # Handlers read and validate the raw arguments themselves (strict, with structured errors);
    # the published schema is the strict model's schema.
    optimize.parameters = inline_model_schema(OptimizeInput)

    get_result = Tool.from_function(
        make_get_result_tool(deps),
        name=GET_RESULT_TOOL,
        title=descriptions.GET_RESULT_TITLE,
        description=descriptions.GET_RESULT_DESCRIPTION,
        annotations=ToolAnnotations(
            title=descriptions.GET_RESULT_TITLE,
            read_only_hint=True,
            destructive_hint=False,
            idempotent_hint=True,
            open_world_hint=False,
        ),
        structured_output=True,
    )
    get_result.parameters = inline_model_schema(GetResultInput)
    return [optimize, get_result]

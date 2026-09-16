"""Tool registration. There is intentionally no cancel tool."""

from __future__ import annotations

from mcp.server.mcpserver.tools import Tool
from mcp_types import ToolAnnotations

from vepathos_mcp.schemas.inputs import (
    GeocodeInput,
    GetAccountInput,
    GetGeocodeInput,
    GetResultInput,
    ListFleetInput,
    OptimizeInput,
)
from vepathos_mcp.schemas.jsonschema import inline_model_schema
from vepathos_mcp.tools import descriptions
from vepathos_mcp.tools.account import TOOL_NAME as GET_ACCOUNT_TOOL
from vepathos_mcp.tools.account import make_get_account_tool
from vepathos_mcp.tools.fleet import TOOL_NAME as LIST_FLEET_TOOL
from vepathos_mcp.tools.fleet import make_list_fleet_tool
from vepathos_mcp.tools.geocode import (
    GEOCODE_TOOL,
    GET_GEOCODE_TOOL,
    make_geocode_tool,
    make_get_geocode_tool,
)
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

    geocode = Tool.from_function(
        make_geocode_tool(deps),
        name=GEOCODE_TOOL,
        title=descriptions.GEOCODE_TITLE,
        description=descriptions.GEOCODE_DESCRIPTION,
        annotations=ToolAnnotations(
            title=descriptions.GEOCODE_TITLE,
            read_only_hint=False,
            destructive_hint=False,
            idempotent_hint=True,
            open_world_hint=False,
        ),
        structured_output=True,
    )
    geocode.parameters = inline_model_schema(GeocodeInput)

    get_geocode = Tool.from_function(
        make_get_geocode_tool(deps),
        name=GET_GEOCODE_TOOL,
        title=descriptions.GET_GEOCODE_TITLE,
        description=descriptions.GET_GEOCODE_DESCRIPTION,
        annotations=ToolAnnotations(
            title=descriptions.GET_GEOCODE_TITLE,
            read_only_hint=True,
            destructive_hint=False,
            idempotent_hint=True,
            open_world_hint=False,
        ),
        structured_output=True,
    )
    get_geocode.parameters = inline_model_schema(GetGeocodeInput)

    get_account = Tool.from_function(
        make_get_account_tool(deps),
        name=GET_ACCOUNT_TOOL,
        title=descriptions.GET_ACCOUNT_TITLE,
        description=descriptions.GET_ACCOUNT_DESCRIPTION,
        annotations=ToolAnnotations(
            title=descriptions.GET_ACCOUNT_TITLE,
            read_only_hint=True,
            destructive_hint=False,
            idempotent_hint=True,
            open_world_hint=False,
        ),
        structured_output=True,
    )
    get_account.parameters = inline_model_schema(GetAccountInput)

    list_fleet = Tool.from_function(
        make_list_fleet_tool(deps),
        name=LIST_FLEET_TOOL,
        title=descriptions.LIST_FLEET_TITLE,
        description=descriptions.LIST_FLEET_DESCRIPTION,
        annotations=ToolAnnotations(
            title=descriptions.LIST_FLEET_TITLE,
            read_only_hint=True,
            destructive_hint=False,
            idempotent_hint=True,
            open_world_hint=False,
        ),
        structured_output=True,
    )
    list_fleet.parameters = inline_model_schema(ListFleetInput)

    tools = [optimize, get_result, geocode, get_geocode, get_account, list_fleet]
    if deps.settings.map_shares_enabled:
        from vepathos_mcp.tools.maps import DESCRIPTION, CreateMapInput, make_map_tool

        share = Tool.from_function(
            make_map_tool(deps), name="create_optimization_map", title="Create route map",
            description=DESCRIPTION,
            annotations=ToolAnnotations(
                read_only_hint=False, destructive_hint=False,
                idempotent_hint=True, open_world_hint=False,
            ), structured_output=True,
        )
        share.parameters = inline_model_schema(CreateMapInput)
        tools.append(share)
    return tools

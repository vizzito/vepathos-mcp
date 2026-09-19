"""Tool registration. There is intentionally no cancel tool."""

from __future__ import annotations

from typing import Any

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
from vepathos_mcp.tools.automations import CREATE_TOOL_NAME as CREATE_AUTOMATION_TOOL
from vepathos_mcp.tools.automations import LIST_TOOL_NAME as LIST_AUTOMATIONS_TOOL
from vepathos_mcp.tools.automations import (
    CreateAutomationInput,
    ListAutomationsInput,
    make_create_automation_tool,
    make_list_automations_tool,
)
from vepathos_mcp.tools.fleet import TOOL_NAME as LIST_FLEET_TOOL
from vepathos_mcp.tools.fleet import make_list_fleet_tool
from vepathos_mcp.tools.geocode import (
    GEOCODE_TOOL,
    GET_GEOCODE_TOOL,
    make_geocode_tool,
    make_get_geocode_tool,
)
from vepathos_mcp.tools.import_tools import (
    GET_IMPORT_TOOL,
    IMPORT_FILE_TOOL,
    IMPORT_TEXT_TOOL,
    LIST_DATASETS_TOOL,
    OPTIMIZE_PLAN_TOOL,
    UPDATE_MAPPING_TOOL,
    GetImportInput,
    ImportFileInput,
    ImportTextInput,
    ListDatasetsInput,
    OptimizePlanInput,
    UpdateMappingInput,
    make_get_import_tool,
    make_import_file_tool,
    make_import_text_tool,
    make_list_datasets_tool,
    make_optimize_plan_tool,
    make_update_mapping_tool,
)
from vepathos_mcp.tools.optimize import TOOL_NAME as OPTIMIZE_TOOL
from vepathos_mcp.tools.optimize import make_optimize_tool
from vepathos_mcp.tools.plans import TOOL_NAME as LIST_PLANS_TOOL
from vepathos_mcp.tools.plans import ListPlansInput, make_list_plans_tool
from vepathos_mcp.tools.results import TOOL_NAME as GET_RESULT_TOOL
from vepathos_mcp.tools.results import make_get_result_tool
from vepathos_mcp.tools.runtime import ToolDeps


def optimize_schema(*, confirm_before_optimize: bool) -> dict[str, Any]:
    """The published optimize schema. Without the gate, `confirmed` changes nothing, so it is not
    advertised; the model still accepts it, because clients cache schemas and keep sending it."""

    schema = inline_model_schema(OptimizeInput)
    if confirm_before_optimize:
        return schema
    properties = {name: spec for name, spec in schema["properties"].items() if name != "confirmed"}
    return {**schema, "properties": properties}


def _tool(
    fn: Any,
    *,
    name: str,
    title: str,
    description: str,
    schema_model: type,
    read_only: bool,
    confirm_before_optimize: bool | None = None,
    idempotent: bool = True,
) -> Tool:
    tool = Tool.from_function(
        fn,
        name=name,
        title=title,
        description=description,
        annotations=ToolAnnotations(
            title=title,
            read_only_hint=read_only,
            destructive_hint=False,
            idempotent_hint=idempotent,
            open_world_hint=False,
        ),
        structured_output=True,
    )
    if confirm_before_optimize is not None and name == OPTIMIZE_TOOL:
        tool.parameters = optimize_schema(confirm_before_optimize=confirm_before_optimize)
    elif confirm_before_optimize is not None and name == OPTIMIZE_PLAN_TOOL:
        schema = inline_model_schema(schema_model)
        if not confirm_before_optimize:
            schema = {
                **schema,
                "properties": {k: v for k, v in schema["properties"].items() if k != "confirmed"},
            }
        tool.parameters = schema
    else:
        tool.parameters = inline_model_schema(schema_model)
    return tool


def build_tools(deps: ToolDeps) -> list[Tool]:
    confirm = deps.settings.confirm_before_optimize
    imports = deps.settings.import_tools_enabled
    tools = [
        _tool(
            make_optimize_tool(deps),
            name=OPTIMIZE_TOOL,
            title=descriptions.OPTIMIZE_TITLE,
            description=descriptions.optimize_description(confirm_before_optimize=confirm),
            schema_model=OptimizeInput,
            read_only=False,
            confirm_before_optimize=confirm,
        ),
        _tool(
            make_get_result_tool(deps),
            name=GET_RESULT_TOOL,
            title=descriptions.GET_RESULT_TITLE,
            description=descriptions.GET_RESULT_DESCRIPTION,
            schema_model=GetResultInput,
            read_only=True,
        ),
        # Plans exist for every account, so reading and rerunning them is published whether or not the
        # import tools are.
        _tool(
            make_list_plans_tool(deps),
            name=LIST_PLANS_TOOL,
            title=descriptions.LIST_PLANS_TITLE,
            description=descriptions.LIST_PLANS_DESCRIPTION,
            schema_model=ListPlansInput,
            read_only=True,
        ),
        _tool(
            make_optimize_plan_tool(deps),
            name=OPTIMIZE_PLAN_TOOL,
            title=descriptions.OPTIMIZE_PLAN_TITLE,
            description=descriptions.optimize_plan_description(
                confirm_before_optimize=confirm, import_tools=imports
            ),
            schema_model=OptimizePlanInput,
            read_only=False,
            confirm_before_optimize=confirm,
        ),
    ]
    if imports:
        tools.extend(_import_tools(deps))
    tools += [
        _tool(
            make_geocode_tool(deps),
            name=GEOCODE_TOOL,
            title=descriptions.GEOCODE_TITLE,
            description=descriptions.GEOCODE_DESCRIPTION,
            schema_model=GeocodeInput,
            read_only=False,
        ),
        _tool(
            make_get_geocode_tool(deps),
            name=GET_GEOCODE_TOOL,
            title=descriptions.GET_GEOCODE_TITLE,
            description=descriptions.GET_GEOCODE_DESCRIPTION,
            schema_model=GetGeocodeInput,
            read_only=True,
        ),
        _tool(
            make_get_account_tool(deps),
            name=GET_ACCOUNT_TOOL,
            title=descriptions.GET_ACCOUNT_TITLE,
            description=descriptions.GET_ACCOUNT_DESCRIPTION,
            schema_model=GetAccountInput,
            read_only=True,
        ),
        _tool(
            make_list_fleet_tool(deps),
            name=LIST_FLEET_TOOL,
            title=descriptions.LIST_FLEET_TITLE,
            description=descriptions.LIST_FLEET_DESCRIPTION,
            schema_model=ListFleetInput,
            read_only=True,
        ),
        _tool(
            make_list_automations_tool(deps),
            name=LIST_AUTOMATIONS_TOOL,
            title=descriptions.LIST_AUTOMATIONS_TITLE,
            description=descriptions.LIST_AUTOMATIONS_DESCRIPTION,
            schema_model=ListAutomationsInput,
            read_only=True,
        ),
        _tool(
            make_create_automation_tool(deps),
            name=CREATE_AUTOMATION_TOOL,
            title=descriptions.CREATE_AUTOMATION_TITLE,
            description=descriptions.CREATE_AUTOMATION_DESCRIPTION,
            schema_model=CreateAutomationInput,
            read_only=False,
            # The same operation_id returns the same rule: retrying is safe and writes nothing new.
            idempotent=True,
        ),
    ]
    if deps.settings.map_shares_enabled:
        from vepathos_mcp.tools.maps import DESCRIPTION, CreateMapInput, make_map_tool

        tools.append(
            _tool(
                make_map_tool(deps),
                name="create_optimization_map",
                title="Create route map",
                description=DESCRIPTION,
                schema_model=CreateMapInput,
                read_only=False,
            )
        )
    return tools


def _import_tools(deps: ToolDeps) -> list[Tool]:
    """File import. Published only with MCP_IMPORT_TOOLS_ENABLED."""

    return [
        _tool(
            make_import_file_tool(deps),
            name=IMPORT_FILE_TOOL,
            title=descriptions.IMPORT_FILE_TITLE,
            description=descriptions.IMPORT_FILE_DESCRIPTION,
            schema_model=ImportFileInput,
            read_only=False,
            # Each call starts a new import (and a new plan): a client must not retry it on its own.
            idempotent=False,
        ),
        _tool(
            make_import_text_tool(deps),
            name=IMPORT_TEXT_TOOL,
            title=descriptions.IMPORT_TEXT_TITLE,
            description=descriptions.IMPORT_TEXT_DESCRIPTION,
            schema_model=ImportTextInput,
            read_only=False,
            idempotent=False,
        ),
        _tool(
            make_get_import_tool(deps),
            name=GET_IMPORT_TOOL,
            title=descriptions.GET_IMPORT_TITLE,
            description=descriptions.GET_IMPORT_DESCRIPTION,
            schema_model=GetImportInput,
            read_only=True,
        ),
        _tool(
            make_update_mapping_tool(deps),
            name=UPDATE_MAPPING_TOOL,
            title=descriptions.UPDATE_MAPPING_TITLE,
            description=descriptions.UPDATE_MAPPING_DESCRIPTION,
            schema_model=UpdateMappingInput,
            read_only=False,
        ),
        _tool(
            make_list_datasets_tool(deps),
            name=LIST_DATASETS_TOOL,
            title=descriptions.LIST_DATASETS_TITLE,
            description=descriptions.LIST_DATASETS_DESCRIPTION,
            schema_model=ListDatasetsInput,
            read_only=True,
        ),
    ]

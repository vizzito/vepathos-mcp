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
    OptimizeRoutesInput,
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
from vepathos_mcp.tools.catalog_admin import (
    MANAGE_CATALOG_TOOL,
    ManageCatalogInput,
    make_manage_catalog_tool,
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
    IMPORT_TOOL,
    LIST_DATASETS_TOOL,
    UPDATE_MAPPING_TOOL,
    GetImportInput,
    ImportDeliveriesInput,
    ListDatasetsInput,
    UpdateMappingInput,
    make_get_import_tool,
    make_import_deliveries_tool,
    make_list_datasets_tool,
    make_update_mapping_tool,
)
from vepathos_mcp.tools.optimize import TOOL_NAME as OPTIMIZE_TOOL
from vepathos_mcp.tools.optimize import make_optimize_routes_tool
from vepathos_mcp.tools.plans import TOOL_NAME as LIST_PLANS_TOOL
from vepathos_mcp.tools.plans import ListPlansInput, make_list_plans_tool
from vepathos_mcp.tools.results import TOOL_NAME as GET_RESULT_TOOL
from vepathos_mcp.tools.results import make_get_result_tool
from vepathos_mcp.tools.runtime import ToolDeps


def optimize_schema(*, confirm_before_optimize: bool) -> dict[str, Any]:
    """The published optimize schema. Without the gate, `confirmed` changes nothing, so it is not
    advertised; the model still accepts it, because clients cache schemas and keep sending it."""

    schema = inline_model_schema(OptimizeRoutesInput)
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
    destructive: bool = False,
    open_world: bool = False,
) -> Tool:
    tool = Tool.from_function(
        fn,
        name=name,
        title=title,
        description=description,
        annotations=ToolAnnotations(
            title=title,
            read_only_hint=read_only,
            destructive_hint=destructive,
            idempotent_hint=idempotent,
            open_world_hint=open_world,
        ),
        structured_output=True,
    )
    if confirm_before_optimize is not None and name == OPTIMIZE_TOOL:
        tool.parameters = optimize_schema(confirm_before_optimize=confirm_before_optimize)
    else:
        tool.parameters = inline_model_schema(schema_model)
    return tool


def build_tools(deps: ToolDeps) -> list[Tool]:
    confirm = deps.settings.confirm_before_optimize
    imports = deps.settings.import_tools_enabled
    tools = [
        _tool(
            make_optimize_routes_tool(deps),
            name=OPTIMIZE_TOOL,
            title=descriptions.OPTIMIZE_TITLE,
            description=descriptions.optimize_description(
                confirm_before_optimize=confirm, import_tools=imports
            ),
            schema_model=OptimizeRoutesInput,
            read_only=False,
            # A run in a full plan library replaces the oldest plan (plan_replaced).
            destructive=True,
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
        # Plans exist for every account, so reading them is published whether or not the import tools are.
        _tool(
            make_list_plans_tool(deps),
            name=LIST_PLANS_TOOL,
            title=descriptions.LIST_PLANS_TITLE,
            description=descriptions.LIST_PLANS_DESCRIPTION,
            schema_model=ListPlansInput,
            read_only=True,
        ),
    ]
    if imports:
        tools.extend(_import_tools(deps))
    tools += [
        _tool(
            make_geocode_tool(deps),
            name=GEOCODE_TOOL,
            title=descriptions.GEOCODE_TITLE,
            description=descriptions.geocode_description(import_tools=imports),
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
    ]
    if deps.settings.catalog_write_tools_enabled:
        # Master data, behind its own flag: deploying the code publishes nothing new.
        tools += [
            _tool(
                make_manage_catalog_tool(deps),
                name=MANAGE_CATALOG_TOOL,
                title=descriptions.MANAGE_CATALOG_TITLE,
                description=descriptions.MANAGE_CATALOG_DESCRIPTION,
                schema_model=ManageCatalogInput,
                read_only=False,
                # An update overwrites what the account had saved.
                destructive=True,
            ),
        ]
    tools += [
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
            make_import_deliveries_tool(deps),
            name=IMPORT_TOOL,
            title=descriptions.IMPORT_TITLE,
            description=descriptions.IMPORT_DESCRIPTION,
            schema_model=ImportDeliveriesInput,
            read_only=False,
            # It downloads whatever public URL the caller names: the one tool that reaches outside Vepathos.
            open_world=True,
            # Each call starts a new import (and a new plan): a client must not retry it on its own.
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

"""Create a public map through Core; never construct or store bearer links locally."""

from typing import Annotated, Any, Literal
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from mcp.server.mcpserver.context import Context
from mcp_types import CallToolResult
from pydantic import Field, ValidationError

from vepathos_mcp.schemas.inputs import (
    OPTIMIZATION_ID_PATTERN,
    StrictModel,
    validation_error_to_domain,
)
from vepathos_mcp.schemas.maps import MapCreated, MapOutput
from vepathos_mcp.tools.rendering import success_result
from vepathos_mcp.tools.runtime import RequestIdentity, ToolDeps, instrumented

MapLanguage = Literal["en", "es", "pt"]
MAP_LANGUAGES: dict[str, MapLanguage] = {"en": "en", "es": "es", "pt": "pt"}
# BCP 47-ish: "es", "es-AR", "pt_BR". Anything else is rejected before calling Core.
LANGUAGE_TAG_PATTERN = r"^[A-Za-z]{2,3}([-_][A-Za-z0-9]{1,8}){0,3}$"


class CreateMapInput(StrictModel):
    optimization_id: str = Field(
        pattern=OPTIMIZATION_ID_PATTERN,
        description="The finished optimization to share, from optimize_routes.",
    )
    language: str | None = Field(
        None,
        pattern=LANGUAGE_TAG_PATTERN,
        description=(
            "Language of the conversation, e.g. es, es-AR, pt-BR, en. The map page opens in it "
            "when supported (en, es, pt); otherwise it follows the viewer's browser."
        ),
    )


def map_language(tag: str | None) -> MapLanguage | None:
    """`"es-AR"` -> `"es"`; unsupported or missing -> None."""

    if not tag:
        return None
    return MAP_LANGUAGES.get(tag.replace("_", "-").split("-", 1)[0].lower())


def with_map_language(created: MapCreated, language: MapLanguage | None) -> MapCreated:
    """Presentation only: `?lang=` rides on the link; the token and Core's share are unchanged."""

    if language is None:
        return created
    parts = urlsplit(str(created.map_url))
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if k != "lang"]
    query.append(("lang", language))
    url = urlunsplit(parts._replace(query=urlencode(query)))
    return MapCreated.model_validate(
        {**created.model_dump(mode="json"), "map_url": url, "language": language}
    )


DESCRIPTION = (
    "Create a temporary public link to the map of a completed optimization owned by the connected "
    "account. A result reaches the user two ways, and the user chooses: the plan in their Vepathos "
    "account (account_url, sign-in required) or this link. Offer both; create the link only when the "
    "user picks it. Anyone with the link can view the delivery locations without signing in: tell the "
    "user this and that it expires 48 hours after creation. Repeating the call returns the same link "
    "without extending it; after expiry the page says so. Does not optimize again or charge stops. "
    "Pass language with the conversation's language so the page, share message and PDF open in it "
    "(en, es, pt); the same link works in any language."
)


def make_map_tool(deps: ToolDeps) -> Any:
    async def create_optimization_map(ctx: Context) -> Annotated[CallToolResult, MapOutput]:
        async def handle(identity: RequestIdentity, arguments: dict[str, Any]) -> CallToolResult:
            try:
                inp = CreateMapInput.model_validate(arguments)
            except ValidationError as exc:
                raise validation_error_to_domain(exc) from None
            deps.rate_limiter.check(identity.subject, "calls")
            result = await deps.core.create_map(identity.call, inp.optimization_id)
            # Echo optimization_id so tool_call logs are not null (Core map body omits it).
            echoed = MapCreated.model_validate(
                {**result.model_dump(mode="json"), "optimization_id": inp.optimization_id}
            )
            return success_result(with_map_language(echoed, map_language(inp.language)))

        return await instrumented("create_optimization_map", ctx, deps, handle)

    return create_optimization_map

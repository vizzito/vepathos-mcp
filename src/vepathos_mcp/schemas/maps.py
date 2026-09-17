"""Core map creation response, shared with the MCP output contract."""

from typing import Any, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, HttpUrl, model_validator


class MapCreated(BaseModel):
    model_config = ConfigDict(extra="forbid")

    map_url: HttpUrl
    expires_at: AwareDatetime
    access: Literal["anyone_with_link"]
    notice: str = "Anyone with this link can view the delivery locations until expires_at."
    # Echoed for telemetry: tool_call logs read optimization_id from structured output.
    optimization_id: str | None = Field(
        None, description="Optimization this map belongs to (same id passed to the tool)."
    )
    language: Literal["en", "es", "pt"] | None = Field(
        None,
        description="Language the map page opens in. Null: the viewer's browser language.",
    )


class MapOutput(BaseModel):
    """Object-root MCP output supporting either a successful share or a structured error."""

    model_config = ConfigDict(extra="forbid")

    map_url: HttpUrl | None = None
    expires_at: AwareDatetime | None = None
    access: Literal["anyone_with_link"] | None = None
    notice: str | None = None
    optimization_id: str | None = None
    language: Literal["en", "es", "pt"] | None = None
    error: dict[str, Any] | None = None

    @model_validator(mode="after")
    def validate_variant(self) -> "MapOutput":
        success = self.map_url is not None and self.expires_at is not None and self.access is not None
        if success == (self.error is not None):
            raise ValueError("output must contain either a complete map share or an error")
        return self

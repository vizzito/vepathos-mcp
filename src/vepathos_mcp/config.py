"""Runtime configuration, read from environment variables.

Every commercial value (plans, limits, trial rules) lives in Vepathos Core. This module only
configures the adapter: where Core is, how callers authenticate, and operational budgets.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import AliasChoices, Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class AuthMode(StrEnum):
    """How callers of the MCP endpoint authenticate."""

    OAUTH = "oauth"
    """Production: OAuth access tokens issued by the Vepathos authorization server."""

    API_KEY = "api_key"
    """Developers / headless agents: a Vepathos credential with scope `mcp:optimize`."""

    SERVICE = "service"
    """Local development only: one static inbound token and one Vepathos credential from env."""


def _env(*names: str) -> AliasChoices:
    return AliasChoices(*names)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", populate_by_name=True)

    environment: Literal["development", "test", "production"] = Field(
        "development", validation_alias=_env("ENVIRONMENT")
    )
    log_level: str = Field("INFO", validation_alias=_env("LOG_LEVEL"))
    transport: Literal["http", "stdio"] = Field("http", validation_alias=_env("MCP_TRANSPORT"))

    # --- HTTP surface -------------------------------------------------------------------------
    host: str = Field("127.0.0.1", validation_alias=_env("MCP_HOST"))
    port: int = Field(8080, validation_alias=_env("MCP_PORT"))
    public_url: str = Field("http://localhost:8080", validation_alias=_env("MCP_PUBLIC_URL"))
    mcp_path: str = Field("/mcp", validation_alias=_env("MCP_PATH"))
    allowed_hosts: str = Field("localhost:*,127.0.0.1:*", validation_alias=_env("MCP_ALLOWED_HOSTS"))
    allowed_origins: str = Field(
        "http://localhost:*,http://127.0.0.1:*", validation_alias=_env("MCP_ALLOWED_ORIGINS")
    )
    max_request_body_bytes: int = Field(
        8 * 1024 * 1024, ge=64 * 1024, validation_alias=_env("MCP_MAX_REQUEST_BODY_BYTES")
    )
    metrics_bearer_token: SecretStr | None = Field(None, validation_alias=_env("METRICS_BEARER_TOKEN"))

    # --- Vepathos Core ----------------------------------------------------------------------
    core_base_url: str = Field("http://localhost:3000", validation_alias=_env("VEPATHOS_API_BASE_URL"))
    core_service_key: SecretStr = Field(SecretStr(""), validation_alias=_env("VEPATHOS_MCP_SERVICE_KEY"))
    core_timeout_seconds: float = Field(20.0, gt=0, validation_alias=_env("VEPATHOS_API_TIMEOUT_SECONDS"))
    core_submit_timeout_seconds: float = Field(
        45.0, gt=0, validation_alias=_env("VEPATHOS_API_SUBMIT_TIMEOUT_SECONDS")
    )

    # --- Authentication ---------------------------------------------------------------------
    auth_modes_raw: str = Field("service", validation_alias=_env("AUTH_MODES"))
    oauth_issuer: str = Field("https://api.vepathos.com", validation_alias=_env("OAUTH_ISSUER"))
    oauth_jwks_url: str = Field("https://api.vepathos.com/api/jwks", validation_alias=_env("OAUTH_JWKS_URL"))
    oauth_scope: str = Field("optimize", validation_alias=_env("OAUTH_SCOPE"))
    service_credential: SecretStr | None = Field(None, validation_alias=_env("VEPATHOS_SERVICE_CREDENTIAL"))
    dev_bearer_token: SecretStr | None = Field(None, validation_alias=_env("MCP_DEV_BEARER_TOKEN"))
    account_hash_salt: SecretStr = Field(
        SecretStr("vepathos-mcp-dev-salt"), validation_alias=_env("ACCOUNT_HASH_SALT")
    )

    map_shares_enabled: bool = Field(False, validation_alias=_env("MCP_MAP_SHARES_ENABLED"))
    # Optimizing spends the account's stops, so it is confirmed by default. Turning this off makes
    # `confirmed` moot and lets an unattended integration optimize in one call.
    confirm_before_optimize: bool = Field(True, validation_alias=_env("MCP_CONFIRM_BEFORE_OPTIMIZE"))

    # --- Behaviour budgets ------------------------------------------------------------------
    optimize_inline_wait_seconds: float = Field(
        8.0, ge=0, le=25, validation_alias=_env("MCP_OPTIMIZE_INLINE_WAIT_SECONDS")
    )
    result_longpoll_seconds: float = Field(
        20.0, ge=0, le=25, validation_alias=_env("MCP_RESULT_LONGPOLL_SECONDS")
    )
    poll_interval_seconds: float = Field(2.0, gt=0, le=10, validation_alias=_env("MCP_POLL_INTERVAL_SECONDS"))
    rate_limit_optimize_per_minute: int = Field(
        10, ge=1, validation_alias=_env("MCP_RATE_LIMIT_OPTIMIZE_PER_MINUTE")
    )
    rate_limit_calls_per_minute: int = Field(
        120, ge=1, validation_alias=_env("MCP_RATE_LIMIT_CALLS_PER_MINUTE")
    )

    @field_validator("mcp_path")
    @classmethod
    def _path_starts_with_slash(cls, value: str) -> str:
        if not value.startswith("/"):
            raise ValueError("MCP_PATH must start with '/'")
        return value.rstrip("/") or "/"

    @property
    def auth_modes(self) -> tuple[AuthMode, ...]:
        modes: list[AuthMode] = []
        for part in self.auth_modes_raw.split(","):
            name = part.strip().lower()
            if name and AuthMode(name) not in modes:
                modes.append(AuthMode(name))
        return tuple(modes)

    @property
    def resource_url(self) -> str:
        """Canonical MCP resource URI (RFC 8707 / RFC 9728), e.g. https://mcp.vepathos.com/mcp."""
        return self.public_url.rstrip("/") + self.mcp_path

    @property
    def allowed_host_list(self) -> list[str]:
        return [h.strip() for h in self.allowed_hosts.split(",") if h.strip()]

    @property
    def allowed_origin_list(self) -> list[str]:
        return [o.strip() for o in self.allowed_origins.split(",") if o.strip()]

    @model_validator(mode="after")
    def _check_consistency(self) -> Settings:
        try:
            modes = self.auth_modes
        except ValueError as exc:  # unknown mode name
            raise ValueError(f"AUTH_MODES contains an unknown mode: {self.auth_modes_raw!r}") from exc
        if not modes:
            raise ValueError("AUTH_MODES must enable at least one mode")
        if self.transport == "stdio":
            # stdio: a single local user; the credential comes from the environment (MCP auth spec).
            if self.service_credential is None:
                raise ValueError("MCP_TRANSPORT=stdio requires VEPATHOS_SERVICE_CREDENTIAL")
            return self
        if AuthMode.SERVICE in modes and (self.service_credential is None or self.dev_bearer_token is None):
            raise ValueError(
                "AUTH_MODES=service requires VEPATHOS_SERVICE_CREDENTIAL and MCP_DEV_BEARER_TOKEN"
            )
        if self.environment == "production":
            if AuthMode.SERVICE in modes:
                raise ValueError("AUTH_MODES=service is for local development and is refused in production")
            for name, url in (
                ("VEPATHOS_API_BASE_URL", self.core_base_url),
                ("MCP_PUBLIC_URL", self.public_url),
                ("OAUTH_ISSUER", self.oauth_issuer),
                ("OAUTH_JWKS_URL", self.oauth_jwks_url),
            ):
                if not url.startswith("https://"):
                    raise ValueError(f"{name} must use https:// in production")
            if not self.core_service_key.get_secret_value():
                raise ValueError("VEPATHOS_MCP_SERVICE_KEY is required in production")
            if self.account_hash_salt.get_secret_value() == "vepathos-mcp-dev-salt":
                raise ValueError("ACCOUNT_HASH_SALT must be set in production")
        return self

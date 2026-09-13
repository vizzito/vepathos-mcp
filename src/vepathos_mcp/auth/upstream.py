"""Which user credential is forwarded to Vepathos Core for the current request."""

from __future__ import annotations

import hashlib

from mcp.server.auth.provider import AccessToken

from vepathos_mcp.auth.verifier import credential_kind
from vepathos_mcp.config import AuthMode, Settings
from vepathos_mcp.errors.codes import DomainError, ErrorCode


def upstream_authorization(settings: Settings, access_token: AccessToken | None) -> str:
    """`Authorization` header value for the Core channel (the user factor of the trust boundary)."""

    if settings.transport == "stdio" and settings.service_credential is not None:
        return f"Bearer {settings.service_credential.get_secret_value()}"
    if access_token is None:
        raise DomainError(
            ErrorCode.AUTHENTICATION_REQUIRED,
            "The Vepathos connection is not authorized.",
            suggestion="Connect the Vepathos connector and sign in.",
        )
    if credential_kind(access_token) == AuthMode.SERVICE.value:
        if settings.service_credential is None:  # guarded by Settings validation
            raise DomainError(
                ErrorCode.INTERNAL_ERROR, "The Vepathos MCP service is misconfigured.", retryable=False
            )
        return f"Bearer {settings.service_credential.get_secret_value()}"
    return f"Bearer {access_token.token}"


def subject_key(settings: Settings, access_token: AccessToken | None) -> str:
    """Stable, non-reversible key for rate limiting and logs (never the raw credential)."""

    if access_token is None:
        return "anonymous"
    basis = access_token.subject or access_token.token
    salt = settings.account_hash_salt.get_secret_value().encode("utf-8")
    return hashlib.sha256(salt + b":" + basis.encode("utf-8")).hexdigest()[:16]

"""Inbound bearer verification for the MCP endpoint.

- `oauth`: RS256 access tokens from the Vepathos authorization server, verified against JWKS with
  issuer, audience (this server's resource URI), expiry and scope.
- `api_key`: developer credentials `vpt_<id>:vpt_sk_<env>_<secret>` or
  `vpt_mcp_<id>:vpt_sk_<env>_<secret>`. Only the shape is checked here;
  Vepathos Core verifies the secret and scope on every call, so a wrong key fails there.
- `service`: one static development token (constant-time comparison).

The verified token is forwarded to the Core channel as the user factor; Core verifies it again and
derives the account from it. See docs/architecture.md, "Trust boundary".
"""

from __future__ import annotations

import hmac
import re
from typing import Any

import anyio
import jwt
from mcp.server.auth.provider import AccessToken, TokenVerifier

from vepathos_mcp.config import AuthMode, Settings

API_KEY_PATTERN = re.compile(r"^vpt_(?:mcp_)?[0-9a-f]{24}:vpt_sk_(test|live)_[0-9a-f]{48}$")
JWT_SHAPE = re.compile(r"^[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+$")

CREDENTIAL_CLAIM = "vepathos_credential"


class JwksTokenVerifier:
    """Verifies Vepathos OAuth access tokens issued for this MCP resource."""

    def __init__(
        self,
        *,
        issuer: str,
        audience: str,
        jwks_url: str,
        jwk_client: jwt.PyJWKClient | None = None,
        leeway_seconds: int = 30,
    ) -> None:
        self._issuer = issuer
        self._audience = audience
        self._leeway = leeway_seconds
        self._jwks = jwk_client or jwt.PyJWKClient(jwks_url, cache_keys=True, lifespan=300, timeout=5)

    async def verify(self, token: str) -> AccessToken | None:
        try:
            signing_key = await anyio.to_thread.run_sync(self._jwks.get_signing_key_from_jwt, token)
            claims: dict[str, Any] = jwt.decode(
                token,
                signing_key.key,
                algorithms=["RS256"],
                audience=self._audience,
                issuer=self._issuer,
                leeway=self._leeway,
                options={"require": ["exp", "iat", "iss", "aud", "sub"]},
            )
        except (jwt.PyJWTError, ValueError):
            return None

        scope = claims.get("scope")
        scopes = scope.split() if isinstance(scope, str) else []
        client_id = claims.get("client_id") or claims.get("azp")
        exp = claims.get("exp")
        return AccessToken(
            token=token,
            client_id=str(client_id) if client_id else "unknown",
            scopes=scopes,
            expires_at=int(exp) if isinstance(exp, int | float) else None,
            resource=self._audience,
            subject=str(claims["sub"]),
            claims={
                "iss": claims.get("iss"),
                "grant_id": claims.get("grant_id"),
                CREDENTIAL_CLAIM: AuthMode.OAUTH.value,
            },
        )


class CompositeTokenVerifier(TokenVerifier):
    """Dispatches on the credential shape to the enabled authentication modes."""

    def __init__(self, settings: Settings, jwks_verifier: JwksTokenVerifier | None = None) -> None:
        self._modes = settings.auth_modes
        self._required_scope = settings.oauth_scope
        self._dev_token = settings.dev_bearer_token.get_secret_value() if settings.dev_bearer_token else None
        self._jwks = jwks_verifier
        if AuthMode.OAUTH in self._modes and self._jwks is None:
            self._jwks = JwksTokenVerifier(
                issuer=settings.oauth_issuer,
                audience=settings.resource_url,
                jwks_url=settings.oauth_jwks_url,
            )

    async def verify_token(self, token: str) -> AccessToken | None:
        token = token.strip()
        if not token or len(token) > 8192:
            return None

        if (
            AuthMode.SERVICE in self._modes
            and self._dev_token
            and hmac.compare_digest(token, self._dev_token)
        ):
            return AccessToken(
                token=token,
                client_id="service",
                scopes=[self._required_scope],
                claims={CREDENTIAL_CLAIM: AuthMode.SERVICE.value},
            )

        if AuthMode.API_KEY in self._modes and API_KEY_PATTERN.match(token):
            client_id = token.split(":", 1)[0]
            return AccessToken(
                token=token,
                client_id=client_id,
                # Scope `mcp:optimize` is enforced by Core; the local scope only satisfies the MCP gate.
                scopes=[self._required_scope],
                claims={CREDENTIAL_CLAIM: AuthMode.API_KEY.value},
            )

        if AuthMode.OAUTH in self._modes and self._jwks is not None and JWT_SHAPE.match(token):
            return await self._jwks.verify(token)

        return None


def credential_kind(access_token: AccessToken | None) -> str | None:
    if access_token is None or not access_token.claims:
        return None
    value = access_token.claims.get(CREDENTIAL_CLAIM)
    return str(value) if value is not None else None

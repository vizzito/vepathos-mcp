from __future__ import annotations

import json
import logging
import time

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from tests.conftest import API_KEY, DEV_TOKEN, make_settings
from vepathos_mcp.auth.upstream import subject_key, upstream_authorization
from vepathos_mcp.auth.verifier import CompositeTokenVerifier, JwksTokenVerifier, credential_kind
from vepathos_mcp.clients.breaker import CircuitBreaker
from vepathos_mcp.errors.codes import DomainError, ErrorCode
from vepathos_mcp.rate_limit import RateLimiter
from vepathos_mcp.telemetry.client_detect import detect_client
from vepathos_mcp.telemetry.logging import JsonFormatter, redact, redact_text

RESOURCE = "https://mcp.vepathos.com/mcp"
ISSUER = "https://api.vepathos.com"


class StaticJwkClient:
    def __init__(self, key: object) -> None:
        self._key = key

    def get_signing_key_from_jwt(self, _: str) -> object:
        class Signing:
            key = self._key

        return Signing()


@pytest.fixture(scope="module")
def rsa_key() -> rsa.RSAPrivateKey:
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def make_token(key: rsa.RSAPrivateKey, **claims: object) -> str:
    now = int(time.time())
    payload = {
        "iss": ISSUER,
        "aud": RESOURCE,
        "sub": "user_123",
        "client_id": "https://claude.ai/oauth/claude-code-client-metadata",
        "scope": "optimize",
        "iat": now,
        "exp": now + 3600,
        **claims,
    }
    return jwt.encode(payload, key, algorithm="RS256")


def oauth_verifier(key: rsa.RSAPrivateKey) -> CompositeTokenVerifier:
    settings = make_settings(AUTH_MODES="oauth", MCP_PUBLIC_URL="https://mcp.vepathos.com")
    jwks = JwksTokenVerifier(
        issuer=ISSUER, audience=RESOURCE, jwks_url="unused", jwk_client=StaticJwkClient(key.public_key())
    )  # type: ignore[arg-type]
    return CompositeTokenVerifier(settings, jwks_verifier=jwks)


async def test_oauth_token_accepted_with_claims(rsa_key: rsa.RSAPrivateKey) -> None:
    token = await oauth_verifier(rsa_key).verify_token(make_token(rsa_key))
    assert token is not None
    assert token.subject == "user_123" and token.scopes == ["optimize"]
    assert credential_kind(token) == "oauth"


@pytest.mark.parametrize(
    "claims",
    [
        {"aud": "https://api.vepathos.com"},  # token for another resource
        {"iss": "https://evil.example"},
        {"exp": int(time.time()) - 3600},
    ],
)
async def test_oauth_token_rejected(rsa_key: rsa.RSAPrivateKey, claims: dict[str, object]) -> None:
    assert await oauth_verifier(rsa_key).verify_token(make_token(rsa_key, **claims)) is None


async def test_oauth_token_signed_by_other_key_rejected(rsa_key: rsa.RSAPrivateKey) -> None:
    other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    assert await oauth_verifier(rsa_key).verify_token(make_token(other)) is None


async def test_hs256_and_none_algorithms_rejected(rsa_key: rsa.RSAPrivateKey) -> None:
    now = int(time.time())
    claims = {"iss": ISSUER, "aud": RESOURCE, "sub": "u", "iat": now, "exp": now + 60}
    forged = jwt.encode(claims, "x" * 32, algorithm="HS256")
    assert await oauth_verifier(rsa_key).verify_token(forged) is None


async def test_api_key_and_service_modes() -> None:
    verifier = CompositeTokenVerifier(make_settings())
    api = await verifier.verify_token(API_KEY)
    assert api is not None and credential_kind(api) == "api_key" and api.client_id.startswith("vpt_")
    mcp_key = "vpt_mcp_" + "a" * 24 + ":vpt_sk_test_" + "b" * 48
    mcp = await verifier.verify_token(mcp_key)
    assert mcp is not None and mcp.client_id.startswith("vpt_mcp_")
    service = await verifier.verify_token(DEV_TOKEN)
    assert service is not None and credential_kind(service) == "service"
    assert await verifier.verify_token("vpt_short:secret") is None
    assert await verifier.verify_token("wrong-dev-token") is None


async def test_modes_are_isolated() -> None:
    verifier = CompositeTokenVerifier(make_settings(AUTH_MODES="api_key"))
    assert await verifier.verify_token(DEV_TOKEN) is None


async def test_upstream_credential_selection() -> None:
    settings = make_settings()
    verifier = CompositeTokenVerifier(settings)
    service = await verifier.verify_token(DEV_TOKEN)
    api = await verifier.verify_token(API_KEY)
    assert upstream_authorization(settings, service) == f"Bearer {API_KEY}"  # service credential from env
    assert upstream_authorization(settings, api) == f"Bearer {API_KEY}"  # caller's own credential
    with pytest.raises(DomainError) as info:
        upstream_authorization(settings, None)
    assert info.value.code is ErrorCode.AUTHENTICATION_REQUIRED
    assert API_KEY not in subject_key(settings, api)


def test_client_detection() -> None:
    assert detect_client("https://claude.ai/oauth/claude-code-client-metadata", None) == "claude-code"
    assert detect_client("https://claude.ai/oauth/mcp-oauth-client-metadata", None) == "claude"
    assert detect_client(None, "Cursor") == "cursor"
    assert detect_client(None, "Visual Studio Code") == "vscode"
    assert detect_client("https://evil.example/cimd.json", "claude-code") == "other"  # CIMD host wins
    assert detect_client(None, None) == "other"


def test_redaction() -> None:
    assert "vpt_sk_test_" not in redact_text(f"credential {API_KEY}")
    assert redact_text("Authorization: Bearer abc.def.ghi") == "Authorization: Bearer [redacted]"
    assert redact({"authorization": "x", "nested": {"api_key": "y", "ok": 1}}) == {
        "authorization": "[redacted]",
        "nested": {"api_key": "[redacted]", "ok": 1},
    }
    record = logging.LogRecord("vepathos_mcp", logging.INFO, __file__, 1, "tool_call", None, None)
    record.fields = {"token": "secret", "response_tokens": 42, "tool": "optimize_delivery_routes"}  # type: ignore[attr-defined]
    line = json.loads(JsonFormatter().format(record))
    assert line["token"] == "[redacted]" and line["tool"] == "optimize_delivery_routes"
    assert line["response_tokens"] == 42


def test_rate_limiter_window() -> None:
    now = [0.0]
    limiter = RateLimiter({"optimize": 2}, clock=lambda: now[0])
    limiter.check("s", "optimize")
    limiter.check("s", "optimize")
    with pytest.raises(DomainError) as info:
        limiter.check("s", "optimize")
    assert info.value.code is ErrorCode.RATE_LIMITED and info.value.retry_after_seconds == 60
    limiter.check("other", "optimize")
    now[0] = 61.0
    limiter.check("s", "optimize")


def test_circuit_breaker() -> None:
    now = [0.0]
    breaker = CircuitBreaker(failure_threshold=2, reset_after_seconds=10, clock=lambda: now[0])
    breaker.record_failure()
    assert not breaker.is_open
    breaker.record_failure()
    assert breaker.is_open and breaker.retry_after_seconds == 10
    now[0] = 10.5
    assert not breaker.is_open  # half-open
    breaker.record_success()
    assert not breaker.is_open

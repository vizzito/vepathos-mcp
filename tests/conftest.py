from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from typing import Any

import httpx
import pytest
from devtools.fake_core.app import FakeCoreState, create_fake_core

from vepathos_mcp.clients.vepathos_api import VepathosApiClient
from vepathos_mcp.config import Settings

SERVICE_KEY = "test-service-key"
DEV_TOKEN = "dev-bearer-token-1234567890"
API_KEY = "vpt_" + "a" * 24 + ":vpt_sk_test_" + "b" * 48


class FakeClock:
    """Deterministic clock shared by the fake Core and the tools' bounded waits."""

    def __init__(self, start: float = 1_780_000_000.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.now += seconds


def make_settings(**overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "_env_file": None,
        "ENVIRONMENT": "test",
        "VEPATHOS_API_BASE_URL": "http://core.test",
        "VEPATHOS_MCP_SERVICE_KEY": SERVICE_KEY,
        "AUTH_MODES": "service,api_key",
        "VEPATHOS_SERVICE_CREDENTIAL": API_KEY,
        "MCP_DEV_BEARER_TOKEN": DEV_TOKEN,
        "MCP_POLL_INTERVAL_SECONDS": 1,
        "MCP_OPTIMIZE_INLINE_WAIT_SECONDS": 0,
        "MCP_RESULT_LONGPOLL_SECONDS": 0,
    }
    values.update(overrides)
    return Settings(**values)


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def core_state(clock: FakeClock) -> FakeCoreState:
    return FakeCoreState(service_key=SERVICE_KEY, run_seconds=6.0, clock=clock)


@pytest.fixture
def core_client_factory(core_state: FakeCoreState) -> Callable[..., VepathosApiClient]:
    def factory(**kwargs: Any) -> VepathosApiClient:
        async def no_sleep(_: float) -> None:
            return None

        return VepathosApiClient(
            "http://core.test",
            SERVICE_KEY,
            transport=httpx.ASGITransport(app=create_fake_core(core_state)),
            sleep=no_sleep,
            **kwargs,
        )

    return factory


@pytest.fixture
async def core_client(
    core_client_factory: Callable[..., VepathosApiClient],
) -> AsyncIterator[VepathosApiClient]:
    client = core_client_factory()
    try:
        yield client
    finally:
        await client.aclose()


def sample_arguments(stops: int = 12, **extra: Any) -> dict[str, Any]:
    return {
        "depot": {"latitude": -34.6037, "longitude": -58.3816},
        "vehicles": [{"vehicle_id": "van", "count": 3}],
        "stops": [
            {"stop_id": f"ORD-{i:05d}", "latitude": -34.60 - i * 0.001, "longitude": -58.38 - i * 0.001}
            for i in range(stops)
        ],
        "schedule": {"date": "2026-09-14", "time_zone": "America/Argentina/Buenos_Aires"},
        # Confirmed by default: these fixtures exercise the optimization path. The confirmation
        # gate has its own tests, which pass confirmed=False explicitly.
        "confirmed": True,
        **extra,
    }

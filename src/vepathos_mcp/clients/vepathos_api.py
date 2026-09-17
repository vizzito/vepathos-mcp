"""VepathosApiClient — the only module that knows Vepathos Core URLs.

Tools call business operations (create a job, read its status, read results). This client owns
authentication headers, timeouts, safe retries, the circuit breaker and error normalization, so the
Core API can evolve without touching the MCP contract.

Retry policy:
- Reads are retried on transport errors and 502/503/504 with backoff.
- Job creation is retried under the same `Idempotency-Key`, which Core deduplicates per account, so a
  retry can never create a second job or charge twice.
"""

from __future__ import annotations

import random
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, TypeVar
from urllib.parse import quote

import anyio
import httpx
from pydantic import BaseModel, ValidationError

from vepathos_mcp import __version__
from vepathos_mcp.clients.breaker import CircuitBreaker
from vepathos_mcp.clients.core_models import (
    CoreAccount,
    CoreCatalog,
    CoreDataset,
    CoreGeocodeCreated,
    CoreGeocodeResult,
    CoreJobCreated,
    CoreJobResult,
    CoreJobStatusResponse,
)
from vepathos_mcp.errors.codes import DomainError, ErrorCode
from vepathos_mcp.errors.mapping import from_core_error
from vepathos_mcp.schemas.maps import MapCreated

BASE_PATH = "/api/mcp/v1"
RETRYABLE_STATUS = frozenset({502, 503, 504})
MAX_RETRY_AFTER_SECONDS = 5.0

ModelT = TypeVar("ModelT", bound=BaseModel)
Observer = Callable[[str, float, int | None], None]


@dataclass(frozen=True)
class CallContext:
    """Per-call data forwarded to Core. `authorization` is the caller's own credential."""

    authorization: str
    client_label: str | None = None
    traceparent: str | None = None
    request_id: str | None = None


class VepathosApiClient:
    def __init__(
        self,
        base_url: str,
        service_key: str,
        *,
        timeout_seconds: float = 20.0,
        submit_timeout_seconds: float = 45.0,
        max_attempts: int = 3,
        transport: httpx.AsyncBaseTransport | None = None,
        breaker: CircuitBreaker | None = None,
        sleep: Callable[[float], Awaitable[None]] = anyio.sleep,
        observer: Observer | None = None,
    ) -> None:
        self._service_key = service_key
        self._timeout = timeout_seconds
        self._submit_timeout = submit_timeout_seconds
        self._max_attempts = max(1, max_attempts)
        self._breaker = breaker or CircuitBreaker()
        self._sleep = sleep
        self._observer = observer
        self._http = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            transport=transport,
            timeout=httpx.Timeout(timeout_seconds, connect=5.0),
            headers={"Accept": "application/json", "User-Agent": f"vepathos-mcp/{__version__}"},
            follow_redirects=False,
        )

    async def aclose(self) -> None:
        await self._http.aclose()

    # --- Business operations -------------------------------------------------------------------

    async def create_job(
        self, call: CallContext, body: dict[str, Any], idempotency_key: str
    ) -> CoreJobCreated:
        data = await self._request(
            "POST",
            f"{BASE_PATH}/optimization/jobs",
            call,
            operation="submit",
            json_body=body,
            idempotency_key=idempotency_key,
            request_timeout=self._submit_timeout,
        )
        return self._parse(CoreJobCreated, data)

    async def get_job(self, call: CallContext, job_id: str) -> CoreJobStatusResponse:
        data = await self._request(
            "GET", f"{BASE_PATH}/optimization/jobs/{_segment(job_id)}", call, operation="status"
        )
        return self._parse(CoreJobStatusResponse, data)

    async def get_result(
        self,
        call: CallContext,
        job_id: str,
        *,
        view: str,
        offset: int,
        limit: int | None,
        route_id: str | None = None,
    ) -> CoreJobResult:
        params: dict[str, Any] = {"view": view, "offset": offset}
        if limit is not None:
            params["limit"] = limit
        if route_id is not None:
            params["route_id"] = route_id
        data = await self._request(
            "GET",
            f"{BASE_PATH}/optimization/jobs/{_segment(job_id)}/result",
            call,
            operation="result",
            params=params,
        )
        return self._parse(CoreJobResult, data)

    async def create_geocode(
        self, call: CallContext, body: dict[str, Any], idempotency_key: str
    ) -> CoreGeocodeCreated:
        data = await self._request(
            "POST",
            f"{BASE_PATH}/geocode",
            call,
            operation="geocode_submit",
            json_body=body,
            idempotency_key=idempotency_key,
            request_timeout=self._submit_timeout,
        )
        return self._parse(CoreGeocodeCreated, data)

    async def get_geocode(self, call: CallContext, job_id: str) -> CoreGeocodeResult:
        data = await self._request(
            "GET", f"{BASE_PATH}/geocode/{_segment(job_id)}", call, operation="geocode_status"
        )
        return self._parse(CoreGeocodeResult, data)

    async def create_map(self, call: CallContext, job_id: str) -> MapCreated:
        # Core deduplicates permanently per account/job; retries never extend the TTL.
        data = await self._request(
            "POST",
            f"{BASE_PATH}/optimization/jobs/{_segment(job_id)}/map",
            call,
            operation="map_create",
            json_body={},
            idempotency_key=f"map:{job_id}",
        )
        return self._parse(MapCreated, data)

    async def get_account(self, call: CallContext) -> CoreAccount:
        """Who the caller's credential belongs to, plus plan limits and period usage."""

        data = await self._request(
            "GET",
            f"{BASE_PATH}/account",
            call,
            operation="account",
            absent_error=DomainError(
                ErrorCode.INTERNAL_ERROR,
                "This Vepathos deployment does not report account information yet.",
                suggestion=(
                    "Skip get_account. To see which Vepathos account is connected, the user can open "
                    "Connected apps in the Vepathos dashboard."
                ),
                retryable=False,
            ),
        )
        return self._parse(CoreAccount, data)

    async def get_catalog(self, call: CallContext) -> CoreCatalog:
        """The account's own fleets and vehicles, already in kilograms and cubic metres."""

        data = await self._request(
            "GET",
            f"{BASE_PATH}/catalog",
            call,
            operation="catalog",
            absent_error=DomainError(
                ErrorCode.INTERNAL_ERROR,
                "This Vepathos deployment does not expose the vehicle catalog yet.",
                suggestion=(
                    "Skip list_fleet and ask the user to describe the fleet, then pass those vehicles "
                    "to optimize_delivery_routes."
                ),
                retryable=False,
            ),
        )
        return self._parse(CoreCatalog, data)

    async def create_import(
        self, call: CallContext, body: dict[str, Any], idempotency_key: str | None = None
    ) -> dict[str, Any]:
        data = await self._request(
            "POST",
            f"{BASE_PATH}/imports",
            call,
            operation="import_submit",
            json_body=body,
            idempotency_key=idempotency_key or f"import:{uuid.uuid4().hex}",
            request_timeout=self._submit_timeout,
        )
        return data if isinstance(data, dict) else {}

    async def get_import(self, call: CallContext, import_id: str) -> dict[str, Any]:
        data = await self._request(
            "GET",
            f"{BASE_PATH}/imports/{_segment(import_id)}",
            call,
            operation="import_status",
        )
        return data if isinstance(data, dict) else {}

    async def update_import_mapping(
        self, call: CallContext, import_id: str, body: dict[str, Any]
    ) -> dict[str, Any]:
        data = await self._request(
            "PUT",
            f"{BASE_PATH}/imports/{_segment(import_id)}",
            call,
            operation="import_mapping",
            json_body=body,
            request_timeout=self._submit_timeout,
        )
        return data if isinstance(data, dict) else {}

    async def list_datasets(self, call: CallContext, *, limit: int = 20) -> dict[str, Any]:
        data = await self._request(
            "GET",
            f"{BASE_PATH}/datasets",
            call,
            operation="datasets",
            params={"limit": limit},
        )
        return data if isinstance(data, dict) else {"datasets": []}

    async def get_dataset(self, call: CallContext, dataset_id: str) -> CoreDataset:
        """Counts for one dataset and whether its next optimization is billed. Never the stops."""

        data = await self._request(
            "GET",
            f"{BASE_PATH}/datasets/{_segment(dataset_id)}",
            call,
            operation="dataset",
        )
        return self._parse(CoreDataset, data)

    async def health(self) -> bool:
        """Cheap reachability probe used by /ready (service key only, no account access)."""

        try:
            response = await self._http.get(
                f"{BASE_PATH}/health",
                headers={"X-Vepathos-MCP-Service-Key": self._service_key},
                timeout=3.0,
            )
        except httpx.HTTPError:
            return False
        return response.status_code == 200

    # --- Transport -----------------------------------------------------------------------------

    async def _request(
        self,
        method: str,
        path: str,
        call: CallContext,
        *,
        operation: str,
        json_body: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
        idempotency_key: str | None = None,
        request_timeout: float | None = None,
        absent_error: DomainError | None = None,
    ) -> Any:
        if self._breaker.is_open:
            raise DomainError(
                ErrorCode.BACKEND_UNAVAILABLE,
                "Vepathos is temporarily unavailable.",
                suggestion="Retry in a moment; identical requests are safe to resend.",
                retry_after_seconds=self._breaker.retry_after_seconds,
            )

        headers = {
            "Authorization": call.authorization,
            "X-Vepathos-MCP-Service-Key": self._service_key,
            "X-Request-Id": call.request_id or uuid.uuid4().hex,
        }
        if call.client_label:
            headers["X-Vepathos-MCP-Client"] = call.client_label
        if call.traceparent:
            headers["traceparent"] = call.traceparent
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key
        if method == "POST" and not idempotency_key:
            raise ValueError("POST requests to Core must carry an Idempotency-Key")

        last_error: DomainError | None = None
        for attempt in range(1, self._max_attempts + 1):
            started = time.monotonic()
            retry_after: int | None = None
            try:
                response = await self._http.request(
                    method,
                    path,
                    headers=headers,
                    json=json_body,
                    params=params,
                    timeout=httpx.Timeout(request_timeout or self._timeout, connect=5.0),
                )
            except httpx.TimeoutException:
                self._observe(operation, started, None)
                self._breaker.record_failure()
                last_error = DomainError(
                    ErrorCode.TIMEOUT,
                    "Vepathos did not answer in time.",
                    suggestion="Retry shortly; identical requests are safe to resend.",
                )
            except httpx.TransportError:
                self._observe(operation, started, None)
                self._breaker.record_failure()
                last_error = DomainError(
                    ErrorCode.BACKEND_UNAVAILABLE,
                    "Vepathos is temporarily unavailable.",
                    suggestion="Retry in a moment; identical requests are safe to resend.",
                    retry_after_seconds=10,
                )
            else:
                self._observe(operation, started, response.status_code)
                retry_after = _retry_after(response)
                if response.status_code in RETRYABLE_STATUS:
                    self._breaker.record_failure()
                    last_error = from_core_error(response.status_code, _json(response), retry_after)
                elif response.status_code >= 400:
                    if response.status_code >= 500:
                        self._breaker.record_failure()
                    else:
                        # A 4xx means Core is reachable and answered deliberately.
                        self._breaker.record_success()
                    payload = _json(response)
                    if response.status_code == 404 and absent_error is not None and payload is None:
                        # No error envelope on a 404: this Core build does not serve the route.
                        raise absent_error
                    raise from_core_error(response.status_code, payload, retry_after)
                else:
                    self._breaker.record_success()
                    payload = _json(response)
                    if payload is None:
                        raise DomainError(
                            ErrorCode.INTERNAL_ERROR,
                            "Vepathos returned an unexpected response.",
                            retryable=False,
                        )
                    return payload

            if attempt >= self._max_attempts or self._breaker.is_open:
                break
            delay = min(float(retry_after), MAX_RETRY_AFTER_SECONDS) if retry_after else _backoff(attempt)
            await self._sleep(delay)

        assert last_error is not None
        raise last_error

    def _observe(self, operation: str, started: float, status: int | None) -> None:
        if self._observer is not None:
            self._observer(operation, time.monotonic() - started, status)

    @staticmethod
    def _parse(model: type[ModelT], data: Any) -> ModelT:
        try:
            return model.model_validate(data)
        except ValidationError:
            raise DomainError(
                ErrorCode.INTERNAL_ERROR,
                "Vepathos returned an unexpected response.",
                suggestion="Try again later. If it persists, contact Vepathos support.",
                retryable=False,
            ) from None


def _segment(value: str) -> str:
    # Ids are validated upstream; quoting keeps any unexpected character from changing the path.
    return quote(value, safe="")


def _json(response: httpx.Response) -> Any:
    try:
        return response.json()
    except ValueError:
        return None


def _retry_after(response: httpx.Response) -> int | None:
    raw = response.headers.get("Retry-After")
    if raw is None:
        return None
    try:
        seconds = int(float(raw))
    except ValueError:
        return None
    return seconds if seconds > 0 else None


def _backoff(attempt: int) -> float:
    base = 0.4 * (3 ** (attempt - 1))
    return float(base + random.uniform(0, base / 2))  # noqa: S311 - jitter, not crypto

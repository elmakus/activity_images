"""Retry only explicit rate-limit rejection, not possibly billed unknown outcomes."""
from __future__ import annotations

import asyncio
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import sys

import aiohttp
import pytest

MODULE_PATH = (
    Path(__file__).resolve().parents[1]
    / "custom_components" / "activity_images" / "retry.py"
)
spec = spec_from_file_location("activity_images_retry_spec", MODULE_PATH)
assert spec is not None and spec.loader is not None
retry = module_from_spec(spec)
sys.modules[spec.name] = retry
spec.loader.exec_module(retry)


class ProviderHttpError(Exception):
    def __init__(self, status, headers=None):
        self.status = status
        self.headers = headers or {}
        super().__init__(f"HTTP failure {status}")


@pytest.fixture
def delays(monkeypatch):
    seen = []

    async def quick_sleep(seconds):
        seen.append(seconds)

    monkeypatch.setattr(retry.asyncio, "sleep", quick_sleep)
    return seen


@pytest.mark.asyncio
async def test_429_retries_twice_only_and_then_succeeds(delays):
    count = 0

    async def operation():
        nonlocal count
        count += 1
        if count < 3:
            raise ProviderHttpError(429)
        return "generated"

    assert await retry.async_retry_transient(operation) == "generated"
    assert count == 3
    assert delays == [2.0, 4.0]


@pytest.mark.asyncio
async def test_429_never_attempts_fourth_time(delays):
    count = 0

    async def operation():
        nonlocal count
        count += 1
        raise ProviderHttpError(429)

    with pytest.raises(ProviderHttpError):
        await retry.async_retry_transient(operation)
    assert count == 3
    assert delays == [2.0, 4.0]


@pytest.mark.parametrize("status", [400, 401, 403, 404, 422])
@pytest.mark.asyncio
async def test_permanent_http_errors_never_retry(status, delays):
    count = 0

    async def operation():
        nonlocal count
        count += 1
        raise ProviderHttpError(status)

    with pytest.raises(ProviderHttpError):
        await retry.async_retry_transient(operation)
    assert count == 1
    assert not delays


@pytest.mark.parametrize("status", [500, 502, 503, 504, 599])
@pytest.mark.asyncio
async def test_server_errors_require_explicit_retry(status, delays):
    count = 0

    async def operation():
        nonlocal count
        count += 1
        if count == 1:
            raise ProviderHttpError(status)
        return 123

    with pytest.raises(ProviderHttpError):
        await retry.async_retry_transient(operation)
    assert count == 1
    assert delays == []


@pytest.mark.parametrize("exception_type", [TimeoutError, ConnectionError, aiohttp.ClientConnectionError])
@pytest.mark.asyncio
async def test_timeout_and_connections_never_replay_unknown_work(exception_type, delays):
    count = 0

    async def operation():
        nonlocal count
        count += 1
        if count < 3:
            raise exception_type("lost connection")
        return True

    with pytest.raises(exception_type):
        await retry.async_retry_transient(operation)
    assert count == 1
    assert delays == []


@pytest.mark.asyncio
async def test_retry_after_header_is_respected_and_bounded(delays):
    count = 0

    async def operation():
        nonlocal count
        count += 1
        if count == 1:
            raise ProviderHttpError(429, {"Retry-After": "17"})
        if count == 2:
            raise ProviderHttpError(429, {"Retry-After": "900"})
        return count

    assert await retry.async_retry_transient(operation) == 3
    assert delays == [17, 120.0]


@pytest.mark.asyncio
async def test_wrapped_provider_error_is_recognized(delays):
    count = 0

    async def operation():
        nonlocal count
        count += 1
        if count == 1:
            try:
                raise ProviderHttpError(429, {"Retry-After": "9"})
            except ProviderHttpError as exc:
                raise RuntimeError("HA wrapped provider error") from exc
        return count

    assert await retry.async_retry_transient(operation) == 2
    assert delays == [9]


@pytest.mark.asyncio
async def test_unknown_exception_is_not_retried(delays):
    count = 0

    async def operation():
        nonlocal count
        count += 1
        raise RuntimeError("Something failed")

    with pytest.raises(RuntimeError):
        await retry.async_retry_transient(operation)
    assert count == 1
    assert delays == []


@pytest.mark.asyncio
async def test_cancelled_task_is_never_retried(delays):
    count = 0

    async def operation():
        nonlocal count
        count += 1
        raise asyncio.CancelledError()

    with pytest.raises(asyncio.CancelledError):
        await retry.async_retry_transient(operation)
    assert count == 1
    assert delays == []

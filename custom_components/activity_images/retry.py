"""Bounded retries for explicit rate-limit rejection, never unknown outcomes."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import logging
from typing import Awaitable, Callable, TypeVar

_LOGGER = logging.getLogger(__name__)
T = TypeVar("T")
MAX_ATTEMPTS = 3  # Includes the first request: only two retries.
MAX_DELAY_SECONDS = 120.0


def _chain(exc: BaseException):
    """Walk wrapped provider errors (HA may wrap aiohttp/OpenAI exceptions)."""
    seen: set[int] = set()
    current: BaseException | None = exc
    for _ in range(6):
        if current is None or id(current) in seen:
            break
        seen.add(id(current))
        yield current
        current = current.__cause__ or current.__context__


def _status(exc: BaseException) -> int | None:
    for candidate in (exc, getattr(exc, "response", None)):
        if candidate is None:
            continue
        for field in ("status", "status_code"):
            value = getattr(candidate, field, None)
            if isinstance(value, int) and not isinstance(value, bool):
                return value
    return None


def _transient(exc: BaseException) -> bool:
    for item in _chain(exc):
        status = _status(item)
        if status is not None:
            # A timeout, disconnect or 5xx can follow accepted billable work.
            # Only an explicit 429 rejection authorizes an automatic retry.
            return status == 429
    return False


def _retry_after_seconds(exc: BaseException) -> float | None:
    for item in _chain(exc):
        for candidate in (item, getattr(item, "response", None)):
            headers = getattr(candidate, "headers", None)
            if not headers:
                continue
            raw = headers.get("Retry-After") or headers.get("retry-after")
            if raw is None:
                continue
            try:
                return max(0.0, float(raw))
            except (ValueError, TypeError):
                pass
            try:
                date = parsedate_to_datetime(raw)
                if date.tzinfo is None:
                    date = date.replace(tzinfo=timezone.utc)
                return max(0.0, (date - datetime.now(timezone.utc)).total_seconds())
            except (ValueError, TypeError, OverflowError):
                continue
    return None


async def async_retry_transient(operation: Callable[[], Awaitable[T]]) -> T:
    """At most 3 rate-limit-rejected attempts; other outcomes need explicit retry."""
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            return await operation()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            if attempt >= MAX_ATTEMPTS or not _transient(exc):
                raise
            exponential_delay = float(2 ** attempt)  # 2s, then 4s.
            retry_after = _retry_after_seconds(exc)
            delay = min(
                MAX_DELAY_SECONDS,
                max(exponential_delay, retry_after or 0.0),
            )
            _LOGGER.warning(
                "Transient image-provider error (%s); retry %d/%d in %.1fs",
                type(exc).__name__, attempt, MAX_ATTEMPTS - 1, delay,
            )
            await asyncio.sleep(delay)
    raise AssertionError("Unreachable: retry loop must return or raise")

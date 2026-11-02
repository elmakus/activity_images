"""Persisted, deduplicated AI generation queue, with provider cooldown."""
from __future__ import annotations

import asyncio
import contextlib
import logging
import random
import time

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from .const import DOMAIN, CONF_MIN_INTERVAL, CONF_MAX_INTERVAL
from .engine import ActivityImageGenerator
from .image_cache import normalize_title

_LOGGER = logging.getLogger(__name__)


class ActivityImageQueue:
    def __init__(self, hass: HomeAssistant, engine: ActivityImageGenerator) -> None:
        self.hass = hass
        self.engine = engine
        self._store = Store(hass, 1, f"{DOMAIN}_jobs")
        self._pending: list[str] = []
        self._failed: dict[str, str] = {}
        self._next_run: float = 0.0
        self._current: str | None = None
        self._worker: asyncio.Task | None = None
        self._lock = asyncio.Lock()
        self._closed = False

    async def async_setup(self) -> None:
        data = await self._store.async_load() or {}
        raw = data.get("pending") or []
        self._pending = [
            title for title in raw if isinstance(title, str) and title.strip()
        ][:2000]
        failed = data.get("failed") or {}
        self._failed = {
            k: v for k, v in failed.items()
            if isinstance(k, str) and isinstance(v, str)
        } if isinstance(failed, dict) else {}
        self._next_run = float(data.get("next_run") or 0)
        if self._pending:
            self._kick()

    async def _save(self) -> None:
        await self._store.async_save({
            "pending": self._pending,
            "failed": self._failed,
            "next_run": self._next_run,
        })

    async def async_enqueue(self, titles: list[str]) -> list[str]:
        """Accept up to 64 new unique jobs; never duplicate pending jobs."""
        accepted = []
        async with self._lock:
            known = {normalize_title(t) for t in self._pending}
            if self._current:
                known.add(normalize_title(self._current))
            for value in titles[:64]:
                if not isinstance(value, str):
                    continue
                title = value.strip()
                key = normalize_title(title)
                if not title or len(title) > 255 or not key or key in known or key in self._failed:
                    continue
                if await self.engine.cache.async_find(title):
                    continue
                known.add(key)
                accepted.append(title)
                self._pending.append(title)
            if accepted:
                await self._save()
        if accepted:
            self._kick()
        return accepted

    async def async_resolve(self, titles: list[str], enqueue_missing: bool = False) -> dict:
        unique = list(dict.fromkeys(
            title.strip() for title in titles[:64]
            if isinstance(title, str) and title.strip()
        ))
        images = await self.engine.cache.async_find_many(unique)
        accepted = await self.async_enqueue(
            [title for title in unique if title not in images]
        ) if enqueue_missing else []
        return {"images": images, "queued": accepted}

    async def async_cancel(self, titles: list[str]) -> int:
        keys = {normalize_title(value) for value in titles}
        async with self._lock:
            original = len(self._pending)
            self._pending = [
                t for t in self._pending if normalize_title(t) not in keys
            ]
            removed = original - len(self._pending)
            if removed:
                await self._save()
        return removed

    async def async_clear_errors(self) -> int:
        async with self._lock:
            removed = len(self._failed)
            self._failed.clear()
            await self._save()
            return removed

    def status(self) -> dict:
        return {
            "pending": list(self._pending),
            "current": self._current,
            "failed": dict(self._failed),
            "next_run_at": self._next_run or None,
        }

    def _kick(self) -> None:
        if self._closed or not self._pending:
            return
        if self._worker and not self._worker.done():
            return
        self._worker = self.hass.async_create_task(self._run())
        self._worker.add_done_callback(self._on_worker_done)

    def _on_worker_done(self, worker: asyncio.Task) -> None:
        """A title queued during the worker's final return must not be stranded."""
        if worker.cancelled() or self._closed:
            return
        if self._pending:
            self._kick()

    async def _run(self) -> None:
        """A single worker; failures are recorded and do not cause busy retries."""
        while not self._closed:
            async with self._lock:
                if not self._pending:
                    return
                title = self._pending[0]
                self._current = title
            result = None
            try:
                if not await self.engine.cache.async_find(title):
                    wait = max(0.0, self._next_run - time.time())
                    if wait:
                        await asyncio.sleep(wait)
                    if self._closed:
                        return
                result = await self.engine.async_generate(title)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                _LOGGER.warning("Activity image generation failed for %r: %s", title, exc)
                self._failed[normalize_title(title)] = (
                    f"{type(exc).__name__}: {str(exc)[:300]}"
                )
            finally:
                if not self._closed:
                    async with self._lock:
                        if self._pending and self._pending[0] == title:
                            self._pending.pop(0)
                        self._current = None
                        if not result or not result["reused"]:
                            minimum = max(0, int(self.engine.settings.get(CONF_MIN_INTERVAL, 20)))
                            maximum = max(minimum, int(self.engine.settings.get(CONF_MAX_INTERVAL, 35)))
                            self._next_run = time.time() + random.uniform(minimum, maximum) * 60
                        await self._save()

    async def async_shutdown(self) -> None:
        self._closed = True
        if self._worker and not self._worker.done():
            self._worker.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._worker
        await self._save()

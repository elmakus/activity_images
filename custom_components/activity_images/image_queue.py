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
        self._retry_task: asyncio.Task | None = None
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
        try:
            self._next_run = max(0.0, float(data.get("next_run") or 0))
        except (ValueError, TypeError):
            self._next_run = 0.0
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
                if not title or len(title) > 255 or not key or key in known:
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

    async def async_resolve_activity_images(
        self, titles: list[str], enqueue_titles: list[str] | None = None
    ) -> tuple[dict[str, str], list[str]]:
        """Original source API: enqueue only explicitly allowed missing titles.

        An omitted enqueue_titles means all missing titles may be queued.
        An empty list means resolve-only, without generating historical items.
        """
        unique = list(dict.fromkeys(
            title.strip() for title in titles if isinstance(title, str) and title.strip()
        ))[:64]
        images = await self.engine.cache.async_find_many(unique)
        wanted = set(unique if enqueue_titles is None else enqueue_titles)
        pending = [title for title in unique if title in wanted and title not in images]
        accepted = await self.async_enqueue(pending) if pending else []
        return images, accepted

    async def async_resolve(self, titles: list[str], enqueue_missing: bool = False) -> dict:
        images, accepted = await self.async_resolve_activity_images(
            titles, enqueue_titles=titles if enqueue_missing else []
        )
        return {
            "images": images,
            "image_metadata": await self.engine.cache.async_image_metadata(images),
            "queued": accepted,
            "queue_size": len(self._pending),
            "next_run_at": self._next_run or None,
        }

    async def async_cancel(self, titles: list[str]) -> int:
        keys = {normalize_title(value) for value in titles}
        async with self._lock:
            original = len(self._pending)
            self._pending = [
                t for t in self._pending
                if normalize_title(t) not in keys or t == self._current
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
        if self._retry_task and not self._retry_task.done() and self._worker is not None and self._worker.done():
            return
        if self._worker and not self._worker.done():
            return
        self._worker = self.hass.async_create_task(self._run())
        self._worker.add_done_callback(self._on_worker_done)

    def _on_worker_done(self, worker: asyncio.Task) -> None:
        """A title queued during the worker's final return must not be stranded."""
        if worker.cancelled() or self._closed:
            return
        failure = worker.exception()
        if failure is not None:
            _LOGGER.error("Activity image queue worker stopped: %s", failure)
            return
        if self._pending and (self._retry_task is None or self._retry_task.done()):
            self._kick()

    async def _retry_after(self, delay: float) -> None:
        """Keep a failed activity queued, as the original source worker did.

        Retrying waits for provider cooldown; a zero-cooldown configuration
        still applies a one-minute floor after a provider failure.
        """
        try:
            await asyncio.sleep(delay)
            if not self._closed:
                self._kick()
        except asyncio.CancelledError:
            raise
        finally:
            self._retry_task = None

    def _schedule_retry(self, delay: float) -> None:
        if self._closed:
            return
        if self._retry_task and not self._retry_task.done():
            return
        self._retry_task = self.hass.async_create_task(self._retry_after(delay))

    async def _run(self) -> None:
        """A single worker; failures are recorded and do not cause busy retries."""
        while not self._closed:
            async with self._lock:
                if not self._pending:
                    return
                title = self._pending[0]
                self._current = title
            result = None
            failed = False
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
                failed = True
                _LOGGER.warning("Activity image generation failed for %r: %s", title, exc)
                self._failed[normalize_title(title)] = (
                    f"{type(exc).__name__}: {str(exc)[:300]}"
                )
            finally:
                if not self._closed:
                    async with self._lock:
                        if not failed and self._pending and self._pending[0] == title:
                            self._pending.pop(0)
                            self._failed.pop(normalize_title(title), None)
                        self._current = None
                        if failed or (result and not result["reused"]):
                            minimum = max(0, int(self.engine.settings.get(CONF_MIN_INTERVAL, 20)))
                            maximum = max(minimum, int(self.engine.settings.get(CONF_MAX_INTERVAL, 35)))
                            delay = random.uniform(minimum, maximum) * 60
                            if failed:
                                delay = max(delay, 60.0)
                            self._next_run = time.time() + delay
                        await self._save()
            if failed:
                self._schedule_retry(max(1.0, self._next_run - time.time()))
                return

    async def async_shutdown(self) -> None:
        self._closed = True
        if self._worker and not self._worker.done():
            self._worker.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._worker
        if self._retry_task and not self._retry_task.done():
            self._retry_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._retry_task
        await self._save()

"""Persistent deduplicated generation queue, without indefinite retry loops."""
from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Awaitable, Callable
from copy import deepcopy
from datetime import UTC, datetime
from uuid import uuid4

from homeassistant.core import CoreState, HomeAssistant
from homeassistant.helpers.storage import Store

from .const import DOMAIN
from .engine import ActivityImageGenerator, GenerationSuppressed
from .image_cache import asset_url, normalize_title
from .storage import async_save_verified

_LOGGER = logging.getLogger(__name__)


class ActivityImageQueue:
    def __init__(self, hass: HomeAssistant, engine: ActivityImageGenerator) -> None:
        self.hass = hass
        self.engine = engine
        self._store = Store(hass, 1, f"{DOMAIN}_jobs", atomic_writes=True)
        self._pending: list[str] = []
        self._failed: dict[str, str] = {}
        self._current: str | None = None
        self._worker: asyncio.Task | None = None
        self._lock = asyncio.Lock()
        self._closed = False
        self._loaded = False
        self._save_generation = 0
        self._jobs: list[dict] = []
        self._manual_blocked: set[str] = set()
        self._retry_blocked: set[str] = set()
        self.calendar_authorizer: Callable[[str, list[dict]], Awaitable[None]] | None = None

    async def async_setup(self) -> None:
        data = await self._store.async_load() or {}
        self._manual_blocked = set(data.get("manual_blocked", []))
        self._retry_blocked = set(data.get("retry_blocked", []))
        raw = data.get("pending") or []
        self._pending = [
            title for title in raw if isinstance(title, str) and title.strip()
        ]
        failed = data.get("failed") or {}
        self._failed = {
            k: v for k, v in failed.items()
            if isinstance(k, str) and isinstance(v, str)
        } if isinstance(failed, dict) else {}
        # Old queue data may include a pending failed job and a cooldown timestamp.
        # Do not resume any old failed job: no implicit retries after restart.
        previous_pending = len(self._pending)
        self._pending = [
            title for title in self._pending
            if normalize_title(title) not in self._failed
        ]
        self._jobs = [job for job in data.get("jobs", []) if isinstance(job, dict)
                      and job.get("kind") in {"manual", "background"} and isinstance(job.get("id"), str)
                      and isinstance(job.get("title"), str) and job.get("state") in {
                          "pending", "running", "review", "accepted", "discarded", "failed", "cancelled", "interrupted", "completed", "abandoned",
                      }]
        for job in self._jobs:
            if job["state"] in {"running", "interrupted"} or (job["kind"] == "manual" and job["state"] == "pending"):
                self._set_state(job, "interrupted", "Request outcome unknown — explicitly retry after checking Queue & history")
                self._retry_blocked.add(normalize_title(job["title"]))
                self._pending = [t for t in self._pending if normalize_title(t) != normalize_title(job["title"])]
        for title in list(self._pending):
            job = self._background(title)
            if job is None or job.get("origin") not in {"explicit", "calendar"} or (
                    job.get("origin") == "calendar" and not self._valid_sources(job.get("sources"))):
                if job is None:
                    job = self._new_job(title, "background")
                    self._jobs.append(job)
                self._set_state(job, "interrupted", "Legacy pending work has no verified origin; explicit review/retry required")
                self._retry_blocked.add(normalize_title(title))
                self._pending.remove(title)
        self._trim_history()
        self._loaded = True
        if len(self._pending) != previous_pending or self._jobs:
            await self._save()
        if self._pending:
            self._kick()

    async def _save(self) -> None:
        self._trim_history()
        write = asyncio.create_task(async_save_verified(self.hass, self._store, {
            "pending": list(self._pending),
            "failed": deepcopy(self._failed),
            "jobs": deepcopy(self._jobs),
            "manual_blocked": sorted(self._manual_blocked),
            "retry_blocked": sorted(self._retry_blocked),
        }))
        cancelled = False
        # Drain the retained write before cancellation can release the queue lock.
        while not write.done():
            try:
                await asyncio.shield(write)
            except asyncio.CancelledError:
                cancelled = True
        await write
        self._loaded = True
        self._save_generation += 1
        if cancelled:
            raise asyncio.CancelledError

    @contextlib.asynccontextmanager
    async def _mutation(self):
        async with self._lock:
            if self._closed:
                raise ValueError("Activity Images is unloading")
            generation = self._save_generation
            pending, failed, jobs, blocked = list(self._pending), dict(self._failed), list(self._jobs), set(self._manual_blocked)
            retry_blocked = set(self._retry_blocked)
            job_values = deepcopy(jobs)
            try:
                yield
            except BaseException as exc:
                if not isinstance(exc, asyncio.CancelledError) or generation == self._save_generation:
                    self._pending, self._failed, self._jobs, self._manual_blocked = pending, failed, jobs, blocked
                    self._retry_blocked = retry_blocked
                    for job, values in zip(jobs, job_values, strict=True):
                        job.clear()
                        job.update(values)
                raise

    @staticmethod
    def _valid_sources(sources) -> bool:
        return (isinstance(sources, list) and 1 <= len(sources) <= 640
                and all(isinstance(source, dict)
                        and isinstance(source.get("calendar_entity_id"), str)
                        and source["calendar_entity_id"].startswith("calendar.")
                        and isinstance(source.get("raw_summary"), str)
                        and 1 <= len(source["raw_summary"]) <= 1024 for source in sources))

    def _background(self, title: str) -> dict | None:
        return next((job for job in reversed(self._jobs) if job["kind"] == "background"
                     and normalize_title(job["title"]) == normalize_title(title)
                     and job["state"] in {"pending", "running"}), None)

    async def async_enqueue(self, titles: list[str], *, calendar_sources: list[dict] | None = None) -> list[str]:
        """Persist provenance before dispatch; public enqueueing stays independent."""
        if calendar_sources is not None and not self._valid_sources(calendar_sources):
            if not titles and calendar_sources == []:
                return []
            raise ValueError("Calendar jobs require original verified raw-summary sources")
        accepted = []
        async with self._mutation():
            if self._closed:
                raise ValueError("Activity Images is unloading")
            known = {normalize_title(t) for t in self._pending}
            known.update(normalize_title(job["title"]) for job in self._jobs if job["state"] in {"pending", "running", "review"})
            if self._current:
                known.add(normalize_title(self._current))
            for value in titles[:64]:
                if not isinstance(value, str):
                    continue
                title = value.strip()
                key = normalize_title(title)
                if (
                    not title or len(title) > 255 or not key
                    or key in known or key in self._failed or key in self._manual_blocked or key in self._retry_blocked
                    or self.engine.cache.reserved(title)
                ):
                    continue
                if await self.engine.cache.async_find(title):
                    continue
                known.add(key)
                accepted.append(title)
                job = self._new_job(title, "background")
                job["origin"] = "calendar" if calendar_sources is not None else "explicit"
                if calendar_sources is not None:
                    job["sources"] = [dict(source) for source in calendar_sources
                                      if normalize_title(source["raw_summary"]) == key]
                    if not job["sources"]:
                        raise ValueError("No verified calendar source for title")
                self._jobs.append(job)
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
            "next_run_at": None,
        }

    async def async_cancel(self, titles: list[str]) -> int:
        keys = {normalize_title(value) for value in titles}
        async with self._mutation():
            original = len(self._pending)
            for job in self._jobs:
                if job["state"] == "pending" and normalize_title(job["title"]) in keys:
                    self._set_state(job, "cancelled")
            self._pending = [
                t for t in self._pending
                if normalize_title(t) not in keys or t == self._current
            ]
            removed = original - len(self._pending)
            if removed:
                # A canceled retry is not an outstanding generation failure.
                for key in keys:
                    if self._current is None or normalize_title(self._current) != key:
                        self._failed.pop(key, None)
                await self._save()
        return removed

    async def async_clear_errors(self, *, preserve_retry_blocks: bool = False) -> int:
        async with self._mutation():
            removed = len(self._failed)
            if preserve_retry_blocks:
                self._retry_blocked.update(key for key in self._failed if key not in self._manual_blocked)
            else:
                # Preserve the existing public action's clear-and-requeue semantics.
                self._retry_blocked.clear()
            self._failed.clear()
            for job in self._jobs:
                job["error"] = None
            await self._save()
            return removed

    def status(self) -> dict:
        return {
            "pending": list(self._pending),
            "current": self._current,
            "failed": dict(self._failed),
            "next_run_at": None,
            "jobs": [self._public_job(job) for job in self._jobs if job["kind"] == "manual" or job["state"] not in {"pending", "running"}],
            "active": self.engine.is_generating or self._current is not None,
            "retry_blocked": sorted({
                next((job["title"] for job in reversed(self._jobs) if normalize_title(job["title"]) == key), key)
                for key in self._retry_blocked
            }),
        }

    def _manual(self, title: str) -> dict | None:
        return next((job for job in self._jobs if job["kind"] == "manual"
                     and normalize_title(job["title"]) == normalize_title(title)
                     and job["state"] in {"pending", "running", "review"}), None)

    @staticmethod
    def _set_state(job: dict, state: str, error: str | None = None) -> None:
        job.update(state=state, error=error, updated_at=datetime.now(UTC).isoformat())

    def _public_job(self, job: dict) -> dict:
        from .library import IMAGE_API, ActivityImageLibrary
        result_deleted = bool(job.get("result_deleted")) or self.engine.cache.is_deleted(job.get("result_url") or "")
        url = None if result_deleted else job.get("result_url")
        image_id = ActivityImageLibrary.image_id(url.split("?", 1)[0].rsplit("/", 1)[-1]) if url else None
        return {key: job.get(key) for key in ("id", "title", "kind", "state", "error", "created_at", "updated_at")} | {
            "key": normalize_title(job["title"]),
            "image_id": image_id, "image_url": f"{IMAGE_API}/{image_id}/master" if image_id else None,
            "result_deleted": result_deleted,
            "operation_id": job.get("deletion", {}).get("operation_id"),
            "origin": job.get("origin", "manual" if job["kind"] == "manual" else "legacy"),
        }

    def _trim_history(self) -> None:
        terminal = [job for job in self._jobs if job["state"] not in {"pending", "running", "review"}]
        remove = {job["id"] for job in terminal[:-100]}
        self._jobs = [job for job in self._jobs if job["id"] not in remove]

    def _new_job(self, title: str, kind: str) -> dict:
        now = datetime.now(UTC).isoformat()
        return {"id": uuid4().hex, "title": title, "kind": kind, "state": "pending",
                "error": None, "created_at": now, "updated_at": now,
                "original_url": self.engine.cache.exact(title), "result_url": None}

    async def async_candidate(self, title: str, *, deletion: dict | None = None, job_id: str | None = None) -> dict:
        from .library import validate_title
        title = validate_title(title)
        async with self._mutation():
            if self._closed:
                raise ValueError("Activity Images is unloading")
            if self.engine.cache.reserved(title, operation_id=deletion["operation_id"] if deletion else None):
                raise ValueError("Title is reserved for permanent deletion")
            if job_id and any(job["id"] == job_id for job in self._jobs):
                return self._public_job(self._job(job_id))
            if existing := self._manual(title):
                if deletion or existing.get("deletion"):
                    raise ValueError("An outstanding job already exists for this title")
                return self._public_job(existing)
            if any(normalize_title(t) == normalize_title(title) for t in self._pending):
                raise ValueError(
                    "A background job already exists for this title. Wait for it or cancel "
                    "the waiting job before requesting a fresh candidate."
                )
            job = self._new_job(title, "manual")
            if deletion:
                job.update(id=job_id, deletion=deepcopy(deletion))
            self._manual_blocked.add(normalize_title(title))
            self._jobs.append(job)
            self._pending.append(title)
            await self._save()
        self._kick()
        return self._public_job(job)

    def _job(self, job_id: str) -> dict:
        job = next((job for job in self._jobs if job["id"] == job_id), None)
        if not job:
            raise ValueError("Job not found")
        return job

    def candidate_url(self, job_id: str) -> str | None:
        return self._job(job_id).get("result_url")

    async def async_accept(self, job_id: str) -> dict:
        async with self._mutation():
            job = self._job(job_id)
            if job.get("deletion"):
                raise ValueError("Replacement candidates are managed by their deletion operation")
            if job["state"] == "accepted":
                return self._public_job(job)
            if job["state"] != "review":
                raise ValueError("Only a review candidate can be accepted")
            if self.engine.cache.exact(job["title"]) != job["result_url"]:
                await self.engine.cache.async_link(job["title"], job["result_url"], replace=True,
                                                   expected=job["original_url"], compare=True)
            self._set_state(job, "accepted")
            self._trim_history()
            try:
                await self._save()
            except Exception:
                if job["original_url"] is None:
                    await self.engine.cache.async_unlink(job["title"], job["result_url"], expected=job["result_url"], compare=True)
                else:
                    await self.engine.cache.async_link(job["title"], job["original_url"], replace=True,
                                                       expected=job["result_url"], compare=True)
                raise
        if self.engine.image_saved_callback:
            self.engine.image_saved_callback(job["title"], job["result_url"])
        return self._public_job(job)

    async def async_discard(self, job_id: str) -> dict:
        async with self._mutation():
            job = self._job(job_id)
            if job.get("deletion"):
                raise ValueError("Replacement candidates are managed by their deletion operation")
            if job["state"] != "review":
                raise ValueError("Only a review candidate can be discarded")
            self._set_state(job, "discarded")
            await self._save()
            self._trim_history()
            return self._public_job(job)

    async def async_retry(self, *, job_id: str | None = None, title: str | None = None) -> dict:
        if job_id:
            job = self._job(job_id)
            if job.get("deletion"):
                raise ValueError("Use the deletion operation explicit retry")
            if job["kind"] != "manual" or job["state"] not in {"failed", "interrupted"}:
                raise ValueError("Only failed or interrupted manual jobs can be retried")
            return await self.async_candidate(job["title"])
        from .library import validate_title
        title = validate_title(title or "")
        key = normalize_title(title)
        accepted = []
        async with self._mutation():
            if key in self._manual_blocked or (key not in self._failed and key not in self._retry_blocked):
                raise ValueError("No failed legacy title to retry; manual jobs require job_id")
            self._failed.pop(key, None)
            self._retry_blocked.discard(key)
            if not any(normalize_title(t) == key for t in self._pending) and not await self.engine.cache.async_find(title):
                self._pending.append(title)
                job = self._new_job(title, "background")
                job["origin"] = "explicit"
                self._jobs.append(job)
                accepted.append(title)
            await self._save()
        if accepted:
            self._kick()
        return {"queued": accepted}

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
        failure = worker.exception()
        if failure is not None:
            _LOGGER.error("Activity image queue worker stopped: %s", failure)
            return
        if self._pending:
            self._kick()

    async def _run(self) -> None:
        """Persist the paid-attempt boundary before contacting the provider."""
        while not self._closed:
            async with self._lock:
                if not self._pending:
                    return
                title = self._pending[0]
                self._current = title
                job = self._manual(title)
                manual = job is not None
                if job is None:
                    job = self._background(title)
                if job is None:
                    # Never guess the authority of an unclassified pending title.
                    job = self._new_job(title, "background")
                    self._jobs.append(job)
                self._set_state(job, "running")
                try:
                    await self._save()
                except Exception:
                    self._set_state(job, "interrupted", "Could not persist attempt; no provider request sent")
                    if manual:
                        self._pending.pop(0)
                    self._current = None
                    raise
            result = None
            state, error = "review" if manual else "completed", None
            try:
                if manual and job.get("deletion"):
                    result = await self.engine.async_generate(title, force=True, remember=False, deletion=job["deletion"])
                elif manual:
                    result = await self.engine.async_generate(title, force=True, remember=False)
                elif job.get("origin") == "calendar":
                    async def authorize(job: dict = job, title: str = title):
                        authorizer = self.calendar_authorizer
                        key = normalize_title(title)
                        if (self._closed or job["state"] != "running" or authorizer is None
                                or key in self._failed or key in self._manual_blocked or key in self._retry_blocked):
                            raise GenerationSuppressed("Automatic queue eligibility changed")
                        await authorizer(title, job.get("sources", []))
                        if (self._closed or job["state"] != "running"
                                or key in self._failed or key in self._manual_blocked or key in self._retry_blocked):
                            raise GenerationSuppressed("Automatic queue eligibility changed while waiting")
                    result = await self.engine.async_generate(title, authorize=authorize)
                elif job.get("origin") == "explicit":
                    result = await self.engine.async_generate(title)
                else:
                    raise GenerationSuppressed("Legacy automatic work requires explicit review")
            except GenerationSuppressed as exc:
                state, error = "cancelled", str(exc)
            except asyncio.CancelledError:
                state, error = "interrupted", "Interrupted; explicitly retry to generate a new candidate"
                raise
            except Exception as exc:
                state, error = "failed", f"{type(exc).__name__}: {str(exc)[:300]}"
                _LOGGER.warning("Activity image generation failed for %r: %s", title, error)
            finally:
                async with self._lock:
                    if result:
                        job["result_url"] = result["image_url"]
                    self._set_state(job, state, error)
                    if state == "failed" and error:
                        self._failed[normalize_title(title)] = error
                    if state == "interrupted":
                        self._retry_blocked.add(normalize_title(title))
                    if self._pending and self._pending[0] == title:
                        self._pending.pop(0)
                    if state not in {"failed", "interrupted"}:
                        self._failed.pop(normalize_title(title), None)
                    self._current = None
                    self._trim_history()
                    # The durable running boundary already requires explicit manual retry.
                    if self.hass.state is not CoreState.stopping:
                        await self._save()

    async def async_supersede_replacements_locked(self, job_ids: list[str]) -> None:
        """Caller holds queue lock; invalidate only explicitly reviewed missing results."""
        jobs = [job for job in self._jobs if job["id"] in job_ids and job.get("deletion")]
        before = deepcopy(jobs)
        generation = self._save_generation
        try:
            for job in jobs:
                self._set_state(job, "failed", "Persisted replacement unavailable; explicit retry superseded this attempt")
            if jobs:
                await self._save()
        except BaseException:
            if generation == self._save_generation:
                for job, values in zip(jobs, before, strict=True):
                    job.clear()
                    job.update(values)
            raise

    async def async_redact_deleted(self, urls: list[str]) -> None:
        """Persist history redaction before any physical cleanup (separate Store)."""
        async with self._mutation():
            for job in self._jobs:
                if asset_url(job.get("result_url") or "") in urls:
                    job["result_url"] = None
                    job["result_deleted"] = True
                if asset_url(job.get("original_url") or "") in urls:
                    job["original_url"] = None
            await self._save()

    async def async_finish_replacements(self, operation_id: str, image_id: str) -> None:
        async with self._mutation():
            item = next(item for item in self.engine.cache._deletions[operation_id]["items"] if item["id"] == image_id)
            for job in self._jobs:
                context = job.get("deletion", {})
                key = context.get("key")
                if (context.get("operation_id") == operation_id and context.get("image_id") == image_id
                        and item["jobs"].get(key) == job["id"] and key in item["candidates"]):
                    job["result_url"] = item["candidates"][key]
                    self._set_state(job, "accepted")
            await self._save()

    async def async_shutdown(self) -> None:
        self._closed = True
        if self._worker and not self._worker.done():
            self._worker.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._worker
        async with self._lock:
            # Pending/running work was persisted before any provider request.
            if self._loaded and self.hass.state is not CoreState.stopping:
                await self._save()

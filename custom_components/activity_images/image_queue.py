"""Persistent deduplicated generation queue, without indefinite retry loops."""
from __future__ import annotations

import asyncio
import contextlib
import logging
import re
from collections.abc import AsyncIterator, Awaitable, Callable, MutableMapping
from typing import NotRequired, TypedDict, cast

from copy import deepcopy
from datetime import UTC, datetime
from uuid import uuid4

from homeassistant.core import CoreState, HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers.storage import Store

from .const import DOMAIN
from .generated_image import ImageTransparencyError
from .engine import ActivityImageGenerator, DeletionContext, GenerationResult, GenerationSuppressed, ProviderConfigurationError
from .image_cache import asset_url, normalize_title
from .storage import async_drain, async_save_verified

_LOGGER = logging.getLogger(__name__)
_REVISION = re.compile(r"^[a-f0-9]{32}$")
MAX_PENDING_JOBS = 256


class CalendarSource(TypedDict):
    calendar_entity_id: str
    raw_summary: str


class Job(TypedDict):
    id: str
    title: str
    kind: str
    state: str
    error: NotRequired[str | None]
    created_at: NotRequired[str]
    updated_at: NotRequired[str]
    original_url: NotRequired[str | None]
    result_url: NotRequired[str | None]
    result_deleted: NotRequired[bool]
    origin: NotRequired[str]
    sources: NotRequired[list[CalendarSource]]
    deletion: NotRequired[DeletionContext]


class ManualBlock(TypedDict):
    reason: str
    revision: str
    job_id: str | None
    title: str
    uncertain: bool
    deletion_operation_id: str | None
    updated_at: str


class ManualBlockInfo(TypedDict):
    key: str
    title: str
    reason: str
    revision: str | None
    job_id: str | None
    uncertain: bool
    deletion: bool


class QueueData(TypedDict, total=False):
    pending: list[str]
    failed: dict[str, str]
    jobs: list[Job]
    manual_blocked: list[str]
    manual_blocks: dict[str, ManualBlock]
    retry_blocked: list[str]


class CoverageSnapshot(TypedDict):
    jobs: list[Job]
    failed: set[str]
    retry_blocked: set[str]
    manual_blocked: set[str]
    manual_blocks: dict[str, ManualBlock]


class PublicJob(TypedDict):
    id: str
    title: str
    kind: str
    state: str
    error: str | None
    created_at: str | None
    updated_at: str | None
    key: str
    image_id: str | None
    image_url: str | None
    result_deleted: bool
    operation_id: str | None
    origin: str
    thumbnail_url: NotRequired[str | None]
    result_unavailable: NotRequired[bool]


class QueueStatus(TypedDict):
    pending: list[str]
    current: str | None
    failed: dict[str, str]
    next_run_at: None
    jobs: list[PublicJob]
    active: bool
    retry_blocked: list[str]
    manual_blocks: list[ManualBlockInfo]


class ResolveResult(TypedDict):
    images: dict[str, str]
    image_metadata: dict[str, dict[str, int]]
    queued: list[str]
    queue_size: int
    next_run_at: None


class ActivityImageQueue:
    def __init__(self, hass: HomeAssistant, engine: ActivityImageGenerator) -> None:
        self.hass = hass
        self.engine = engine
        self._store: Store[QueueData] = Store(hass, 1, f"{DOMAIN}_jobs", atomic_writes=True)
        self._pending: list[str] = []
        self._failed: dict[str, str] = {}
        self._current: str | None = None
        self._worker: asyncio.Task[None] | None = None
        self._lock = asyncio.Lock()
        # Preserve synchronous same-title reuse for concurrent direct callers.
        # The engine lock separately serializes provider calls with queue work.
        self._direct_lock = asyncio.Lock()
        self._direct_tasks: set[asyncio.Task[GenerationResult]] = set()
        self._closed = False
        self._ready = True
        self._loaded = False
        self._save_generation = 0
        self._jobs: list[Job] = []
        self._manual_blocked: set[str] = set()
        self._manual_blocks: dict[str, ManualBlock] = {}
        self._retry_blocked: set[str] = set()
        self.calendar_authorizer: Callable[[str, list[CalendarSource]], Awaitable[None]] | None = None
        self.deletion_authorizer: Callable[[str, DeletionContext], Awaitable[None]] | None = None

    async def async_setup(self, *, start_worker: bool = True) -> None:
        self._ready = False
        data: QueueData = await self._store.async_load() or {}
        self._manual_blocked = set(data.get("manual_blocked", []))
        self._retry_blocked = set(data.get("retry_blocked", []))
        self._manual_blocks = {}
        raw_blocks = data.get("manual_blocks") or {}
        if isinstance(raw_blocks, dict):
            for key, entry in raw_blocks.items():
                if (not isinstance(key, str) or key not in self._manual_blocked
                        or not isinstance(entry, dict)
                        or entry.get("reason") not in {"active", "cancelled", "discarded", "accepted", "unknown"}
                        or not isinstance(entry.get("revision"), str) or not _REVISION.fullmatch(entry["revision"])
                        or type(entry.get("uncertain")) is not bool):
                    # Malformed optional metadata falls back to legacy_unknown.
                    continue
                job_id = entry.get("job_id")
                title = entry.get("title")
                deletion_id = entry.get("deletion_operation_id")
                self._manual_blocks[key] = {
                    "reason": entry["reason"],
                    "revision": entry["revision"],
                    "job_id": job_id if isinstance(job_id, str) else None,
                    "title": title if isinstance(title, str) and title else key,
                    "uncertain": entry["uncertain"] or entry["reason"] == "unknown",
                    "deletion_operation_id": deletion_id if isinstance(deletion_id, str) else None,
                    "updated_at": entry.get("updated_at") if isinstance(entry.get("updated_at"), str) else "",
                }
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
                if job["kind"] == "manual":
                    self._touch_manual_block(job["title"], reason="unknown", job_id=job["id"],
                                             deletion_operation_id=(job["deletion"].get("operation_id") if job.get("deletion") else None))
        for title in list(self._pending):
            pending_job = self._background(title)
            if pending_job is None or pending_job.get("origin") not in {"explicit", "calendar"} or (
                    pending_job.get("origin") == "calendar" and not self._valid_sources(pending_job.get("sources"))):
                if pending_job is None:
                    pending_job = self._new_job(title, "background")
                    self._jobs.append(pending_job)
                self._set_state(pending_job, "interrupted", "Legacy pending work has no verified origin; explicit review/retry required")
                self._retry_blocked.add(normalize_title(title))
                self._pending.remove(title)
        self._trim_history()
        self._loaded = True
        if len(self._pending) != previous_pending or self._jobs:
            await self._save()
        if start_worker:
            self.start()

    def start(self) -> None:
        """Allow dispatch only after successful initialization."""
        self._ready = True
        self._kick()

    async def _save(self) -> None:
        self._trim_history()
        snapshot: QueueData = {
            "pending": list(self._pending),
            "failed": deepcopy(self._failed),
            "jobs": deepcopy(self._jobs),
            "manual_blocked": sorted(self._manual_blocked),
            "manual_blocks": deepcopy(self._manual_blocks),
            "retry_blocked": sorted(self._retry_blocked),
        }
        _, cancelled = await async_drain(async_save_verified(self.hass, self._store, snapshot))
        self._loaded = True
        self._save_generation += 1
        if cancelled:
            raise asyncio.CancelledError

    @contextlib.asynccontextmanager
    async def _mutation(self) -> AsyncIterator[None]:
        async with self._lock:
            if self._closed:
                raise ValueError("Activity Images is unloading")
            generation = self._save_generation
            pending, failed, jobs, blocked = list(self._pending), dict(self._failed), list(self._jobs), set(self._manual_blocked)
            retry_blocked = set(self._retry_blocked)
            # Jobs are mutable records but nested provenance is immutable after creation.
            manual_blocks = self._manual_blocks.copy()
            job_values = [job.copy() for job in jobs]
            try:
                yield
            except BaseException as exc:
                if not isinstance(exc, asyncio.CancelledError) or generation == self._save_generation:
                    self._pending, self._failed, self._jobs, self._manual_blocked = pending, failed, jobs, blocked
                    self._retry_blocked = retry_blocked
                    self._manual_blocks = manual_blocks
                    for job, values in zip(jobs, job_values, strict=True):
                        raw_job = cast(MutableMapping[str, object], job)
                        raw_job.clear()
                        raw_job.update(values)
                raise

    def _touch_manual_block(self, title: str, *, reason: str, job_id: str | None,
                            deletion_operation_id: str | None = None) -> None:
        """Record the latest manual outcome with a fresh unpredictable restore token.

        A monotonic counter would restart after restore and let an old
        confirmation authorize a new policy; a UUID token cannot recur.
        Sticky uncertainty survives later safe work.
        """
        key = normalize_title(title)
        existing = self._manual_blocks.get(key)
        if existing is None and key in self._manual_blocked:
            prev_uncertain = True
        elif existing:
            prev_uncertain = bool(existing.get("uncertain"))
        else:
            prev_uncertain = False
        self._manual_blocked.add(key)
        self._manual_blocks[key] = {
            "reason": reason,
            "revision": uuid4().hex,
            "job_id": job_id,
            "title": title,
            "uncertain": prev_uncertain or reason == "unknown",
            "deletion_operation_id": deletion_operation_id,
            "updated_at": datetime.now(UTC).isoformat(),
        }

    def _manual_block_info(self, key: str) -> ManualBlockInfo | None:
        if key not in self._manual_blocked:
            return None
        entry = self._manual_blocks.get(key)
        if entry is None:
            title = next((job["title"] for job in reversed(self._jobs)
                            if normalize_title(job["title"]) == key), key)
            deletion = any(normalize_title(job["title"]) == key and job.get("deletion")
                             for job in self._jobs)
            return {"key": key, "title": title, "reason": "legacy_unknown",
                    "revision": None, "job_id": None, "uncertain": True,
                    "deletion": deletion}
        deletion = bool(entry.get("deletion_operation_id")) or any(
            normalize_title(job["title"]) == key and job.get("deletion") for job in self._jobs)
        return {"key": key, "title": entry.get("title") or key, "reason": entry["reason"],
                "revision": entry.get("revision"), "job_id": entry.get("job_id"),
                "uncertain": bool(entry.get("uncertain")), "deletion": deletion}

    async def async_coverage_snapshot(self) -> CoverageSnapshot:
        """Return detached jobs and blocker sets for Coverage under the queue lock.

        Call with the calendar lock held for coherent groups; the queue lock is
        released before bounded cache lookups. Never call from the engine
        authorizer, which runs under the generator lock.
        """
        async with self._lock:
            return {
                "jobs": deepcopy(self._jobs),
                "failed": set(self._failed),
                "retry_blocked": set(self._retry_blocked),
                "manual_blocked": set(self._manual_blocked),
                "manual_blocks": deepcopy(self._manual_blocks),
            }

    @staticmethod
    def _valid_sources(sources: object) -> bool:
        return (isinstance(sources, list) and 1 <= len(sources) <= 640
                and all(isinstance(source, dict)
                        and isinstance(source.get("calendar_entity_id"), str)
                        and source["calendar_entity_id"].startswith("calendar.")
                        and isinstance(source.get("raw_summary"), str)
                        and 1 <= len(source["raw_summary"]) <= 1024 for source in sources))

    def _background(self, title: str) -> Job | None:
        key = normalize_title(title)
        return next((job for job in reversed(self._jobs) if job["kind"] == "background"
                     and normalize_title(job["title"]) == key
                     and job["state"] in {"pending", "running"}), None)

    async def async_enqueue(self, titles: list[str], *, calendar_sources: list[CalendarSource] | None = None) -> list[str]:
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
                if len(self._pending) + len(self._direct_tasks) >= MAX_PENDING_JOBS:
                    break
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
                    job["sources"] = [source.copy() for source in calendar_sources
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

    async def async_generate_for_action(self, title: str, *, force: bool = False) -> GenerationResult:
        """Expose a native action error without weakening internal retry barriers."""
        try:
            result = await self.async_generate_direct(title, force=force)
        except (ProviderConfigurationError, GenerationSuppressed) as exc:
            # These are deliberately local, actionable messages, never provider payloads.
            raise ServiceValidationError(str(exc), translation_domain=DOMAIN, translation_key=exc.translation_key) from exc
        except ImageTransparencyError as exc:
            raise HomeAssistantError(str(exc), translation_domain=DOMAIN, translation_key=exc.translation_key) from exc
        except Exception as exc:
            raise HomeAssistantError(
                "Image generation failed; review Queue & history before retrying", translation_domain=DOMAIN, translation_key="generation_failed"
            ) from exc
        return result

    async def async_generate_direct(self, title: str, *, force: bool = False) -> GenerationResult:
        """Keep synchronous AI Task callers inside the persistent paid-work boundary."""
        if self._closed or not self._ready:
            raise ValueError("Activity Images is not ready")
        if len(self._pending) + len(self._direct_tasks) >= MAX_PENDING_JOBS:
            raise ValueError("Activity Images generation queue is full")
        task = self.hass.async_create_task(self._generate_direct(title, force))
        self._direct_tasks.add(task)
        task.add_done_callback(self._direct_done)
        return await asyncio.shield(task)

    def _direct_done(self, task: asyncio.Task[GenerationResult]) -> None:
        self._direct_tasks.discard(task)
        if not task.cancelled():
            task.exception()

    async def _generate_direct(self, title: str, force: bool) -> GenerationResult:
        from .library import validate_title
        title = validate_title(title)
        key = normalize_title(title)
        async with self._direct_lock:
            async with self._lock:
                if self._closed or not self._ready:
                    raise ValueError("Activity Images is not ready")
                if self.engine.cache.reserved(title):
                    raise GenerationSuppressed("Title is reserved for permanent deletion", translation_key="title_reserved")
                if not force and (cached := await self.engine.cache.async_find(title)):
                    return self.engine.cached_result(title, cached)
            async with self._mutation():
                if self.engine.cache.reserved(title):
                    raise GenerationSuppressed("Title is reserved for permanent deletion", translation_key="title_reserved")
                if any(normalize_title(job["title"]) == key and job["state"] in {"pending", "running", "review"}
                       for job in self._jobs):
                    raise GenerationSuppressed("An outstanding generation job already exists", translation_key="outstanding_job")
                if not force and (key in self._failed or key in self._retry_blocked or key in self._manual_blocked):
                    raise GenerationSuppressed("Previous generation requires explicit retry", translation_key="explicit_retry")
                job = self._new_job(title, "background")
                job["origin"] = "explicit"
                self._set_state(job, "running")
                self._jobs.append(job)
                try:
                    await self._save()
                except asyncio.CancelledError:
                    if job in self._jobs:
                        self._set_state(job, "interrupted", "Interrupted before provider dispatch")
                        self._retry_blocked.add(key)
                    raise
            result = None
            state, error = "completed", None
            try:
                result = await self.engine.async_generate(title, force=force)
                return result
            except asyncio.CancelledError:
                state, error = "interrupted", "Request outcome unknown; explicit retry required"
                raise
            except Exception as exc:
                state, error = "failed", (str(exc) if isinstance(exc, (ProviderConfigurationError, ImageTransparencyError))
                                else f"{type(exc).__name__}: Generation failed; explicit retry required")
                raise
            finally:
                async with self._lock:
                    self._finish_job_locked(job, result, state, error, force=force)
                    if self.hass.state is not CoreState.stopping:
                        await self._save()

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

    async def async_resolve(self, titles: list[str], enqueue_missing: bool = False) -> ResolveResult:
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
            cancelled_manual: dict[str, Job] = {}
            for job in self._jobs:
                if job["state"] == "pending" and normalize_title(job["title"]) in keys:
                    self._set_state(job, "cancelled")
                    if job["kind"] == "manual":
                        cancelled_manual[normalize_title(job["title"])] = job
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
                for key, job in cancelled_manual.items():
                    if self._current is None or normalize_title(self._current) != key:
                        self._touch_manual_block(job["title"], reason="cancelled", job_id=job["id"],
                                                 deletion_operation_id=(job["deletion"].get("operation_id") if job.get("deletion") else None))
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

    def status(self) -> QueueStatus:
        return {
            "pending": list(self._pending),
            "current": self._current,
            "failed": dict(self._failed),
            "next_run_at": None,
            "jobs": [self._public_job(job) for job in self._jobs if job["kind"] == "manual" or job["state"] not in {"pending", "running"}],
            "active": self.engine.is_generating or self._current is not None or bool(self._direct_tasks),
            "retry_blocked": sorted({
                next((job["title"] for job in reversed(self._jobs) if normalize_title(job["title"]) == key), key)
                for key in self._retry_blocked
            }),
            "manual_blocks": sorted(
                (info for key in self._manual_blocked if (info := self._manual_block_info(key))),
                key=lambda item: (item["title"].casefold(), item["title"]),
            ),
        }

    def _manual(self, title: str) -> Job | None:
        key = normalize_title(title)
        return next((job for job in self._jobs if job["kind"] == "manual"
                     and normalize_title(job["title"]) == key
                     and job["state"] in {"pending", "running", "review"}), None)

    @staticmethod
    def _set_state(job: Job, state: str, error: str | None = None) -> None:
        job.update({"state": state, "error": error, "updated_at": datetime.now(UTC).isoformat()})

    def _public_job(self, job: Job) -> PublicJob:
        from .library import IMAGE_API, ActivityImageLibrary
        result_deleted = bool(job.get("result_deleted")) or self.engine.cache.is_deleted(job.get("result_url") or "")
        url = None if result_deleted else job.get("result_url")
        image_id = ActivityImageLibrary.image_id(url.split("?", 1)[0].rsplit("/", 1)[-1]) if url else None
        return {
            "id": job["id"], "title": job["title"], "kind": job["kind"], "state": job["state"],
            "error": job.get("error"), "created_at": job.get("created_at"), "updated_at": job.get("updated_at"),
            "key": normalize_title(job["title"]),
            "image_id": image_id, "image_url": f"{IMAGE_API}/{image_id}/master" if image_id else None,
            "result_deleted": result_deleted,
            "operation_id": (job["deletion"].get("operation_id") if job.get("deletion") else None),
            "origin": job.get("origin", "manual" if job["kind"] == "manual" else "legacy"),
        }

    def _trim_history(self) -> None:
        terminal = [job for job in self._jobs if job["state"] not in {"pending", "running", "review"}]
        remove = {job["id"] for job in terminal[:-100]}
        self._jobs = [job for job in self._jobs if job["id"] not in remove]

    def _new_job(self, title: str, kind: str) -> Job:
        now = datetime.now(UTC).isoformat()
        return {"id": uuid4().hex, "title": title, "kind": kind, "state": "pending",
                "error": None, "created_at": now, "updated_at": now,
                "original_url": self.engine.cache.exact(title), "result_url": None}

    async def async_candidate(self, title: str, *, deletion: DeletionContext | None = None, job_id: str | None = None) -> PublicJob:
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
            if any(normalize_title(t) == normalize_title(title) for t in self._pending) or any(
                    job["kind"] == "background" and job["state"] == "running"
                    and normalize_title(job["title"]) == normalize_title(title) for job in self._jobs):
                raise ValueError(
                    "A background job already exists for this title. Wait for it or cancel "
                    "the waiting job before requesting a fresh candidate."
                )
            if len(self._pending) + len(self._direct_tasks) >= MAX_PENDING_JOBS:
                raise ValueError("Activity Images generation queue is full")
            job = self._new_job(title, "manual")
            if deletion:
                if not job_id:
                    raise ValueError("Deletion replacement requires a job ID")
                job.update({"id": job_id, "deletion": deepcopy(deletion)})
            self._touch_manual_block(title, reason="active", job_id=job["id"],
                                       deletion_operation_id=(deletion.get("operation_id") if deletion else None))
            self._jobs.append(job)
            self._pending.append(title)
            await self._save()
        self._kick()
        return self._public_job(job)

    def _record_result(self, job: Job, result: GenerationResult | None, state: str) -> str:
        if result:
            job["result_url"] = result["image_url"]
        return state

    def _job(self, job_id: str) -> Job:
        job = next((job for job in self._jobs if job["id"] == job_id), None)
        if not job:
            raise ValueError("Job not found")
        return job

    def candidate_url(self, job_id: str) -> str | None:
        return self._job(job_id).get("result_url")

    async def async_accept(self, job_id: str) -> PublicJob:
        async with self._mutation():
            job = self._job(job_id)
            if job.get("deletion"):
                raise ValueError("Replacement candidates are managed by their deletion operation")
            if job["state"] == "accepted":
                return self._public_job(job)
            if job["state"] != "review":
                raise ValueError("Only a review candidate can be accepted")
            result_url = job.get("result_url")
            if not isinstance(result_url, str) or not result_url:
                raise ValueError("Review candidate has no saved image")
            if self.engine.cache.exact(job["title"]) != result_url:
                await self.engine.cache.async_link(job["title"], result_url, replace=True,
                                                   expected=job["original_url"], compare=True)
            self._set_state(job, "accepted")
            self._touch_manual_block(job["title"], reason="accepted", job_id=job["id"])
            self._trim_history()
            try:
                await self._save()
            except Exception:
                if job["original_url"] is None:
                    await self.engine.cache.async_unlink(job["title"], result_url, expected=result_url, compare=True)
                else:
                    await self.engine.cache.async_link(job["title"], job["original_url"], replace=True,
                                                       expected=result_url, compare=True)
                raise
        if self.engine.image_saved_callback:
            self.engine.image_saved_callback(job["title"], result_url)
        return self._public_job(job)

    async def async_discard(self, job_id: str) -> PublicJob:
        async with self._mutation():
            job = self._job(job_id)
            if job.get("deletion"):
                raise ValueError("Replacement candidates are managed by their deletion operation")
            if job["state"] != "review":
                raise ValueError("Only a review candidate can be discarded")
            self._set_state(job, "discarded")
            self._touch_manual_block(job["title"], reason="discarded", job_id=job["id"])
            await self._save()
            self._trim_history()
            return self._public_job(job)

    async def async_retry(self, *, job_id: str | None = None, title: str | None = None) -> PublicJob | dict[str, list[str]]:
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
            if any(normalize_title(job["title"]) == key and job["state"] in {"pending", "running", "review"}
                   for job in self._jobs):
                raise ValueError("An outstanding generation job already exists")
            if len(self._pending) + len(self._direct_tasks) >= MAX_PENDING_JOBS:
                raise ValueError("Activity Images generation queue is full")
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

    async def async_restore_auto(self, title: str, revision: str, confirm: bool) -> dict[str, str | bool]:
        """Remove one explained safe manual block; never enqueues, refreshes or calls AI."""
        from .library import validate_title
        title = validate_title(title)
        if confirm is not True:
            raise ValueError("Restoring automatic generation requires confirmation")
        if not isinstance(revision, str) or not _REVISION.fullmatch(revision):
            raise ValueError("Invalid manual block revision")
        key = normalize_title(title)
        async with self._mutation():
            if key not in self._manual_blocked:
                raise ValueError("No manual automatic-generation block for this title")
            info = self._manual_block_info(key)
            if info is None:
                raise ValueError("No manual automatic-generation block for this title")
            # Uncertainty is sticky and unrestorable, independent of token freshness;
            # legacy blocks carry no token at all.
            if info["uncertain"] or info["reason"] in {"unknown", "legacy_unknown"}:
                raise ValueError("Automatic generation remains blocked: a previous manual request has an unknown outcome. Use explicit Generate new image or Retry.")
            if revision != info["revision"]:
                raise ValueError("Manual block changed elsewhere (revision conflict); refresh before restoring")
            if info["deletion"]:
                raise ValueError("Deletion operation owns recovery for this title; use the deletion Queue & history actions")
            if self.engine.cache.reserved(title):
                raise ValueError("Title is reserved for permanent deletion")
            active = any(normalize_title(job["title"]) == key and job["state"] in {"pending", "running", "review"}
                           for job in self._jobs)
            active = active or any(normalize_title(value) == key for value in self._pending)
            active = active or (self._current is not None and normalize_title(self._current) == key)
            if active or info["reason"] == "active":
                raise ValueError("An outstanding manual job exists for this title; resolve it in Queue & history before restoring")
            if key in self._failed or key in self._retry_blocked:
                raise ValueError("A recorded failure barrier exists for this title; use explicit Retry before restoring")
            if info["reason"] not in {"cancelled", "discarded", "accepted"}:
                raise ValueError("Only a cancelled, discarded or accepted manual block can be restored")
            self._manual_blocked.discard(key)
            self._manual_blocks.pop(key, None)
            await self._save()
            return {"title": info["title"], "key": key, "restored": True}

    def _kick(self) -> None:
        if self._closed or not self._ready or not self._pending:
            return
        if self._worker and not self._worker.done():
            return
        self._worker = self.hass.async_create_task(self._run())
        self._worker.add_done_callback(self._on_worker_done)

    def _on_worker_done(self, worker: asyncio.Task[None]) -> None:
        """A title queued during the worker's final return must not be stranded."""
        if worker.cancelled() or self._closed:
            return
        failure = worker.exception()
        if failure is not None:
            _LOGGER.error("Activity image queue worker stopped (%s)", type(failure).__name__)
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
                except (Exception, asyncio.CancelledError):
                    self._set_state(job, "interrupted", "Could not persist attempt; no provider request sent")
                    key = normalize_title(title)
                    self._retry_blocked.add(key)
                    self._pending.pop(0)
                    if manual:
                        self._touch_manual_block(title, reason="unknown", job_id=job["id"],
                                                 deletion_operation_id=(job["deletion"].get("operation_id") if job.get("deletion") else None))
                    self._current = None
                    raise
            result = None
            state, error = "review" if manual else "completed", None
            try:
                if manual and job.get("deletion"):
                    async def authorize_deletion() -> None:
                        if self._closed or self.deletion_authorizer is None or job["state"] != "running":
                            raise GenerationSuppressed("Deletion replacement is no longer authorized")
                        await self.deletion_authorizer(title, job["deletion"])
                    result = await self.engine.async_generate(title, force=True, remember=False,
                                                              deletion=job["deletion"], authorize=authorize_deletion)
                elif manual:
                    result = await self.engine.async_generate(title, force=True, remember=False)
                elif job.get("origin") == "calendar":
                    async def authorize(job: Job = job, title: str = title) -> None:
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
                state, error = "failed", (str(exc) if isinstance(exc, (ProviderConfigurationError, ImageTransparencyError))
                                else f"{type(exc).__name__}: Generation failed; explicit retry required")
                _LOGGER.warning("Activity image generation failed (%s)", type(exc).__name__)
            finally:
                async with self._lock:
                    self._finish_job_locked(job, result, state, error)
                    if self._pending and self._pending[0] == title:
                        self._pending.pop(0)
                    self._current = None
                    if self.hass.state is not CoreState.stopping:
                        await self._save()

    def _finish_job_locked(self, job: Job, result: GenerationResult | None, state: str,
                           error: str | None, *, force: bool = False) -> None:
        """One terminal/review transition for direct and background work."""
        key = normalize_title(job["title"])
        state = self._record_result(job, result, state)
        self._set_state(job, state, error)
        if state in {"failed", "interrupted"}:
            self._retry_blocked.add(key)
            self._failed[key] = error or "Explicit retry required"
            if job["kind"] == "manual":
                self._touch_manual_block(job["title"], reason="unknown", job_id=job["id"],
                                         deletion_operation_id=(job["deletion"].get("operation_id") if job.get("deletion") else None))
        else:
            self._failed.pop(key, None)
            if force:
                self._retry_blocked.discard(key)

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
                    raw_job = cast(MutableMapping[str, object], job)
                    raw_job.clear()
                    raw_job.update(values)
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
                context = job.get("deletion")
                if not context:
                    continue
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
        direct = tuple(self._direct_tasks)
        for task in direct:
            task.cancel()
        if direct:
            await asyncio.gather(*direct, return_exceptions=True)
        async with self._lock:
            # Pending/running work was persisted before any provider request.
            if self._loaded and self.hass.state is not CoreState.stopping:
                await self._save()

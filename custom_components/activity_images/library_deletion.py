"""Explicit bounded deletion operations, journaled in the mapping Store."""
from __future__ import annotations

import asyncio
import hashlib
import json
import re
import time
from contextlib import asynccontextmanager, suppress
from collections.abc import AsyncIterator, Iterable, MutableMapping
from typing import TYPE_CHECKING, TypedDict, Unpack, cast

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from .calendar_adapter import ActivityCalendarAdapter
    from .image_queue import ActivityImageQueue
    from .library import ActivityImageLibrary
from copy import deepcopy
from pathlib import Path
from io import BytesIO

from PIL import Image
from urllib.parse import quote, unquote
from uuid import uuid4

from .const import (
    CONF_CALENDARS,
    CONF_LOOKAHEAD_DAYS,
    CONF_REFERENCE_MEDIA_ID,
    DEFAULT_LOOKAHEAD_DAYS,
)
from .deletion_files import FileSnapshot, cleanup, group_files, snapshot
from .engine import DeletionContext, GenerationSuppressed, generation_stem
from .image_cache import (
    ACTIVITY_IMAGE_PREFIX,
    CANVAS_NORMALIZATION_EXCLUDED_FILES,
    MappingConflict, DeletionItem, DeletionOperation, MappingEntry, RecoveredCandidate,
    asset_url,
    normalize_title,
)
from .library import _read_owned, _verify
from .image_processing import has_meaningful_transparency
from .retry import MAX_ATTEMPTS
from .storage import async_drain

EXTERNAL_WARNING = "External references (including dashboards and Home Tasks) are unknown. Deletion does not update or delete Home Tasks copies."
_ID = re.compile(r"^[a-f0-9]{32}$")


class PlanItem(TypedDict):
    id: str
    filename: str
    files: list[str]
    titles: list[str]
    needed_titles: list[str]
    safe_without_regeneration: bool
    blocked_reason: str | None
    identity: FileSnapshot
    mappings: dict[str, MappingEntry]
    urls: list[str]
    reserved_titles: list[str]
    expected_titles: dict[str, str | None]


class DeletionPlan(TypedDict):
    items: list[PlanItem]
    demand: list[str]
    entries: dict[str, MappingEntry]
    replacement_requests: int


class SavedPlan(TypedDict, total=False):
    expires_at: float
    plan: DeletionPlan
    signature: str
    retry: str
    snapshot: DeletionOperation
    replacement_requests: int
    recoveries: dict[str, dict[str, RecoveredCandidate]]
    blocked: dict[str, str]
    invalid_candidates: dict[str, list[str]]
    consumed: bool


class ItemUpdate(TypedDict, total=False):
    state: str
    error: str | None
    jobs: dict[str, str]
    job_ids: list[str]


class PublicDeletionItem(TypedDict):
    id: str
    filename: str
    state: str
    error: str | None
    needed_titles: list[str]
    job_ids: list[str]


class PublicDeletion(TypedDict):
    id: str
    state: str
    can_abandon: bool
    replacement_requests: int
    archived: bool
    items: list[PublicDeletionItem]


class ActivityImageDeletions:
    def __init__(self, hass: HomeAssistant, library: ActivityImageLibrary, queue: ActivityImageQueue, calendar: ActivityCalendarAdapter) -> None:
        self.hass, self.library, self.queue, self.calendar = hass, library, queue, calendar
        self.cache = library.cache
        self.queue.deletion_authorizer = self.async_authorize_replacement
        self._plans: dict[str, SavedPlan] = {}
        self._lock = asyncio.Lock()
        self._task: asyncio.Task[None] | None = None
        self._closed = False
        self._calendar_error: str | None = None
        self.quarantine_root = Path(hass.config.path(".storage", "activity_images_deletions"))

    @asynccontextmanager
    async def _boundaries(self) -> AsyncIterator[None]:
        # Existing call order is calendar -> queue -> generator -> cache. Never invert it.
        async with self.calendar._lock, self.queue._lock, self.queue.engine._lock:
            yield

    def _remember_plan(self, token: str, plan: SavedPlan) -> None:
        now = time.monotonic()
        self._plans = dict(list((key, value) for key, value in self._plans.items()
                                if value["expires_at"] > now)[-63:])
        self._plans[token] = {**plan, "expires_at": now + 600}

    def _get_plan(self, token: str) -> SavedPlan | None:
        plan = self._plans.get(token)
        return plan if plan and plan["expires_at"] > time.monotonic() else None

    def operations(self) -> list[PublicDeletion]:
        return [self.public(operation) for operation in self.cache._deletions.values()
                if not operation.get("archived")]

    @staticmethod
    def public(operation: DeletionOperation) -> PublicDeletion:
        return {"id": operation["id"], "state": operation["state"],
                "can_abandon": any(item["state"] in {"failed", "interrupted"} for item in operation["items"]),
                "replacement_requests": operation["replacement_requests"],
                "archived": bool(operation.get("archived")),
                "items": [{"id": item["id"], "filename": item["filename"], "state": item["state"],
                           "error": item["error"], "needed_titles": deepcopy(item["needed_titles"]),
                           "job_ids": deepcopy(item["job_ids"])} for item in operation["items"]]}

    async def _refresh_demand(self) -> None:
        if not self.calendar.calendars:
            self._calendar_error = None
            return
        previous = self.calendar.last_scan
        try:
            await self.calendar.async_refresh_for_deletion()
        except Exception as exc:
            self._calendar_error = f"Current calendar demand could not be verified: {exc}"
            return
        try:
            stale = self.calendar.coverage_stale() or self.calendar._demand_truncated
        except Exception:
            stale = True
        self._calendar_error = ("Current calendar demand could not be verified; retry after a successful calendar refresh"
                                if self.calendar.last_error or not self.calendar.last_scan or self.calendar.last_scan == previous or stale else None)

    def _demand(self) -> list[str]:
        return sorted({title for titles in self.calendar.calendar_titles.values() for title in titles})

    def _demand_error(self) -> str | None:
        if self._calendar_error:
            return self._calendar_error
        settings = self.queue.engine.settings
        if (tuple(cast(Iterable[str], settings.get(CONF_CALENDARS) or ())) != self.calendar.calendars
                or settings.get(CONF_LOOKAHEAD_DAYS, DEFAULT_LOOKAHEAD_DAYS) != self.calendar.lookahead_days):
            return "Current calendar settings require a successful refresh before deletion"
        if not self.calendar.calendars:
            return None
        if self.calendar.last_error or not self.calendar.last_scan:
            return "Current calendar settings require a successful refresh before deletion"
        try:
            truncated = self.calendar._demand_truncated
            complete = self.calendar.status()["complete"]
        except Exception:
            return "Current calendar demand could not be verified; retry after a successful calendar refresh"
        if truncated:
            return "Calendar demand exceeded 64 distinct titles or 512 events; permanent deletion is blocked"
        if complete is not True:
            return "Current calendar demand is stale or incomplete; retry after a successful complete refresh"
        return None

    def validate_reference(self, media_id: str) -> None:
        """Do not let a Settings save select an original reserved for deletion."""
        reference = unquote(media_id)
        if reference and any(
            filename in reference
            for operation in self.cache._deletions.values()
            for item in operation["items"] if item["state"] not in {"done", "abandoned"}
            for filename in item["files"]
        ):
            raise ValueError("Reference image is reserved for permanent deletion")

    async def _snapshot_plan(self, image_ids: list[str], *, own_operation: str | None = None) -> DeletionPlan:
        if (not isinstance(image_ids, list) or not 1 <= len(image_ids) <= 64
                or any(not isinstance(value, str) or not _ID.fullmatch(value) for value in image_ids)
                or len(set(image_ids)) != len(image_ids)):
            raise ValueError("Select 1–64 distinct valid image IDs")
        await self.library.async_inventory(refresh=True)
        entries = self.cache.entries()
        demand = self._demand()
        resolved = {title: await self.cache.async_find(title, touch=False, prune=False) for title in demand}
        items: list[PlanItem] = []
        for image_id in sorted(image_ids):
            image = self.library._images.get(image_id)
            if image is None:
                raise MappingConflict("Image unavailable; refresh the library")
            files = await self.hass.async_add_executor_job(group_files, self.library.root, list(image["files"].values()), image["filename"])
            variants = set(files)
            if image["filename"].lower().endswith(".png"):
                stem = image["filename"][:-4]
                variants.update(stem + suffix for suffix in ("_thumb.webp", "_qa.webp"))
            elif image["filename"].endswith(("_thumb.webp", "_qa.webp")):
                stem = image["filename"].rsplit("_", 1)[0]
                variants.update(stem + suffix for suffix in (".png", "_thumb.webp", "_qa.webp"))
            urls = sorted(ACTIVITY_IMAGE_PREFIX + quote(filename, safe="") for filename in variants)
            mappings = {key: entry for key, entry in entries.items() if asset_url(entry["image_url"]) in urls}
            needed: dict[str, str] = {}
            for title, url in resolved.items():
                if url and asset_url(url) in urls:
                    needed.setdefault(normalize_title(title), title)
            needed_titles = sorted(needed.values(), key=str.casefold)
            reserved_titles = sorted(set(mappings) | set(needed))
            demand_error = self._demand_error()
            reasons = [demand_error] if demand_error else []
            reference = unquote(cast(str, self.queue.engine.settings.get(CONF_REFERENCE_MEDIA_ID) or ""))
            if any(filename in CANVAS_NORMALIZATION_EXCLUDED_FILES or filename in reference for filename in variants):
                reasons.append("Shared style/reference asset is protected")
            for other_id, other in self.library._images.items():
                if other_id != image_id and variants & set(other["files"].values()):
                    reasons.append("Asset variant is shared with another image")
            if any(self.cache.reserved(title, url, operation_id=own_operation)
                   for title in reserved_titles or [""] for url in urls):
                reasons.append("An unfinished deletion already reserves this image or title")
            for job in self.queue._jobs:
                if job["state"] not in {"pending", "running", "review"}:
                    continue
                if own_operation is not None and job.get("deletion", {}).get("operation_id") == own_operation:
                    continue
                if (normalize_title(job["title"]) in reserved_titles
                        or any(asset_url(value or "") in urls for value in (job.get("original_url"), job.get("result_url")))):
                    reasons.append("Outstanding generation/review job must be resolved first")
            if any(normalize_title(title) in reserved_titles for title in self.queue._pending):
                reasons.append("Pending generation must be resolved first")
            try:
                file_identity = await self.hass.async_add_executor_job(snapshot, self.library.root, files)
            except (OSError, ValueError) as exc:
                reasons.append(f"Cannot safely snapshot files: {exc}")
                file_identity = cast(FileSnapshot, {})  # Blocked plan; never used for cleanup.
            items.append({"id": image_id, "filename": image["filename"], "files": files,
                          "titles": sorted({entry["title"] for entry in mappings.values()}, key=str.casefold),
                          "needed_titles": needed_titles, "safe_without_regeneration": not needed_titles and not reasons,
                          "blocked_reason": "; ".join(sorted(set(reasons))) if reasons else None,
                          "identity": file_identity, "mappings": mappings, "urls": urls,
                          "reserved_titles": reserved_titles,
                          "expected_titles": {key: entries[key]["image_url"] if key in entries else None for key in needed}})
        return {"items": items, "demand": demand, "entries": entries,
                "replacement_requests": sum(len(item["needed_titles"]) for item in items)}

    @staticmethod
    def _signature(plan: DeletionPlan) -> str:
        value = {**plan, "entries": {key: (entry["title"], entry["image_url"]) for key, entry in plan["entries"].items()},
                 "items": [{**item, "mappings": {key: (entry["title"], entry["image_url"])
                                                  for key, entry in item["mappings"].items()}}
                           for item in plan["items"]]}
        return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()

    async def async_plan(self, image_ids: list[str]) -> dict[str, object]:
        await self._refresh_demand()
        async with self._lock, self._boundaries(), self.cache._write_lock:
            if self._closed:
                raise ValueError("Activity Images is unloading")
            plan = await self._snapshot_plan(image_ids)
            token = uuid4().hex
            # Plans are short-lived review capabilities, never a durable authorization.
            self._remember_plan(token, {"plan": plan, "signature": self._signature(plan)})
            result: dict[str, object] = {"token": token, "items": [{"id": item["id"], "filename": item["filename"],
                       "files": item["files"], "titles": item["titles"], "needed_titles": item["needed_titles"],
                       "safe_without_regeneration": item["safe_without_regeneration"], "blocked_reason": item["blocked_reason"]}
                       for item in plan["items"]], "replacement_requests": plan["replacement_requests"],
                      "max_provider_attempts": plan["replacement_requests"] * MAX_ATTEMPTS, "external_warning": EXTERNAL_WARNING}
            if self.calendar.calendars and (self.calendar.last_error or not self.calendar.last_scan):
                result["calendar_warning"] = "Calendar demand is the last available snapshot; unavailable or unseen future consumers are unknown."
            return result

    async def async_delete(self, token: str, regenerate: bool, confirm: bool) -> PublicDeletion:
        if confirm is not True:
            raise ValueError("Permanent deletion requires confirmation")
        existing = next((op for op in self.cache._deletions.values() if op["token"] == token), None)
        if existing:
            return self.public(existing)
        await self._refresh_demand()
        async with self._lock, self._boundaries():
            if self._closed:
                raise ValueError("Activity Images is unloading")
            existing = next((op for op in self.cache._deletions.values() if op["token"] == token), None)
            if existing:
                self._kick()
                return self.public(existing)
            saved = self._get_plan(token)
            if not saved:
                raise MappingConflict("Deletion plan expired; review a fresh plan")
            async with self.cache._write_lock:
                plan = await self._snapshot_plan([item["id"] for item in saved["plan"]["items"]])
                if self._signature(plan) != saved["signature"]:
                    raise MappingConflict("Deletion plan is stale; review a fresh plan")
                if any(item["blocked_reason"] for item in plan["items"]):
                    raise MappingConflict("Deletion is blocked; resolve the displayed reasons first")
                if plan["replacement_requests"] and not regenerate:
                    raise ValueError("Current calendar consumers require explicit replacement authorization")
            operation: DeletionOperation = {"id": uuid4().hex, "token": token, "state": "pending",
                         "items": [cast(DeletionItem, {**item, "state": "pending", "error": None,
                                                        "candidates": {}, "job_ids": [], "jobs": {}})
                                   for item in deepcopy(plan["items"])], "demand": plan["demand"],
                         "replacement_requests": plan["replacement_requests"]}
            await self.cache.async_journal(operation, expected_entries=plan["entries"])
            self._kick()
            return self.public(operation)

    async def _invalid_candidates(self, item: DeletionItem) -> set[str]:
        invalid = set()
        for key, url in item["candidates"].items():
            evidence = item.get("candidate_identities", {}).get(key)
            if (not evidence or self.cache.is_deleted(url)
                    or self.cache.image_metadata(url).get("background_status", "").startswith("review_needed")):
                invalid.add(key)
                continue
            try:
                current = await self.hass.async_add_executor_job(snapshot, self.library.root, list(evidence["files"]))
                if current != evidence:
                    invalid.add(key)
            except (OSError, ValueError):
                invalid.add(key)
        return invalid

    async def _recoverable(self, item: DeletionItem) -> dict[str, RecoveredCandidate]:
        recovered: dict[str, RecoveredCandidate] = {}
        for title in item["needed_titles"]:
            key = normalize_title(title)
            if key in item["candidates"] or key not in item["jobs"]:
                continue
            stem = generation_stem(title, item["jobs"][key])
            files = [stem + suffix for suffix in (".png", "_thumb.webp", "_qa.webp")]
            url = ACTIVITY_IMAGE_PREFIX + stem + ".png"
            if self.cache.image_metadata(url).get("background_status", "").startswith("review_needed"):
                continue
            def verify(files: list[str] = files, url: str = url) -> FileSnapshot:
                for filename in files:
                    data = _read_owned(self.library.root, filename)
                    _verify(data)
                    if filename == files[0] and not self.cache.image_metadata(url).get("background_status"):
                        with Image.open(BytesIO(data)) as image:
                            if not has_meaningful_transparency(image):
                                raise ValueError("Unjournaled image requires manual background review")
                return snapshot(self.library.root, files)
            try:
                evidence = await self.hass.async_add_executor_job(verify)
            except (OSError, ValueError, SyntaxError):
                continue
            recovered[key] = {"url": ACTIVITY_IMAGE_PREFIX + stem + ".png", "identity": evidence}
        return recovered

    async def _validate_original(self, operation: DeletionOperation, item: DeletionItem) -> None:
        demand_error = self._demand_error()
        if demand_error:
            raise MappingConflict(demand_error)
        if self._demand() != operation["demand"]:
            raise MappingConflict("Calendar demand changed; original files and mappings retained")
        entries = self.cache.entries()
        current = {key: entry for key, entry in entries.items() if asset_url(entry["image_url"]) in item["urls"]}
        if self.cache.mapping_identity(current) != self.cache.mapping_identity(item["mappings"]):
            raise MappingConflict("Original associations changed; original files retained")
        if any((entries[key]["image_url"] if key in entries else None) != url for key, url in item["expected_titles"].items()):
            raise MappingConflict("Replacement title changed; original files retained")
        current_files = await self.hass.async_add_executor_job(group_files, self.library.root, item["files"], item["filename"])
        current_identity = await self.hass.async_add_executor_job(snapshot, self.library.root, current_files)
        if current_identity != item["identity"]:
            raise MappingConflict("Original image changed; no files deleted")
        reference = unquote(cast(str, self.queue.engine.settings.get(CONF_REFERENCE_MEDIA_ID) or ""))
        if reference and any(filename in reference for filename in item["files"]):
            raise MappingConflict("Shared style/reference asset is protected")

    async def async_retry_plan(self, operation_id: str) -> dict[str, object]:
        await self._refresh_demand()
        async with self._lock, self._boundaries(), self.cache._write_lock:
            operation = self._operation(operation_id)
            if operation["state"] not in {"failed", "interrupted", "cleanup_failed"}:
                raise ValueError("Operation is not retryable")
            recoveries: dict[str, dict[str, RecoveredCandidate]] = {}
            blocked: dict[str, str] = {}
            invalid_candidates: dict[str, list[str]] = {}
            for item in operation["items"]:
                if item["state"] in {"done", "abandoned", "cleanup", "cleanup_failed"}:
                    continue
                invalid_candidates[item["id"]] = sorted(await self._invalid_candidates(item))
                recoveries[item["id"]] = await self._recoverable(item)
                try:
                    await self._validate_original(operation, item)
                except (OSError, ValueError) as exc:
                    blocked[item["id"]] = str(exc)
            count = sum(len(set(map(normalize_title, item["needed_titles"])) - (set(item["candidates"]) - set(invalid_candidates.get(item["id"], []))) - set(recoveries.get(item["id"], {})))
                        for item in operation["items"] if item["state"] not in {"done", "abandoned", "cleanup", "cleanup_failed"})
            token = uuid4().hex
            self._remember_plan(token, {"retry": operation_id, "snapshot": deepcopy(operation), "replacement_requests": count,
                                        "recoveries": recoveries, "blocked": blocked, "invalid_candidates": invalid_candidates})
            items = []
            for item in operation["items"]:
                needed = [title for title in item["needed_titles"] if (normalize_title(title) not in item["candidates"]
                           or normalize_title(title) in invalid_candidates.get(item["id"], []))
                          and normalize_title(title) not in recoveries.get(item["id"], {})
                          and item["state"] not in {"done", "abandoned", "cleanup", "cleanup_failed"}]
                items.append({"id": item["id"], "filename": item["filename"], "files": deepcopy(item["files"]),
                    "titles": deepcopy(item["titles"]), "state": item["state"], "error": item["error"],
                    "needed_titles": needed, "safe_without_regeneration": not needed and item["id"] not in blocked,
                    "blocked_reason": blocked.get(item["id"]),
                })
            return {"token": token, "operation_id": operation_id, "items": items, "replacement_requests": count,
                    "max_provider_attempts": count * MAX_ATTEMPTS, "external_warning": EXTERNAL_WARNING}

    async def async_retry(self, operation_id: str, token: str, confirm: bool) -> PublicDeletion:
        if confirm is not True:
            raise ValueError("Explicit retry confirmation required")
        if token in self._operation(operation_id).get("retry_tokens", []):
            return self.public(self._operation(operation_id))
        await self._refresh_demand()
        async with self._lock, self._boundaries():
            operation = deepcopy(self._operation(operation_id))
            if token in operation.get("retry_tokens", []):
                return self.public(operation)
            plan = self._get_plan(token)
            if plan and plan.get("consumed") and plan.get("retry") == operation_id:
                return self.public(operation)
            if not plan or plan.get("retry") != operation_id or plan["snapshot"] != operation:
                raise MappingConflict("Retry plan is stale; review outstanding requests again")
            if self._closed:
                raise ValueError("Activity Images is unloading")
            if plan["blocked"]:
                raise MappingConflict("Retry is blocked; original image or calendar demand changed")
            async with self.cache._write_lock:
                for item in operation["items"]:
                    if item["state"] in {"failed", "interrupted"}:
                        await self._validate_original(operation, item)
                        invalid = await self._invalid_candidates(item)
                        if sorted(invalid) != plan["invalid_candidates"].get(item["id"], []):
                            raise MappingConflict("Replacement availability changed; review a new retry plan")
                        recovered = await self._recoverable(item)
                        if recovered != plan["recoveries"].get(item["id"], {}):
                            raise MappingConflict("Retry candidate files changed; review a new retry plan")
                        item["recoveries"] = recovered
                        for key in invalid:
                            item["candidates"].pop(key)
                            item.get("candidate_identities", {}).pop(key, None)
            superseded = [item["jobs"][key] for item in operation["items"]
                          for key in plan["invalid_candidates"].get(item["id"], []) if key in item["jobs"]]
            await self.queue.async_supersede_replacements_locked(superseded)
            for item in operation["items"]:
                if item["state"] in {"failed", "interrupted"}:
                    item.update({"state": "pending", "error": None})
                    item["jobs"] = {key: job_id for key, job_id in item["jobs"].items()
                                    if key in item["candidates"] or key in item.get("recoveries", {})}
                elif item["state"] == "cleanup_failed":
                    item.update({"state": "cleanup", "error": None})
            operation["state"] = "pending"
            operation.setdefault("retry_tokens", []).append(token)
            await self.cache.async_journal(operation)
            plan["consumed"] = True
            self._kick()
            return self.public(operation)

    @staticmethod
    def _state(items: list[DeletionItem]) -> str:
        states = {item["state"] for item in items}
        return ("running" if states & {"running", "pending", "cleanup"} else
                "cleanup_failed" if "cleanup_failed" in states else
                "interrupted" if "interrupted" in states else "failed" if "failed" in states else
                "abandoned" if "abandoned" in states else "done")

    async def async_abandon(self, operation_id: str, confirm: bool) -> PublicDeletion:
        if confirm is not True:
            raise ValueError("Abandonment requires explicit confirmation")
        async with self._lock, self._boundaries():
            if self._closed:
                raise ValueError("Activity Images is unloading")
            operation = deepcopy(self._operation(operation_id))
            selected = {item["id"] for item in operation["items"] if item["state"] in {"failed", "interrupted"}}
            if not selected:
                if any(item["state"] == "abandoned" for item in operation["items"]):
                    return self.public(operation)
                raise MappingConflict("Only failed or interrupted precommit items can be abandoned; committed cleanup must be retried")
            if self._task and not self._task.done():
                raise MappingConflict("Deletion work is still settling; retry abandonment after it stops")
            jobs = [job for job in self.queue._jobs
                    if job.get("deletion", {}).get("operation_id") == operation_id
                    and job["deletion"].get("image_id") in selected]
            if any(job["state"] in {"pending", "running"} or self.queue._current == job["title"] for job in jobs):
                raise MappingConflict("Managed generation is still settling; no abandonment was committed")
            for item in operation["items"]:
                if item["id"] in selected:
                    if any(self.cache.is_deleted(url) for url in item["urls"]):
                        raise MappingConflict("Committed image cleanup cannot be abandoned")
                    item.update({"state": "abandoned", "error": None})
            operation["state"] = self._state(operation["items"])
            before = list(self.queue._jobs)
            values = deepcopy(before)

            async def commit() -> None:
                try:
                    for job in jobs:
                        item = next(item for item in operation["items"] if item["id"] == job["deletion"]["image_id"])
                        job["result_url"] = item["candidates"].get(job["deletion"]["key"], job.get("result_url"))
                        self.queue._set_state(job, "abandoned", "Deletion abandoned; generated files retained in the library")
                    # Separate Stores: retain reservations until terminal history is durable.
                    if jobs:
                        await self.queue._save()
                    await self.cache.async_journal(operation)
                except BaseException:
                    self.queue._jobs = before
                    for job, previous in zip(before, values, strict=True):
                        raw_job = cast(MutableMapping[str, object], job)
                        raw_job.clear()
                        raw_job.update(previous)
                    if jobs:
                        await self.queue._save()
                    raise

            _, cancelled = await async_drain(commit())
            if cancelled:
                raise asyncio.CancelledError
            return self.public(self._operation(operation_id))

    def _operation(self, operation_id: str) -> DeletionOperation:
        if operation_id not in self.cache._deletions:
            raise ValueError("Deletion operation not found")
        return self.cache._deletions[operation_id]

    def _kick(self) -> None:
        if not self._closed and (not self._task or self._task.done()):
            task = self.hass.async_create_task(self._run())
            self._task = task
            task.add_done_callback(self._completed)

    def _completed(self, task: asyncio.Task[None]) -> None:
        if task.cancelled() or self._closed:
            return
        if task.exception() is None and any(item["state"] in {"pending", "cleanup"}
                                            for operation in self.cache._deletions.values() for item in operation["items"]):
            self._kick()

    async def async_setup(self) -> None:
        for operation in list(self.cache._deletions.values()):
            if operation["state"] in {"done", "abandoned"}:
                continue
            operation = deepcopy(operation)
            changed = False
            for item in operation["items"]:
                if item["state"] in {"pending", "running"}:
                    item.update({"state": "interrupted", "error": "Interrupted; explicit retry required. No generation resumed."})
                    changed = True
            if changed:
                operation["state"] = "interrupted"
                await self.cache.async_journal(operation)
        # Only committed, confirmed cleanup may resume on restart.
        self._kick()

    async def _save_item(self, operation_id: str, image_id: str, **values: Unpack[ItemUpdate]) -> None:
        operation = deepcopy(self._operation(operation_id))
        item = next(item for item in operation["items"] if item["id"] == image_id)
        cast(MutableMapping[str, object], item).update(values)
        operation["state"] = self._state(operation["items"])
        await self.cache.async_journal(operation)

    async def _run(self) -> None:
        for operation_id in list(self.cache._deletions):
            for initial in list(self._operation(operation_id)["items"]):
                if self._closed:
                    return
                if initial["state"] not in {"pending", "cleanup"}:
                    continue
                image_id = initial["id"]
                try:
                    if initial["state"] == "pending":
                        await self._save_item(operation_id, image_id, state="running", error=None)
                        await self._replace(operation_id, image_id)
                    await self._cleanup(operation_id, image_id)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    current = next(item for item in self._operation(operation_id)["items"] if item["id"] == image_id)
                    state = "cleanup_failed" if current["state"] == "cleanup" else "failed"
                    try:
                        await self._save_item(operation_id, image_id, state=state, error=f"{type(exc).__name__}: {str(exc)[:300]}")
                    except OSError:
                        # Keep an observable failure in memory; the durable running/cleanup phase is retry-safe.
                        async with self.cache._write_lock:
                            operation = deepcopy(self._operation(operation_id))
                            item = next(item for item in operation["items"] if item["id"] == image_id)
                            item.update({"state": state, "error": "Persistence failed; explicit retry required"})
                            operation["state"] = state
                            self.cache._deletions[operation_id] = operation

    def _item(self, operation_id: str, image_id: str) -> DeletionItem:
        return next(item for item in self._operation(operation_id)["items"] if item["id"] == image_id)

    async def async_authorize_replacement(self, title: str, context: DeletionContext) -> None:
        """Recheck live demand before every attempt, including a 429 retry."""
        operation = self._operation(context["operation_id"])
        item = self._item(operation["id"], context["image_id"])
        key = normalize_title(title)
        def valid() -> bool:
            return (not self._closed and item["state"] == "running"
                    and key == context["key"] and item["jobs"].get(key) == context["job_id"]
                    and key not in item["candidates"])
        if not valid():
            raise GenerationSuppressed("Replacement authorization changed")
        fresh = await self.calendar.async_read_live_demand()
        if fresh != operation["demand"]:
            raise GenerationSuppressed("Calendar demand changed; original files retained")
        await self._validate_original(operation, item)
        if (not valid() or operation is not self._operation(context["operation_id"])):
            raise GenerationSuppressed("Replacement authorization changed while checking demand")

    async def _replace(self, operation_id: str, image_id: str) -> None:
        item = deepcopy(self._item(operation_id, image_id))
        await self._refresh_demand()
        async with self._boundaries(), self.cache._write_lock:
            await self._validate_original(self._operation(operation_id), item)
        for key, recovery in item.get("recoveries", {}).items():
            current = await self.hass.async_add_executor_job(snapshot, self.library.root, list(recovery["identity"]["files"]))
            if current != recovery["identity"]:
                raise MappingConflict("Persisted replacement changed; no provider request sent")
            await self.cache.async_record_candidate(recovery["url"], {"origin": "generated"},
                                                     {"operation_id": operation_id, "image_id": image_id, "key": key})
        item = deepcopy(self._item(operation_id, image_id))
        # Persist allocation before queueing. A successful candidate is journaled by the generator itself.
        for title in item["needed_titles"]:
            key = normalize_title(title)
            if key in item["candidates"]:
                continue
            job_id = item["jobs"].get(key)
            if not job_id:
                job_id = uuid4().hex
                item["jobs"][key] = job_id
                item["job_ids"].append(job_id)
                await self._save_item(operation_id, image_id, jobs=item["jobs"], job_ids=item["job_ids"])
            await self.queue.async_candidate(title, deletion={"operation_id": operation_id, "image_id": image_id, "key": key, "job_id": job_id}, job_id=job_id)
            job = self.queue._job(job_id)
            while not self._closed:
                job = self.queue._job(job_id)
                if job["state"] not in {"pending", "running"}:
                    break
                worker = self.queue._worker
                if worker and worker.done():
                    await worker
                    raise ValueError("Replacement worker stopped before completion")
                await asyncio.sleep(0.05)
            if self._closed:
                raise asyncio.CancelledError
            item = deepcopy(self._item(operation_id, image_id))
            if key not in item["candidates"]:
                raise ValueError(job.get("error") or "Replacement failed; original files retained")
            if self.cache.image_metadata(item["candidates"][key]).get("background_status", "").startswith("review_needed"):
                raise ValueError("Replacement requires manual background review; original retained. Keep originals to link the candidate manually, or explicitly retry.")
        await self._refresh_demand()
        async with self._boundaries():
            await self._validate_original(self._operation(operation_id), item)
            if await self._invalid_candidates(item):
                raise MappingConflict("Persisted replacement changed; original image retained")
            for url in item["candidates"].values():
                await self.library.async_url(self.library.image_id(unquote(asset_url(url).rsplit("/", 1)[-1])))
            await self.cache.async_commit_deletion(operation_id, image_id)
        await self.calendar.async_reconcile()

    async def _cleanup(self, operation_id: str, image_id: str) -> None:
        item = self._item(operation_id, image_id)
        # Queue history is another Store: redact durably before irreversible unlink.
        await self.queue.async_redact_deleted(item["urls"])
        await self.queue.async_finish_replacements(operation_id, image_id)
        _, cancelled = await async_drain(self.hass.async_add_executor_job(
            cleanup, self.library.root, self.quarantine_root, item["identity"], operation_id, image_id))
        self.library.invalidate()
        await self._save_item(operation_id, image_id, state="done", error=None)
        if cancelled:
            raise asyncio.CancelledError

    async def async_shutdown(self) -> None:
        self._closed = True
        if self._task and not self._task.done():
            self._task.cancel()
            with suppress(asyncio.CancelledError):
                await self._task

"""Persistent library of task images with conservative local title matching."""

from __future__ import annotations

import asyncio
import logging
import os
import re
import stat
import time
import unicodedata
from copy import deepcopy
from difflib import SequenceMatcher
from functools import wraps
from pathlib import Path
from urllib.parse import quote, unquote

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store
from PIL import Image

from .const import DOMAIN
from .deletion_files import open_root, validate_filename
from .storage import async_drain, async_save_verified

_LOGGER = logging.getLogger(__name__)

STORAGE_KEY = f"{DOMAIN}_image_cache"
STORAGE_VERSION = 1
CANVAS_NORMALIZATION_VERSION = 2

# High enough to avoid reusing an image merely because two tasks share a common
# action phrase, while still matching small wording changes such as
# "pobranie krwi" versus "badanie krwi".
DEFAULT_SIMILARITY_THRESHOLD = 0.85
DEFAULT_TOKEN_OVERLAP = 0.50
MIN_INDEX_TOKEN_LENGTH = 4
RECOMMENDED_LIBRARY_SIZE = 4000
RECENCY_WRITE_INTERVAL = 3600
DETAILED_DELETION_HISTORY = 100
LOCAL_IMAGE_LOCATIONS = {
    "/local/activity_images/": ("www", "activity_images"),
    "/local/home_tasks/": ("www", "home_tasks"),
}
ACTIVITY_IMAGE_PREFIX = "/local/activity_images/"
CANVAS_NORMALIZATION_EXCLUDED_FILES = {
    "activity-icons-style-board-v1.png",
}


def normalize_title(title: str) -> str:
    """Return a language-independent comparison form for a task title."""
    decomposed = unicodedata.normalize("NFKD", title.casefold())
    without_marks = "".join(
        character for character in decomposed if not unicodedata.combining(character)
    )
    return " ".join(re.findall(r"[^\W_]+", without_marks, flags=re.UNICODE))


def _title_tokens(normalized_title: str) -> set[str]:
    return {
        token
        for token in normalized_title.split()
        if len(token) >= MIN_INDEX_TOKEN_LENGTH
    }


def _canonical_url(image_url: str) -> str:
    """Ignore cache-busting query parameters when comparing local images."""
    return image_url.split("?", 1)[0]


def asset_url(image_url: str) -> str:
    """Canonical owned asset identity, including percent-encoded filenames."""
    canonical = _canonical_url(image_url)
    if canonical.startswith(ACTIVITY_IMAGE_PREFIX):
        return ACTIVITY_IMAGE_PREFIX + quote(unquote(canonical[len(ACTIVITY_IMAGE_PREFIX):]), safe="")
    return canonical


class MappingConflict(ValueError):
    """An exact association changed or replacement was not confirmed."""


def _transaction(method):
    """Serialize mutations and restore memory if persistence fails."""
    @wraps(method)
    async def wrapped(self, *args, **kwargs):
        async with self._write_lock:
            # Entries, metadata and journal values are replaced, never edited in place.
            entries, metadata = self._entries.copy(), self._metadata.copy()
            deletions, tombstones = self._deletions.copy(), self._tombstones.copy()
            generation = self._save_generation
            try:
                return await method(self, *args, **kwargs)
            except BaseException as exc:
                if not isinstance(exc, asyncio.CancelledError) or generation == self._save_generation:
                    self._entries, self._metadata = entries, metadata
                    self._deletions, self._tombstones = deletions, tombstones
                    self._rebuild_token_index()
                raise
    return wrapped


class ActivityImageCache:
    """Store generated images independently from the lifetime of tasks."""

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass
        self._store = Store(hass, STORAGE_VERSION, STORAGE_KEY, atomic_writes=True)
        self._entries: dict[str, dict] = {}
        self._token_index: dict[str, set[str]] = {}
        self._canvas_normalization_version = 0
        self._loaded = False
        self._load_lock = asyncio.Lock()
        self._write_lock = asyncio.Lock()
        self._metadata: dict[str, dict] = {}
        self.image_revision = 0
        self._save_generation = 0
        self._deletions: dict[str, dict] = {}
        self._tombstones: set[str] = set()

    async def async_setup(self) -> None:
        """Load the lightweight title index once."""
        if self._loaded:
            return
        async with self._load_lock:
            if self._loaded:
                return
            data = await self._store.async_load() or {}
            self._deletions = data.get("deletions", {})
            self._tombstones = set(data.get("tombstones", []))
            raw_metadata = data.get("images", {})
            self._metadata = {
                asset_url(url): metadata for url, metadata in raw_metadata.items()
                if isinstance(url, str) and isinstance(metadata, dict)
            } if isinstance(raw_metadata, dict) else {}
            stored_canvas_version = data.get("canvas_normalization_version", 0)
            if isinstance(stored_canvas_version, int):
                self._canvas_normalization_version = stored_canvas_version
            raw_entries = data.get("entries", {})
            if isinstance(raw_entries, dict):
                self._entries = {
                    key: value
                    for key, value in raw_entries.items()
                    if isinstance(key, str)
                    and isinstance(value, dict)
                    and isinstance(value.get("image_url"), str) and value["image_url"]
                }
                for entry in self._entries.values():
                    last_used_at = entry.get("last_used_at", 0)
                    entry["last_used_at"] = (
                        float(last_used_at)
                        if isinstance(last_used_at, (int, float))
                        else 0.0
                    )
            self._rebuild_token_index()
            changed = self._compact_history()
            if self._canvas_normalization_version < CANVAS_NORMALIZATION_VERSION:
                # Preserve published masters and references on upgrade.
                self._canvas_normalization_version = CANVAS_NORMALIZATION_VERSION
                changed = True
            if changed:
                await self._async_save()
            self._loaded = True

    def _activity_image_path(self, canonical_url: str) -> Path | None:
        """Resolve one safe generated activity-image URL to its master file."""
        if not canonical_url.startswith(ACTIVITY_IMAGE_PREFIX):
            return None
        filename = unquote(canonical_url[len(ACTIVITY_IMAGE_PREFIX):])
        if (
            not filename
            or filename != os.path.basename(filename)
            or "\\" in filename or chr(0) in filename
            or not filename.lower().endswith(".png")
            or filename in CANVAS_NORMALIZATION_EXCLUDED_FILES
        ):
            return None
        return Path(self.hass.config.path("www", "activity_images", filename))

    async def async_image_metadata(
        self, images: dict[str, str]
    ) -> dict[str, dict[str, int]]:
        """Return intrinsic dimensions for resolved local activity images."""

        def read_size(path: Path) -> tuple[int, int]:
            directory = open_root(path.parent)
            try:
                fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
                with os.fdopen(fd, "rb") as stream:
                    if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                        raise ValueError("Invalid image file")
                    with Image.open(stream) as image:
                        return image.size
            finally:
                os.close(directory)

        metadata: dict[str, dict[str, int]] = {}
        sizes: dict[Path, tuple[int, int] | None] = {}
        for title, image_url in images.items():
            if not isinstance(image_url, str):
                continue
            path = self._activity_image_path(_canonical_url(image_url))
            if path is None:
                continue
            if path not in sizes:
                try:
                    sizes[path] = await self.hass.async_add_executor_job(read_size, path)
                except (OSError, ValueError, SyntaxError, Image.DecompressionBombError):
                    sizes[path] = None
            if (size := sizes[path]) and size[0] > 0 and size[1] > 0:
                metadata[title] = {"width": size[0], "height": size[1]}
        return metadata

    def _rebuild_token_index(self) -> None:
        self._token_index = {}
        for key, entry in self._entries.items():
            tokens = set(entry.get("tokens") or _title_tokens(key))
            ordered = sorted(tokens)
            if entry.get("tokens") != ordered:
                self._entries[key] = {**entry, "tokens": ordered}
            for token in tokens:
                self._token_index.setdefault(token, set()).add(key)

    @_transaction
    async def async_remember(self, title: str, image_url: str) -> None:
        """Remember one mapping without evicting any other association."""
        key = normalize_title(title)
        self.check_mapping(title, image_url)
        if not key or not image_url:
            return
        self._remember_mapping(title, image_url, key)
        await self._async_save()

    def _remember_mapping(self, title: str, image_url: str, key: str) -> None:
        old = self._entries.get(key)
        if old:
            for token in old.get("tokens", []):
                keys = self._token_index.get(token)
                if keys:
                    keys.discard(key)
                    if not keys:
                        self._token_index.pop(token, None)
        tokens = _title_tokens(key)
        self._entries[key] = {
            "title": title.strip(),
            "image_url": image_url,
            "tokens": sorted(tokens),
            "last_used_at": time.time(),
        }
        for token in tokens:
            self._token_index.setdefault(token, set()).add(key)

    @_transaction
    async def async_remember_many(self, tasks: list[dict]) -> None:
        """Seed the library from already visible task images with one disk write."""
        changed = False
        remembered_at = time.time()
        for task in tasks:
            title = (task.get("title") or "").strip()
            image_url = task.get("image_url")
            key = normalize_title(title)
            if not key or not image_url or self.is_deleted(image_url) or self.reserved(title, image_url):
                continue
            current = self._entries.get(key)
            if current and current.get("image_url") == image_url:
                continue
            self._entries[key] = {
                "title": title,
                "image_url": image_url,
                "tokens": sorted(_title_tokens(key)),
                "last_used_at": remembered_at,
            }
            changed = True
        if changed:
            self._rebuild_token_index()
            await self._async_save()

    async def async_find(self, title: str, *, touch: bool = True, similar: bool = True, prune: bool = True) -> str | None:
        """Find an exact or conservatively similar cached title locally."""
        key = normalize_title(title)
        if not key:
            return None

        exact = self._entries.get(key)
        if exact and await self._async_url_exists(exact["image_url"]):
            if touch:
                await self._async_touch_url(exact["image_url"])
            self._check_lookup(key, exact["image_url"])
            return exact["image_url"]
        if self._entries.get(key) is not exact:
            raise MappingConflict("Title mapping changed during lookup; retry lookup")
        if exact and prune:
            await self._async_forget_keys({key}, expected_url=exact["image_url"])

        if not similar:
            return None
        tokens = _title_tokens(key)
        if not tokens:
            return None
        candidate_keys: set[str] = set()
        for token in tokens:
            candidate_keys.update(self._token_index.get(token, set()))

        best_key: str | None = None
        best_score = DEFAULT_SIMILARITY_THRESHOLD
        for candidate_key in sorted(candidate_keys):
            candidate_tokens = set(
                self._entries[candidate_key].get("tokens")
                or _title_tokens(candidate_key)
            )
            smaller_token_count = min(len(tokens), len(candidate_tokens))
            if not smaller_token_count:
                continue
            overlap = len(tokens & candidate_tokens) / smaller_token_count
            if overlap < DEFAULT_TOKEN_OVERLAP:
                continue
            score = SequenceMatcher(None, key, candidate_key).ratio()
            if score >= best_score:
                best_key = candidate_key
                best_score = score

        if best_key is None:
            return None
        image_url = self._entries[best_key]["image_url"]
        if not await self._async_url_exists(image_url):
            if self._entries.get(best_key, {}).get("image_url") != image_url:
                raise MappingConflict("Image mapping changed during lookup; retry lookup")
            if prune:
                await self._async_forget_keys({best_key}, expected_url=image_url)
            return None

        # A fuzzy lookup updates the canonical image's recency without creating
        # an unbounded title alias for every wording variation.
        if touch:
            await self._async_touch_url(image_url)
        self._check_lookup(best_key, image_url)
        if key != best_key and self._entries.get(key) is not exact and self._entries.get(key) is not None:
            raise MappingConflict("Exact title mapping changed during lookup; retry lookup")
        return image_url

    def _check_lookup(self, key: str, url: str) -> None:
        if self._entries.get(key, {}).get("image_url") != url or self.is_deleted(url):
            raise MappingConflict("Image mapping changed during lookup; retry lookup")

    async def async_find_many(self, titles: list[str]) -> dict[str, str]:
        """Resolve several titles while persisting their LRU touch only once."""
        resolved: dict[str, str] = {}
        touched_urls: set[str] = set()
        for title in titles:
            image_url = await self.async_find(title, touch=False)
            if image_url:
                resolved[title] = image_url
                touched_urls.add(image_url)
        if touched_urls:
            await self._async_touch_urls(touched_urls)
        return resolved

    async def async_forget_url(self, image_url: str | None) -> None:
        """Evict a deliberately rejected image from every cached title."""
        if not image_url:
            return
        await self.async_forget(url=image_url)

    def references_url(self, image_url: str | None) -> bool:
        """Return whether the persistent library intentionally retains a file."""
        if not image_url:
            return False
        canonical = asset_url(image_url)
        return any(
            asset_url(entry.get("image_url", "")) == canonical
            for entry in self._entries.values()
        )

    @_transaction
    async def _async_forget_keys(self, keys: set[str], *, expected_url: str | None = None) -> None:
        if expected_url is not None:
            keys = {key for key in keys if self._entries.get(key, {}).get("image_url") == expected_url}
        keys = {key for key in keys if not self.reserved(key, self._entries.get(key, {}).get("image_url", ""))}
        if not keys:
            return
        for key in keys:
            self._entries.pop(key, None)
        self._rebuild_token_index()
        await self._async_save()

    async def _async_touch_url(self, image_url: str) -> None:
        """Refresh the recency of every title alias for one canonical image."""
        await self._async_touch_urls({image_url})

    async def _async_touch_urls(self, image_urls: set[str]) -> None:
        """Skip snapshots and URL normalization entirely on warm recency reads."""
        canonicals = {asset_url(url) for url in image_urls}
        now = time.time()
        async with self._write_lock:
            touched = {key: entry for key, entry in self._entries.items()
                       if now - entry.get("last_used_at", 0) >= RECENCY_WRITE_INTERVAL
                       and asset_url(entry["image_url"]) in canonicals}
            if not touched:
                return
            generation = self._save_generation
            self._entries.update({key: {**entry, "last_used_at": now} for key, entry in touched.items()})
            try:
                await self._async_save()
            except BaseException as exc:
                if not isinstance(exc, asyncio.CancelledError) or generation == self._save_generation:
                    self._entries.update(touched)
                raise

    async def _async_url_exists(self, image_url: str) -> bool:
        if self.is_deleted(image_url):
            return False
        path = _canonical_url(image_url)
        location = next(
            (
                (prefix, parts)
                for prefix, parts in LOCAL_IMAGE_LOCATIONS.items()
                if path.startswith(prefix)
            ),
            None,
        )
        if location is None:
            return True
        prefix, directory_parts = location
        filename = unquote(path[len(prefix):])
        def exists() -> bool:
            try:
                validate_filename(filename)
                directory = open_root(Path(self.hass.config.path(*directory_parts)))
                try:
                    return stat.S_ISREG(os.stat(filename, dir_fd=directory, follow_symlinks=False).st_mode)
                finally:
                    os.close(directory)
            except (OSError, ValueError):
                return False
        return await self.hass.async_add_executor_job(exists)

    async def async_metadata(self, titles: list[str]) -> dict[str, dict]:
        """Metadata helper for standalone callers."""
        images = await self.async_find_many(titles)
        return await self.async_image_metadata(images)

    @_transaction
    async def async_forget(
        self, *, title: str | None = None, url: str | None = None
    ) -> int:
        """Forget mappings without ever removing an image file."""
        keys = {
            key for key, entry in self._entries.items()
            if (title and key == normalize_title(title))
            or (url and asset_url(entry["image_url"]) == asset_url(url))
        }
        if any(self.reserved(key, self._entries[key]["image_url"]) for key in keys):
            raise MappingConflict("Associations are reserved for permanent deletion")
        if keys:
            for key in keys:
                self._entries.pop(key)
            self._rebuild_token_index()
            await self._async_save()
        return len(keys)

    def entries(self) -> dict[str, dict]:
        """Return a detached snapshot without touching recency."""
        return deepcopy(self._entries)

    def exact(self, title: str) -> str | None:
        """Return the exact URL, including its version, without fuzzy lookup."""
        return self._entries.get(normalize_title(title), {}).get("image_url")

    def image_metadata(self, url: str) -> dict:
        return deepcopy(self._metadata.get(asset_url(url), self._metadata.get(_canonical_url(url), {})))

    @_transaction
    async def async_record_image(self, url: str, metadata: dict, *, title: str | None = None) -> None:
        """Commit provenance and an approved automatic association together."""
        self.check_mapping(title or "", url)
        self._metadata[asset_url(url)] = deepcopy(metadata)
        if title is not None and (key := normalize_title(title)):
            self._remember_mapping(title, url, key)
        await self._async_save()
        self.image_revision += 1

    @_transaction
    async def async_link(
        self, title: str, url: str, *, replace: bool = False,
        expected: str | None = None, compare: bool = False,
    ) -> None:
        self.check_mapping(title, url)
        key = normalize_title(title)
        if not key or not title.strip() or len(title.strip()) > 255:
            raise ValueError("Activity title must contain 1–255 characters and a letter or number")
        current = self.exact(title)
        if compare and current != expected:
            raise MappingConflict("The title mapping changed; refresh before accepting")
        if current and asset_url(current) != asset_url(url) and not replace:
            raise MappingConflict("The title is already linked; confirm replacement")
        self._entries[key] = {
            "title": title.strip(), "image_url": url,
            "tokens": sorted(_title_tokens(key)), "last_used_at": time.time(),
        }
        self._rebuild_token_index()
        await self._async_save()

    @_transaction
    async def async_unlink(self, title: str, url: str, *, expected: str | None = None, compare: bool = False) -> None:
        self.check_mapping(title, url)
        current = self.exact(title)
        if compare and current != expected:
            raise MappingConflict("The title mapping changed; refresh before accepting")
        if current is None or asset_url(current) != asset_url(url):
            raise MappingConflict("The title is no longer linked to this image")
        self._entries.pop(normalize_title(title))
        self._rebuild_token_index()
        await self._async_save()

    @staticmethod
    def mapping_identity(entries: dict) -> dict:
        return {key: (entry["title"], entry["image_url"]) for key, entry in entries.items()}

    def is_deleted(self, url: str) -> bool:
        return asset_url(url) in self._tombstones

    def reserved(self, title: str = "", url: str = "", *, operation_id: str | None = None) -> bool:
        key = normalize_title(title)
        for operation in self._deletions.values():
            if operation["id"] == operation_id or operation["state"] in {"done", "abandoned"}:
                continue
            for item in operation["items"]:
                if item["state"] in {"done", "abandoned"}:
                    continue
                if asset_url(url) in item["urls"] or key in item["reserved_titles"]:
                    return True
        return False

    def check_mapping(self, title: str, url: str) -> None:
        if self.is_deleted(url) or self.reserved(title, url):
            raise MappingConflict("Image or title is reserved for permanent deletion; refresh the library")

    @_transaction
    async def async_journal(self, operation: dict, *, expected_entries: dict | None = None) -> None:
        if expected_entries is not None and self.mapping_identity(self._entries) != self.mapping_identity(expected_entries):
            raise MappingConflict("Library associations changed; review a fresh deletion plan")
        self._deletions[operation["id"]] = deepcopy(operation)
        self._compact_history()
        await self._async_save()

    def _compact_history(self) -> bool:
        """Keep old terminal receipts/idempotency, discard only obsolete recovery evidence."""
        terminal = [key for key, operation in self._deletions.items()
                    if operation["state"] in {"done", "abandoned"} and not operation.get("archived")]
        changed = False
        for key in terminal[:-DETAILED_DELETION_HISTORY]:
            operation = self._deletions[key]
            self._deletions[key] = {
                **{name: operation[name] for name in ("id", "token", "state", "replacement_requests")},
                "retry_tokens": list(operation.get("retry_tokens", [])),
                "archived": True,
                "items": [{name: item[name] for name in ("id", "filename", "state", "error", "needed_titles", "job_ids")}
                          for item in operation["items"]],
            }
            changed = True
        return changed

    @_transaction
    async def async_record_candidate(self, url: str, metadata: dict, context: dict) -> None:
        operation = deepcopy(self._deletions[context["operation_id"]])
        item = next(item for item in operation["items"] if item["id"] == context["image_id"])
        if item["state"] != "running":
            raise MappingConflict("Deletion operation is no longer running")
        from .deletion_files import snapshot
        filename = unquote(asset_url(url)[len(ACTIVITY_IMAGE_PREFIX):])
        stem = filename[:-4]
        root = Path(self.hass.config.path("www", "activity_images"))
        evidence = await self.hass.async_add_executor_job(snapshot, root, [stem + suffix for suffix in (".png", "_thumb.webp", "_qa.webp")])
        item.setdefault("candidate_identities", {})[context["key"]] = evidence
        item["candidates"][context["key"]] = url
        self._metadata[asset_url(url)] = deepcopy(metadata)
        self._deletions[operation["id"]] = operation
        await self._async_save()
        self.image_revision += 1

    @_transaction
    async def async_commit_deletion(self, operation_id: str, image_id: str) -> None:
        operation = deepcopy(self._deletions[operation_id])
        item = next(item for item in operation["items"] if item["id"] == image_id)
        current = {key: entry for key, entry in self._entries.items() if asset_url(entry["image_url"]) in item["urls"]}
        # Recency may legitimately change during reads; associations may not.
        if self.mapping_identity(current) != self.mapping_identity(item["mappings"]):
            raise MappingConflict("Image associations changed; original files retained")
        for title in item["needed_titles"]:
            key = normalize_title(title)
            if key not in item["candidates"]:
                raise MappingConflict("Not all replacements are durable")
            if self._entries.get(key, {}).get("image_url") != item["expected_titles"][key]:
                raise MappingConflict("Replacement title changed; original files retained")
        for key in current:
            self._entries.pop(key)
        for title in item["needed_titles"]:
            key = normalize_title(title)
            self._entries[key] = {"title": title, "image_url": item["candidates"][key],
                                  "tokens": sorted(_title_tokens(key)), "last_used_at": time.time()}
        for url in item["urls"]:
            self._metadata.pop(url, None)
            self._tombstones.add(url)
        item["state"] = "cleanup"
        self._deletions[operation_id] = operation
        self._rebuild_token_index()
        await self._async_save()
        self.image_revision += 1

    async def _async_save(self) -> None:
        _, cancelled = await async_drain(async_save_verified(self.hass, self._store,
            {
                "entries": deepcopy(self._entries),
                "images": deepcopy(self._metadata),
                "canvas_normalization_version": self._canvas_normalization_version,
                "deletions": deepcopy(self._deletions),
                "tombstones": sorted(self._tombstones),
            }
        ))
        self._save_generation += 1
        if cancelled:
            raise asyncio.CancelledError

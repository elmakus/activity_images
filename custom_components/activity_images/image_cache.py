"""Persistent library of task images with conservative local title matching."""

from __future__ import annotations

import asyncio
import logging
import os
import re
import time
import unicodedata
from copy import deepcopy
from difflib import SequenceMatcher
from functools import wraps
from pathlib import Path

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store
from PIL import Image

from .const import DOMAIN
from .generated_image import normalize_existing_generated_image

_LOGGER = logging.getLogger(__name__)

DATA_IMAGE_CACHE = f"{DOMAIN}_image_cache"
STORAGE_KEY = f"{DOMAIN}_image_cache"
STORAGE_VERSION = 1
CANVAS_NORMALIZATION_VERSION = 2

# High enough to avoid reusing an image merely because two tasks share a common
# action phrase, while still matching small wording changes such as
# "pobranie krwi" versus "badanie krwi".
DEFAULT_SIMILARITY_THRESHOLD = 0.85
DEFAULT_TOKEN_OVERLAP = 0.50
MIN_INDEX_TOKEN_LENGTH = 4
MAX_CACHED_IMAGES = 4000
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


class MappingConflict(ValueError):
    """An exact association changed or replacement was not confirmed."""


def _transaction(method):
    """Serialize mutations and restore memory if persistence fails."""
    @wraps(method)
    async def wrapped(self, *args, **kwargs):
        async with self._write_lock:
            entries, metadata = deepcopy(self._entries), deepcopy(self._metadata)
            generation = self._save_generation
            try:
                return await method(self, *args, **kwargs)
            except BaseException as exc:
                if not isinstance(exc, asyncio.CancelledError) or generation == self._save_generation:
                    self._entries, self._metadata = entries, metadata
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

    async def async_setup(self) -> None:
        """Load the lightweight title index once."""
        if self._loaded:
            return
        async with self._load_lock:
            if self._loaded:
                return
            data = await self._store.async_load() or {}
            raw_metadata = data.get("images", {})
            self._metadata = {
                url: metadata for url, metadata in raw_metadata.items()
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
                    and value.get("image_url")
                }
                for entry in self._entries.values():
                    last_used_at = entry.get("last_used_at", 0)
                    entry["last_used_at"] = (
                        float(last_used_at)
                        if isinstance(last_used_at, (int, float))
                        else 0.0
                    )
            self._rebuild_token_index()
            changed = False
            if self._canvas_normalization_version < CANVAS_NORMALIZATION_VERSION:
                await self._async_migrate_activity_image_canvases()
                changed = True
            self._loaded = True
            if changed:
                await self._async_save()

    def _activity_image_path(self, canonical_url: str) -> Path | None:
        """Resolve one safe generated activity-image URL to its master file."""
        if not canonical_url.startswith(ACTIVITY_IMAGE_PREFIX):
            return None
        filename = canonical_url[len(ACTIVITY_IMAGE_PREFIX):]
        if (
            not filename
            or filename != os.path.basename(filename)
            or not filename.lower().endswith(".png")
            or filename in CANVAS_NORMALIZATION_EXCLUDED_FILES
        ):
            return None
        return Path(self.hass.config.path("www", "activity_images", filename))

    def _activity_url_map(self) -> dict[str, str]:
        """Return current versioned URLs for generated masters in the cache."""
        url_map: dict[str, str] = {}
        for entry in self._entries.values():
            image_url = entry.get("image_url")
            if not isinstance(image_url, str):
                continue
            canonical = _canonical_url(image_url)
            if self._activity_image_path(canonical) is not None:
                url_map[canonical] = image_url
        return url_map

    async def async_image_metadata(
        self, images: dict[str, str]
    ) -> dict[str, dict[str, int]]:
        """Return intrinsic dimensions for resolved local activity images."""

        def read_size(path: Path) -> tuple[int, int]:
            with Image.open(path) as image:
                return image.size

        metadata: dict[str, dict[str, int]] = {}
        for title, image_url in images.items():
            if not isinstance(image_url, str):
                continue
            path = self._activity_image_path(_canonical_url(image_url))
            if path is None or not await self.hass.async_add_executor_job(path.is_file):
                continue
            try:
                width, height = await self.hass.async_add_executor_job(read_size, path)
            except OSError:
                continue
            if width > 0 and height > 0:
                metadata[title] = {"width": width, "height": height}
        return metadata

    async def _async_migrate_activity_image_canvases(self) -> None:
        """Normalize cached Gemini masters once and update loaded references."""
        canonical_urls: set[str] = set()
        for entry in self._entries.values():
            image_url = entry.get("image_url")
            if not isinstance(image_url, str):
                continue
            canonical = _canonical_url(image_url)
            if self._activity_image_path(canonical) is not None:
                canonical_urls.add(canonical)
        url_map: dict[str, str] = {}
        for canonical in sorted(canonical_urls):
            path = self._activity_image_path(canonical)
            if path is None or not await self.hass.async_add_executor_job(
                path.is_file
            ):
                continue
            version = await self.hass.async_add_executor_job(
                normalize_existing_generated_image, path
            )
            url_map[canonical] = f"{canonical}?v={version}"

        if url_map:
            for entry in self._entries.values():
                current_url = entry.get("image_url", "")
                replacement = url_map.get(_canonical_url(current_url))
                if replacement:
                    entry["image_url"] = replacement
            await self._async_rewrite_active_references(url_map)

        self._canvas_normalization_version = CANVAS_NORMALIZATION_VERSION
        _LOGGER.info(
            "Normalized %d cached activity-image canvases to version %d",
            len(url_map),
            CANVAS_NORMALIZATION_VERSION,
        )

    async def async_sync_active_references(self) -> None:
        """Bring newly loaded task stores onto the cache's versioned URLs."""
        if self._canvas_normalization_version < CANVAS_NORMALIZATION_VERSION:
            return
        await self._async_rewrite_active_references(self._activity_url_map())

    async def _async_rewrite_active_references(
        self, url_map: dict[str, str]
    ) -> None:
        """Update task references only through their public store APIs."""
        if not url_map:
            return
        for store in self.hass.data.get(DOMAIN, {}).values():
            if hasattr(store, "async_update_task") and hasattr(store, "tasks"):
                updates = [
                    (
                        task.get("id"),
                        url_map.get(_canonical_url(image_url)),
                        image_url,
                    )
                    for task in store.tasks
                    if isinstance((image_url := task.get("image_url")), str)
                ]
                for task_id, replacement, current in updates:
                    if (
                        task_id is not None
                        and replacement
                        and current != replacement
                    ):
                        await store.async_update_task(
                            task_id, image_url=replacement
                        )
            elif hasattr(store, "get_all_overlays") and hasattr(
                store, "async_set_overlay"
            ):
                updates = [
                    (task_uid, url_map.get(_canonical_url(image_url)), image_url)
                    for task_uid, overlay in store.get_all_overlays().items()
                    if isinstance((image_url := overlay.get("image_url")), str)
                ]
                for task_uid, replacement, current in updates:
                    if replacement and current != replacement:
                        await store.async_set_overlay(
                            task_uid, image_url=replacement
                        )

    def _rebuild_token_index(self) -> None:
        self._token_index = {}
        for key, entry in self._entries.items():
            tokens = set(entry.get("tokens") or _title_tokens(key))
            entry["tokens"] = sorted(tokens)
            for token in tokens:
                self._token_index.setdefault(token, set()).add(key)

    @_transaction
    async def async_remember(self, title: str, image_url: str) -> None:
        """Remember one mapping without evicting any other association."""
        key = normalize_title(title)
        if not key or not image_url:
            return
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
        await self._async_save()

    @_transaction
    async def async_remember_many(self, tasks: list[dict]) -> None:
        """Seed the library from already visible task images with one disk write."""
        changed = False
        remembered_at = time.time()
        for task in tasks:
            title = (task.get("title") or "").strip()
            image_url = task.get("image_url")
            key = normalize_title(title)
            if not key or not image_url:
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

    async def async_find(self, title: str, *, touch: bool = True, similar: bool = True) -> str | None:
        """Find an exact or conservatively similar cached title locally."""
        key = normalize_title(title)
        if not key:
            return None

        exact = self._entries.get(key)
        if exact and await self._async_url_exists(exact["image_url"]):
            if touch:
                await self._async_touch_url(exact["image_url"])
            return exact["image_url"]
        if exact:
            await self._async_forget_keys({key})

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
        for candidate_key in candidate_keys:
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
            await self._async_forget_keys({best_key})
            return None

        # A fuzzy lookup updates the canonical image's recency without creating
        # an unbounded title alias for every wording variation.
        if touch:
            await self._async_touch_url(image_url)
        return image_url

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
        canonical = _canonical_url(image_url)
        keys = {
            key
            for key, entry in self._entries.items()
            if _canonical_url(entry.get("image_url", "")) == canonical
        }
        await self._async_forget_keys(keys)

    def references_url(self, image_url: str | None) -> bool:
        """Return whether the persistent library intentionally retains a file."""
        if not image_url:
            return False
        canonical = _canonical_url(image_url)
        return any(
            _canonical_url(entry.get("image_url", "")) == canonical
            for entry in self._entries.values()
        )

    @_transaction
    async def _async_forget_keys(self, keys: set[str]) -> None:
        if not keys:
            return
        for key in keys:
            self._entries.pop(key, None)
        self._rebuild_token_index()
        await self._async_save()

    async def _async_touch_url(self, image_url: str) -> None:
        """Refresh the recency of every title alias for one canonical image."""
        await self._async_touch_urls({image_url})

    @_transaction
    async def _async_touch_urls(self, image_urls: set[str]) -> None:
        """Refresh several canonical images with one storage write."""
        canonicals = {_canonical_url(image_url) for image_url in image_urls}
        touched = False
        now = time.time()
        for entry in self._entries.values():
            if _canonical_url(entry.get("image_url", "")) in canonicals:
                entry["last_used_at"] = now
                touched = True
        if touched:
            await self._async_save()

    async def _async_url_exists(self, image_url: str) -> bool:
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
        filename = path[len(prefix):]
        if not filename or filename != os.path.basename(filename) or "\\" in filename:
            return False
        local_path = self.hass.config.path(*directory_parts, filename)
        return await self.hass.async_add_executor_job(os.path.isfile, local_path)

    async def async_metadata(self, titles: list[str]) -> dict[str, dict]:
        """Metadata helper for standalone callers."""
        images = await self.async_find_many(titles)
        return await self.async_image_metadata(images)

    async def async_forget(
        self, *, title: str | None = None, url: str | None = None
    ) -> int:
        """Forget mappings without ever removing an image file."""
        keys = {
            key for key, entry in self._entries.items()
            if (title and key == normalize_title(title))
            or (url and _canonical_url(entry["image_url"]) == _canonical_url(url))
        }
        await self._async_forget_keys(keys)
        return len(keys)

    def entries(self) -> dict[str, dict]:
        """Return a detached snapshot without touching recency."""
        return deepcopy(self._entries)

    def exact(self, title: str) -> str | None:
        """Return the exact URL, including its version, without fuzzy lookup."""
        return self._entries.get(normalize_title(title), {}).get("image_url")

    def image_metadata(self, url: str) -> dict:
        return deepcopy(self._metadata.get(_canonical_url(url), {}))

    @_transaction
    async def async_record_image(self, url: str, metadata: dict) -> None:
        self._metadata[_canonical_url(url)] = deepcopy(metadata)
        await self._async_save()
        self.image_revision += 1

    @_transaction
    async def async_link(
        self, title: str, url: str, *, replace: bool = False,
        expected: str | None = None, compare: bool = False,
    ) -> None:
        key = normalize_title(title)
        if not key or not title.strip() or len(title.strip()) > 255:
            raise ValueError("Activity title must contain 1–255 characters and a letter or number")
        current = self.exact(title)
        if compare and current != expected:
            raise MappingConflict("The title mapping changed; refresh before accepting")
        if current and _canonical_url(current) != _canonical_url(url) and not replace:
            raise MappingConflict("The title is already linked; confirm replacement")
        self._entries[key] = {
            "title": title.strip(), "image_url": url,
            "tokens": sorted(_title_tokens(key)), "last_used_at": time.time(),
        }
        self._rebuild_token_index()
        await self._async_save()

    @_transaction
    async def async_unlink(self, title: str, url: str, *, expected: str | None = None, compare: bool = False) -> None:
        current = self.exact(title)
        if compare and current != expected:
            raise MappingConflict("The title mapping changed; refresh before accepting")
        if current is None or _canonical_url(current) != _canonical_url(url):
            raise MappingConflict("The title is no longer linked to this image")
        self._entries.pop(normalize_title(title))
        self._rebuild_token_index()
        await self._async_save()

    async def _async_save(self) -> None:
        write = asyncio.create_task(self._store.async_save(
            {
                "entries": deepcopy(self._entries),
                "images": deepcopy(self._metadata),
                "canvas_normalization_version": self._canvas_normalization_version,
            }
        ))
        cancelled = False
        # Cancelling an await cannot stop an executor's atomic file replacement.
        while not write.done():
            try:
                await asyncio.shield(write)
            except asyncio.CancelledError:
                cancelled = True
        await write
        self._save_generation += 1
        if cancelled:
            raise asyncio.CancelledError


async def async_get_cache(hass: HomeAssistant) -> ActivityImageCache:
    """Return the shared cache, loading it lazily for all image entry points."""
    cache = hass.data.get(DATA_IMAGE_CACHE)
    if cache is None:
        cache = ActivityImageCache(hass)
        hass.data[DATA_IMAGE_CACHE] = cache
    await cache.async_setup()
    return cache

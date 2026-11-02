"""Persistent activity image cache, independent from tasks and dashboards.

The normalizer and conservative local matching follow the user's existing
Home Tasks customization. No cloud embedding calls are used for cache lookup.
"""
from __future__ import annotations

import asyncio
from difflib import SequenceMatcher
import logging
from pathlib import Path
import re
import time
import unicodedata

from PIL import Image
from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)
STORAGE_VERSION = 1
STORAGE_KEY = f"{DOMAIN}_image_cache"
MAX_CACHED_IMAGES = 2000
SIMILARITY_THRESHOLD = 0.85
TOKEN_OVERLAP_THRESHOLD = 0.50


def normalize_title(title: str) -> str:
    decomposed = unicodedata.normalize("NFKD", (title or "").casefold())
    plain = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return " ".join(re.findall(r"[^\W_]+", plain, flags=re.UNICODE))


def _tokens(title: str) -> set[str]:
    return {token for token in title.split() if len(token) >= 4}


def _canonical(url: str) -> str:
    return url.split("?", 1)[0]


class ActivityImageCache:
    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass
        self._store = Store(hass, STORAGE_VERSION, STORAGE_KEY)
        self._entries: dict[str, dict] = {}
        self._index: dict[str, set[str]] = {}
        self._load_lock = asyncio.Lock()
        self._loaded = False

    async def async_setup(self) -> None:
        if self._loaded:
            return
        async with self._load_lock:
            if self._loaded:
                return
            stored = await self._store.async_load() or {}
            entries = stored.get("entries") or {}
            if isinstance(entries, dict):
                self._entries = {
                    k: v for k, v in entries.items()
                    if isinstance(k, str) and isinstance(v, dict)
                    and isinstance(v.get("image_url"), str)
                }
                for value in self._entries.values():
                    try:
                        value["last_used_at"] = float(value.get("last_used_at", 0))
                    except (TypeError, ValueError):
                        value["last_used_at"] = 0.0
            self._rebuild_index()
            await self._prune()
            self._loaded = True

    def _rebuild_index(self) -> None:
        self._index = {}
        for title, record in self._entries.items():
            tokens = _tokens(title)
            record["tokens"] = sorted(tokens)
            for token in tokens:
                self._index.setdefault(token, set()).add(title)

    async def _save(self) -> None:
        await self._store.async_save({"entries": self._entries})

    async def _exists(self, url: str) -> bool:
        """Keep original compatibility with legacy Home Tasks and external URLs."""
        path = _canonical(url)
        locations = {
            "/local/activity_images/": ("www", "activity_images"),
            "/local/home_tasks/": ("www", "home_tasks"),
        }
        location = next(
            ((prefix, parts) for prefix, parts in locations.items() if path.startswith(prefix)),
            None,
        )
        if location is None:
            # Original cache accepts non-local URLs (for example externally
            # hosted icons) without making network requests at lookup time.
            return True
        prefix, parts = location
        name = path[len(prefix):]
        if not name or Path(name).name != name or "\\" in name:
            return False
        return await self.hass.async_add_executor_job(
            Path(self.hass.config.path(*parts, name)).is_file
        )

    async def async_find(self, title: str, *, similar: bool = True, touch: bool = True) -> str | None:
        key = normalize_title(title)
        if not key:
            return None
        exact = self._entries.get(key)
        if exact:
            url = exact["image_url"]
            if await self._exists(url):
                if touch:
                    await self._touch([url])
                return url
            self._entries.pop(key, None)
            self._rebuild_index()
            await self._save()
        if not similar:
            return None
        tokens = _tokens(key)
        if not tokens:
            return None
        candidates: set[str] = set()
        for token in tokens:
            candidates.update(self._index.get(token, set()))
        best_key = None
        best_score = SIMILARITY_THRESHOLD
        for candidate in candidates:
            other = set(self._entries[candidate].get("tokens") or _tokens(candidate))
            smaller = min(len(tokens), len(other))
            if not smaller or len(tokens & other) / smaller < TOKEN_OVERLAP_THRESHOLD:
                continue
            score = SequenceMatcher(None, key, candidate).ratio()
            if score >= best_score:
                best_key, best_score = candidate, score
        if best_key is None:
            return None
        url = self._entries[best_key]["image_url"]
        if not await self._exists(url):
            self._entries.pop(best_key, None)
            self._rebuild_index()
            await self._save()
            return None
        if touch:
            await self._touch([url])
        return url

    async def async_find_many(
        self, titles: list[str], *, similar: bool = True
    ) -> dict[str, str]:
        found: dict[str, str] = {}
        for title in titles:
            url = await self.async_find(title, similar=similar, touch=False)
            if url:
                found[title] = url
        if found:
            # Batch last-used updates as the original fork did.
            await self._touch(list(set(found.values())))
        return found

    async def async_remember(self, title: str, url: str) -> None:
        key = normalize_title(title)
        if not key or not url:
            return
        self._entries[key] = {
            "title": title.strip(),
            "image_url": url,
            "last_used_at": time.time(),
            "tokens": sorted(_tokens(key)),
        }
        self._rebuild_index()
        await self._prune()
        await self._save()

    async def async_remember_many(self, pairs: list[dict]) -> None:
        now = time.time()
        changed = False
        for pair in pairs:
            key = normalize_title(pair.get("title", ""))
            url = pair.get("image_url")
            if key and isinstance(url, str) and url:
                self._entries[key] = {
                    "title": pair["title"], "image_url": url,
                    "last_used_at": now, "tokens": sorted(_tokens(key)),
                }
                changed = True
        if changed:
            self._rebuild_index()
            await self._prune()
            await self._save()

    async def async_forget(self, *, title: str | None = None, url: str | None = None) -> int:
        """Forget mappings, never remove files potentially used by other cards."""
        before = len(self._entries)
        if title:
            self._entries.pop(normalize_title(title), None)
        if url:
            canonical = _canonical(url)
            self._entries = {
                k: v for k, v in self._entries.items()
                if _canonical(v.get("image_url", "")) != canonical
            }
        if len(self._entries) != before:
            self._rebuild_index()
            await self._save()
        return before - len(self._entries)

    async def async_image_metadata(self, images: dict[str, str]) -> dict[str, dict[str, int]]:
        """Return image width/height with the original fork's mapping-shaped API."""
        def read_size(path: Path) -> tuple[int, int]:
            with Image.open(path) as im:
                return im.size
        output = {}
        for title, url in images.items():
            canonical = _canonical(url)
            if not canonical.startswith("/local/activity_images/"):
                continue
            filename = canonical.removeprefix("/local/activity_images/")
            if (not filename or Path(filename).name != filename
                    or not filename.lower().endswith(".png")
                    or filename == "activity-icons-style-board-v1.png"):
                continue
            path = Path(self.hass.config.path("www", "activity_images", filename))
            try:
                width, height = await self.hass.async_add_executor_job(read_size, path)
                output[title] = {"width": width, "height": height}
            except OSError:
                continue
        return output

    async def async_metadata(self, titles: list[str]) -> dict[str, dict]:
        """Convenience wrapper retained for standalone callers."""
        images = await self.async_find_many(titles)
        return await self.async_image_metadata(images)

    def references_url(self, url: str | None) -> bool:
        """Retain the original cache's canonical-URL reference check."""
        if not url:
            return False
        return any(
            _canonical(entry.get("image_url", "")) == _canonical(url)
            for entry in self._entries.values()
        )

    async def async_forget_url(self, image_url: str | None) -> None:
        """Preserve original API for deliberately rejecting one image."""
        if image_url:
            await self.async_forget(url=image_url)

    async def _touch(self, urls: list[str]) -> None:
        keys = {_canonical(u) for u in urls}
        now = time.time()
        for entry in self._entries.values():
            if _canonical(entry["image_url"]) in keys:
                entry["last_used_at"] = now
        await self._save()

    async def _prune(self) -> None:
        unique: dict[str, float] = {}
        for v in self._entries.values():
            url = _canonical(v["image_url"])
            unique[url] = max(unique.get(url, 0), v.get("last_used_at", 0))
        overflow = len(unique) - MAX_CACHED_IMAGES
        if overflow <= 0:
            return
        drop = {
            url for url, _ in sorted(unique.items(), key=lambda kv: (kv[1], kv[0]))[:overflow]
        }
        self._entries = {
            k: v for k, v in self._entries.items()
            if _canonical(v["image_url"]) not in drop
        }
        self._rebuild_index()
        # Deliberately preserve physical files: external clients may hold their URLs.
        await self._save()


async def async_get_cache(hass: HomeAssistant) -> ActivityImageCache:
    key = f"{DOMAIN}_image_cache_instance"
    cache = hass.data.get(key)
    if cache is None:
        cache = ActivityImageCache(hass)
        hass.data[key] = cache
    await cache.async_setup()
    return cache

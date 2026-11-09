"""Executor-backed inventory of the integration-owned image directory."""
from __future__ import annotations

import asyncio
import hashlib
import os
import stat
import time
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path

from PIL import Image

from .image_cache import ACTIVITY_IMAGE_PREFIX, ActivityImageCache, normalize_title

IMAGE_API = "/api/activity_images/library"
_EXTENSIONS = {".png", ".webp", ".jpg", ".jpeg", ".gif"}


def validate_title(title: str) -> str:
    title = title.strip()
    if not title or len(title) > 255 or not normalize_title(title):
        raise ValueError("Activity title must contain 1–255 characters and a letter or number")
    return title


def _read_owned(root: Path, filename: str) -> bytes:
    """Open relative to the owned directory; never follow a replaced symlink."""
    if Path(filename).name != filename or filename.startswith(".") or "\\" in filename:
        raise ValueError("Invalid image asset")
    directory = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        fd = os.open(filename, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
        with os.fdopen(fd, "rb") as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                raise ValueError("Invalid image asset")
            return stream.read()
    finally:
        os.close(directory)


def _verify(data: bytes) -> tuple[int, int, str]:
    with Image.open(BytesIO(data)) as image:
        width, height = image.size
        format_name = image.format
        image.verify()
    with Image.open(BytesIO(data)) as decoded:
        decoded.load()
    if format_name not in {"PNG", "WEBP", "JPEG", "GIF"} or width <= 0 or height <= 0:
        raise ValueError("Unsupported image")
    return width, height, format_name


class ActivityImageLibrary:
    def __init__(self, hass, cache: ActivityImageCache) -> None:
        self.hass = hass
        self.cache = cache
        self.root = Path(hass.config.path("www", "activity_images"))
        self._verified: dict[str, tuple[tuple, dict | None]] = {}
        self._images: dict[str, dict] = {}
        self._storage_bytes = 0
        self._scanned_at = 0.0
        self._image_revision = -1
        self._scan_lock = asyncio.Lock()

    @staticmethod
    def image_id(filename: str) -> str:
        return hashlib.sha256(filename.encode()).hexdigest()[:32]

    def _scan(self) -> tuple[dict, int]:
        valid = {}
        fingerprints = {}
        storage_bytes = 0
        if not self.root.exists():
            self._verified = {}
            return {}, 0
        if self.root.is_symlink() or not self.root.is_dir():
            raise ValueError("The image directory must not be a symlink")
        with os.scandir(self.root) as entries:
            for entry in entries:
                try:
                    info = entry.stat(follow_symlinks=False)
                    if not stat.S_ISREG(info.st_mode):
                        continue
                    storage_bytes += info.st_size
                    if Path(entry.name).suffix.lower() not in _EXTENSIONS or entry.name.startswith("."):
                        continue
                    fingerprint = (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)
                    previous = self._verified.get(entry.name)
                    if previous and previous[0] == fingerprint:
                        details = previous[1]
                    else:
                        try:
                            width, height, format_name = _verify(_read_owned(self.root, entry.name))
                            details = {
                                "width": width, "height": height, "format": format_name,
                                "modified_at": datetime.fromtimestamp(info.st_mtime, UTC).isoformat(),
                            }
                        except (OSError, ValueError, Image.DecompressionBombError, SyntaxError):
                            details = None
                    fingerprints[entry.name] = (fingerprint, details)
                    if details:
                        valid[entry.name] = details
                except OSError:
                    continue
        self._verified = fingerprints
        grouped = {}
        variants = set()
        for filename in valid:
            if filename.lower().endswith(".png"):
                stem = filename[:-4]
                assets = {"master": filename}
                for kind, suffix in (("thumbnail", "_thumb.webp"), ("qa", "_qa.webp")):
                    if stem + suffix in valid:
                        assets[kind] = stem + suffix
                        variants.add(stem + suffix)
                grouped[filename] = assets
        for filename in sorted(valid.keys() - variants - grouped.keys()):
            if filename in variants:
                continue
            files = {"master": filename}
            if filename.endswith("_qa.webp") and filename[:-8] + "_thumb.webp" in valid:
                files["thumbnail"] = filename[:-8] + "_thumb.webp"
                variants.add(files["thumbnail"])
            grouped[filename] = files
        return {
            self.image_id(filename): {"filename": filename, **valid[filename], "files": files}
            for filename, files in grouped.items()
        }, storage_bytes

    async def async_inventory(self, *, refresh: bool = False) -> None:
        async with self._scan_lock:
            if not refresh and self._scanned_at and self._image_revision == self.cache.image_revision and time.monotonic() - self._scanned_at < 15:
                return
            revision = self.cache.image_revision
            images, size = await self.hass.async_add_executor_job(self._scan)
            self._images, self._storage_bytes = images, size
            self._scanned_at = time.monotonic()
            self._image_revision = revision

    def invalidate(self) -> None:
        self._scanned_at = 0

    @staticmethod
    def _titles(entries: dict) -> dict[str, list[str]]:
        titles: dict[str, list[str]] = {}
        for key, entry in entries.items():
            titles.setdefault(entry["image_url"].split("?", 1)[0], []).append(entry.get("title", key))
        return titles

    def _item(self, image_id: str, titles_by_url: dict) -> dict:
        image = self._images[image_id]
        filename = image["filename"]
        url = ACTIVITY_IMAGE_PREFIX + filename
        titles = sorted(set(titles_by_url.get(url, [])), key=str.casefold)
        metadata = self.cache.image_metadata(url)
        assets = {kind: f"{IMAGE_API}/{image_id}/{kind}" for kind in image["files"]}
        return {
            "id": image_id, "filename": filename,
            "title": titles[0] if titles else Path(filename).stem,
            "titles": titles, "linked": bool(titles),
            "origin": "generated" if metadata.get("origin") == "generated" else "unknown",
            "image_url": assets["master"],
            "thumbnail_url": assets.get("thumbnail", assets["master"]),
        }

    async def async_list(self, *, query="", filter="all", offset=0, limit=48, refresh=False) -> dict:
        if filter not in {"all", "linked", "unlinked", "generated"} or not 1 <= limit <= 96 or offset < 0 or len(query) > 255:
            raise ValueError("Invalid library query")
        await self.async_inventory(refresh=refresh)
        entries = self.cache.entries()
        titles = self._titles(entries)
        items = [self._item(image_id, titles) for image_id in self._images]
        linked = sum(item["linked"] for item in items)
        stats = {
            "images": len(items), "linked": linked, "unlinked": len(items) - linked,
            "mappings": len(entries), "storage_bytes": self._storage_bytes, "supported_images": 4000,
        }
        search = query.casefold().strip()
        items = [item for item in items if (
            filter == "all" or (filter == "linked" and item["linked"])
            or (filter == "unlinked" and not item["linked"])
            or (filter == "generated" and item["origin"] == "generated")
        ) and (not search or search in " ".join([item["filename"], *item["titles"]]).casefold())]
        items.sort(key=lambda item: (item["title"].casefold(), item["filename"]))
        return {"items": items[offset:offset + limit], "total": len(items), "offset": offset,
                "limit": limit, "stats": stats, "filters": ["all", "linked", "unlinked", "generated"]}

    async def async_get(self, image_id: str) -> dict:
        await self.async_inventory()
        if image_id not in self._images:
            await self.async_inventory(refresh=True)
        if image_id not in self._images:
            raise ValueError("Image not found; refresh the library")
        image = self._images[image_id]
        item = self._item(image_id, self._titles(self.cache.entries()))
        metadata = self.cache.image_metadata(ACTIVITY_IMAGE_PREFIX + image["filename"])
        return {**item, **{key: image[key] for key in ("width", "height", "format", "modified_at")},
                "created_at": metadata.get("created_at"),
                "assets": {kind: f"{IMAGE_API}/{image_id}/{kind}" for kind in image["files"]}}

    async def async_url(self, image_id: str) -> str:
        item = await self.async_get(image_id)
        # Verify at the mutation boundary too, not only at inventory time.
        await self.async_read(image_id, "master")
        return ACTIVITY_IMAGE_PREFIX + item["filename"]

    async def async_read(self, image_id: str, asset: str) -> tuple[bytes, str]:
        await self.async_inventory()
        image = self._images.get(image_id)
        if image is None:
            await self.async_inventory(refresh=True)
            image = self._images.get(image_id)
        if image is None or asset not in image["files"]:
            raise ValueError("Image asset not found")
        def read():
            data = _read_owned(self.root, image["files"][asset])
            try:
                _, _, format_name = _verify(data)
            except (OSError, SyntaxError, Image.DecompressionBombError) as exc:
                raise ValueError("Invalid image asset") from exc
            return data, {"PNG": "image/png", "WEBP": "image/webp", "JPEG": "image/jpeg", "GIF": "image/gif"}[format_name]
        return await self.hass.async_add_executor_job(read)

    async def async_generate(self, queue, title: str, image_id: str | None = None) -> dict:
        title = validate_title(title)
        if image_id:
            item = await self.async_get(image_id)
            if item["linked"] and normalize_title(title) not in {normalize_title(t) for t in item["titles"]}:
                raise ValueError("Select an exact associated title for regeneration")
        return await queue.async_candidate(title)

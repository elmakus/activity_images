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
from urllib.parse import quote
from collections.abc import Mapping
from typing import TYPE_CHECKING, TypedDict

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from .image_queue import ActivityImageQueue, PublicJob

from PIL import Image

from .deletion_files import open_root, validate_filename
from .generated_image import MAX_IMAGE_BYTES
from .image_cache import ACTIVITY_IMAGE_PREFIX, RECOMMENDED_LIBRARY_SIZE, ActivityImageCache, MappingEntry, asset_url, normalize_title
from .image_processing import MAX_IMAGE_PIXELS

IMAGE_API = "/api/activity_images/library"
_EXTENSIONS = {".png", ".webp", ".jpg", ".jpeg", ".gif"}


def validate_title(title: str) -> str:
    title = title.strip()
    if not title or len(title) > 255 or not normalize_title(title):
        raise ValueError("Activity title must contain 1–255 characters and a letter or number")
    return title


def _read_owned(root: Path, filename: str) -> bytes:
    """Open relative to the owned directory; never follow a replaced symlink."""
    validate_filename(filename)
    directory = open_root(root)
    try:
        fd = os.open(filename, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
        with os.fdopen(fd, "rb") as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_IMAGE_BYTES:
                raise ValueError("Invalid or oversized image asset (maximum 20 MiB)")
            data = stream.read(MAX_IMAGE_BYTES + 1)
            if len(data) > MAX_IMAGE_BYTES:
                raise ValueError("Image asset exceeds 20 MiB")
            return data
    finally:
        os.close(directory)


def _verify(data: bytes, *, decode: bool = True) -> tuple[int, int, str]:
    with Image.open(BytesIO(data)) as image:
        width, height = image.size
        format_name = image.format
        if format_name not in {"PNG", "WEBP", "JPEG", "GIF"} or width <= 0 or height <= 0:
            raise ValueError("Unsupported image")
        if width * height > MAX_IMAGE_PIXELS:
            raise ValueError("Image exceeds 25 million pixels")
        image.verify()
    if decode:
        with Image.open(BytesIO(data)) as decoded:
            decoded.load()
    return width, height, format_name


class ImageDetails(TypedDict):
    width: int
    height: int
    format: str
    modified_at: str


class InventoryImage(ImageDetails):
    filename: str
    files: dict[str, str]


class LibraryItem(TypedDict):
    id: str
    filename: str
    title: str
    titles: list[str]
    linked: bool
    origin: str
    background_status: str | None
    image_url: str
    thumbnail_url: str


class LibraryDetails(LibraryItem, ImageDetails):
    created_at: str | None
    assets: dict[str, str]


class LibraryPage(TypedDict):
    items: list[LibraryItem]
    total: int
    offset: int
    limit: int
    stats: dict[str, int]
    filters: list[str]


type Fingerprint = tuple[int, int, int, int, int]


class ActivityImageLibrary:
    def __init__(self, hass: HomeAssistant, cache: ActivityImageCache) -> None:
        self.hass = hass
        self.cache = cache
        self.root = Path(hass.config.path("www", "activity_images"))
        self._verified: dict[str, tuple[Fingerprint, ImageDetails | None]] = {}
        self._images: dict[str, InventoryImage] = {}
        self._storage_bytes = 0
        self._scanned_at = 0.0
        self._image_revision = -1
        self._scan_lock = asyncio.Lock()

    @staticmethod
    def image_id(filename: str) -> str:
        return hashlib.sha256(filename.encode()).hexdigest()[:32]

    def _scan(self) -> tuple[dict[str, InventoryImage], int]:
        if not self.root.exists():
            self._verified = {}
            return {}, 0
        if self.root.is_symlink() or not self.root.is_dir():
            raise ValueError("The image directory must not be a symlink")
        directory = open_root(self.root)
        try:
            return self._scan_directory(directory)
        finally:
            os.close(directory)

    def _scan_directory(self, directory: int) -> tuple[dict[str, InventoryImage], int]:
        valid: dict[str, ImageDetails] = {}
        fingerprints: dict[str, tuple[Fingerprint, ImageDetails | None]] = {}
        storage_bytes = 0
        with os.scandir(directory) as entries:
            for entry in entries:
                try:
                    info = entry.stat(follow_symlinks=False)
                    if not stat.S_ISREG(info.st_mode):
                        continue
                    storage_bytes += info.st_size
                    if (Path(entry.name).suffix.lower() not in _EXTENSIONS or entry.name.startswith(".")
                            or self.cache.is_deleted(ACTIVITY_IMAGE_PREFIX + quote(entry.name, safe=""))):
                        continue
                    fingerprint = (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)
                    previous = self._verified.get(entry.name)
                    if previous and previous[0] == fingerprint:
                        details = previous[1]
                    else:
                        try:
                            # Decode only requested previews, not every pixel in the library.
                            width, height, format_name = _verify(_read_owned(self.root, entry.name), decode=False)
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
        grouped: dict[str, dict[str, str]] = {}
        variants: set[str] = set()
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
    def _titles(entries: Mapping[str, MappingEntry]) -> dict[str, list[str]]:
        titles: dict[str, list[str]] = {}
        for key, entry in entries.items():
            titles.setdefault(asset_url(entry["image_url"]), []).append(entry.get("title", key))
        return titles

    def _item(self, image_id: str, titles_by_url: Mapping[str, list[str]]) -> LibraryItem:
        image = self._images[image_id]
        filename = image["filename"]
        url = ACTIVITY_IMAGE_PREFIX + quote(filename, safe="")
        titles = sorted(set(titles_by_url.get(url, [])), key=str.casefold)
        metadata = self.cache.image_metadata(url)
        assets = {kind: f"{IMAGE_API}/{image_id}/{kind}" for kind in image["files"]}
        return {
            "id": image_id, "filename": filename,
            "title": titles[0] if titles else Path(filename).stem,
            "titles": titles, "linked": bool(titles),
            "origin": "generated" if metadata.get("origin") == "generated" else "unknown",
            "background_status": metadata.get("background_status"),
            "image_url": assets["master"],
            "thumbnail_url": assets.get("thumbnail", assets["master"]),
        }

    async def async_list(self, *, query: str = "", filter: str = "all", offset: int = 0, limit: int = 48, refresh: bool = False) -> LibraryPage:
        if filter not in {"all", "linked", "unlinked", "generated"} or not 1 <= limit <= 96 or offset < 0 or len(query) > 255:
            raise ValueError("Invalid library query")
        await self.async_inventory(refresh=refresh)
        entries = self.cache.entries()
        titles = self._titles(entries)
        items = [self._item(image_id, titles) for image_id in self._images]
        linked = sum(item["linked"] for item in items)
        stats = {
            "images": len(items), "linked": linked, "unlinked": len(items) - linked,
            "mappings": len(entries), "storage_bytes": self._storage_bytes, "supported_images": RECOMMENDED_LIBRARY_SIZE,
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

    async def async_get(self, image_id: str) -> LibraryDetails:
        await self.async_inventory()
        if image_id not in self._images:
            await self.async_inventory(refresh=True)
        if image_id not in self._images:
            raise ValueError("Image not found; refresh the library")
        image = self._images[image_id]
        item = self._item(image_id, self._titles(self.cache.entries()))
        metadata = self.cache.image_metadata(ACTIVITY_IMAGE_PREFIX + quote(image["filename"], safe=""))
        return {**item, "width": image["width"], "height": image["height"],
                "format": image["format"], "modified_at": image["modified_at"],
                "created_at": metadata.get("created_at"),
                "assets": {kind: f"{IMAGE_API}/{image_id}/{kind}" for kind in image["files"]}}

    async def async_url(self, image_id: str) -> str:
        item = await self.async_get(image_id)
        # Verify at the mutation boundary too, not only at inventory time.
        await self.async_read(image_id, "master")
        return ACTIVITY_IMAGE_PREFIX + quote(item["filename"], safe="")

    async def async_read(self, image_id: str, asset: str) -> tuple[bytes, str]:
        await self.async_inventory()
        image = self._images.get(image_id)
        if image is None:
            await self.async_inventory(refresh=True)
            image = self._images.get(image_id)
        if image is None or (asset != "preview" and asset not in image["files"]):
            raise ValueError("Image asset not found")
        def read() -> tuple[bytes, str]:
            data = _read_owned(self.root, image["files"]["master" if asset == "preview" else asset])
            try:
                _, _, format_name = _verify(data)
            except (OSError, SyntaxError, Image.DecompressionBombError) as exc:
                raise ValueError("Invalid image asset") from exc
            if asset == "preview":
                with Image.open(BytesIO(data)) as original:
                    preview = original.convert("RGBA")
                    preview.thumbnail((256, 256), Image.Resampling.LANCZOS)
                    output = BytesIO()
                    preview.save(output, format="PNG")
                return output.getvalue(), "image/png"
            return data, {"PNG": "image/png", "WEBP": "image/webp", "JPEG": "image/jpeg", "GIF": "image/gif"}[format_name]
        return await self.hass.async_add_executor_job(read)

    async def async_generate(self, queue: ActivityImageQueue, title: str, image_id: str | None = None) -> PublicJob:
        title = validate_title(title)
        if image_id:
            item = await self.async_get(image_id)
            if item["linked"] and normalize_title(title) not in {normalize_title(t) for t in item["titles"]}:
                raise ValueError("Select an exact associated title for regeneration")
        return await queue.async_candidate(title)

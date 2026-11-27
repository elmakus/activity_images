"""Validate and persist native transparent AI-generated images."""
from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import cast
import os
import stat
import tempfile

from PIL import Image
from homeassistant.components.media_source import async_resolve_media
from homeassistant.core import HomeAssistant

from .image_processing import (
    create_theme_qa_preview,
    has_meaningful_transparency,
    validate_dimensions,
    normalize_icon_canvas,
)

MAX_IMAGE_BYTES = 20 * 1024 * 1024
PUBLIC_DIRECTORY = "activity_images"
PUBLIC_PREFIX = "/local/activity_images/"


class ImageTransparencyError(ValueError):
    """Provider output violates the native transparency contract."""

    def __init__(self, message: str, *, translation_key: str) -> None:
        super().__init__(message)
        self.translation_key = translation_key


@dataclass(slots=True)
class SavedGeneratedImage:
    image_url: str
    background_status: str
    qa_url: str


def _read_local_source(path: Path) -> bytes:
    with os.fdopen(os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW), "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_IMAGE_BYTES:
            raise ValueError("Image source must be a regular file of at most 20 MiB")
        data = stream.read(MAX_IMAGE_BYTES + 1)
    if not data or len(data) > MAX_IMAGE_BYTES:
        raise ValueError("Image exceeds size limit or is empty")
    return data


async def _read_source(hass: HomeAssistant, source: str) -> bytes:
    """Use the native AI Task local media result; never fetch provider-supplied URLs."""
    if not source.startswith("media-source://"):
        raise ValueError("AI Task must return a local media-source image ID")
    resolved = await async_resolve_media(hass, source, None)
    if not resolved.path:
        raise ValueError("AI Task media source must resolve to a local file")
    return await hass.async_add_executor_job(_read_local_source, Path(resolved.path))


def _atomic_save(image: Image.Image, destination: Path, format: str, **kw: object) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    name = None
    try:
        with tempfile.NamedTemporaryFile(dir=destination.parent, delete=False) as temp:
            name = temp.name
        image.save(name, format=format, **kw)
        os.chmod(name, 0o644)
        os.replace(name, destination)
    finally:
        if name and os.path.exists(name):
            os.unlink(name)


def _process(data: bytes, target: Path) -> None:
    with Image.open(BytesIO(data)) as source:
        validate_dimensions(source)
        if not has_meaningful_transparency(source):
            raise ImageTransparencyError("Image provider returned an image without a transparent background; check provider settings before retrying", translation_key="opaque_image")
        rgba = source.convert("RGBA")
        # RGBA's alpha channel is a single-band 8-bit image, not a multiband extrema tuple.
        alpha_extrema = cast(tuple[int, int], rgba.getchannel("A").getextrema())
        if alpha_extrema[1] <= 8:
            raise ImageTransparencyError("Image provider returned an entirely transparent image; check provider settings before retrying", translation_key="empty_image")
        normalized = normalize_icon_canvas(rgba)
    preview = create_theme_qa_preview(normalized)
    thumb = normalized.copy()
    thumb.thumbnail((512, 512), Image.Resampling.LANCZOS)
    target.parent.mkdir(parents=True, exist_ok=True)
    # Encode every variant before publishing; the master appears last.
    with tempfile.TemporaryDirectory(dir=target.parent) as directory:
        staged = Path(directory)
        qa = target.with_name(target.stem + "_qa.webp")
        thumbnail = target.with_name(target.stem + "_thumb.webp")
        _atomic_save(normalized, staged / target.name, "PNG", optimize=True)
        _atomic_save(preview, staged / qa.name, "WEBP", quality=86)
        _atomic_save(thumb, staged / thumbnail.name, "WEBP", quality=82)
        published = []
        try:
            for destination in (qa, thumbnail, target):
                os.link(staged / destination.name, destination)
                published.append(destination)
        except BaseException:
            for destination in reversed(published):
                try:
                    if os.path.samestat(destination.lstat(), (staged / destination.name).stat()):
                        destination.unlink()
                except FileNotFoundError:
                    pass
            raise


def normalize_existing_generated_image(destination: Path) -> int:
    """Retain the original fork's explicit re-normalization/migration helper.

    Never called automatically at startup: other dashboards may still rely on
    existing assets. The future opt-in importer may invoke this on copied files.
    """
    with Image.open(destination) as image:
        normalized = normalize_icon_canvas(image)
    _atomic_save(normalized, destination, "PNG", optimize=True)
    qa = create_theme_qa_preview(normalized)
    _atomic_save(qa, destination.with_name(destination.stem + "_qa.webp"), "WEBP", quality=86)
    thumb = normalized.copy()
    thumb.thumbnail((512, 512), Image.Resampling.LANCZOS)
    _atomic_save(
        thumb, destination.with_name(destination.stem + "_thumb.webp"),
        "WEBP", quality=82,
    )
    return destination.stat().st_mtime_ns


async def async_save_generated_image(
    hass: HomeAssistant, image_url: str, filename_stem: str,
) -> SavedGeneratedImage:
    """Save a 512px-height transparent PNG, thumbnail and theme QA preview."""
    if not filename_stem or filename_stem != os.path.basename(filename_stem):
        raise ValueError("Unsafe image name")
    data = await _read_source(hass, image_url)
    target = Path(hass.config.path("www", PUBLIC_DIRECTORY, filename_stem + ".png"))
    await hass.async_add_executor_job(_process, data, target)
    return SavedGeneratedImage(
        image_url=PUBLIC_PREFIX + target.name,
        background_status="existing_alpha",
        qa_url=PUBLIC_PREFIX + filename_stem + "_qa.webp",
    )

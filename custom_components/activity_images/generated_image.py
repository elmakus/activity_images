"""Persist AI-generated images with optional Lucida background removal.

Refactored from the MIT-licensed Home Tasks image pipeline. The background
remover address is provided by the user, never embedded in distributable code.
"""
from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
import logging
import os
import stat
import tempfile

import aiohttp
from PIL import Image
from homeassistant.components.media_source import async_resolve_media
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .image_processing import (
    create_theme_qa_preview,
    has_meaningful_transparency,
    validate_dimensions,
    normalize_icon_canvas,
    normalize_generated_image,
)

_LOGGER = logging.getLogger(__name__)
MAX_IMAGE_BYTES = 20 * 1024 * 1024
PUBLIC_DIRECTORY = "activity_images"
PUBLIC_PREFIX = "/local/activity_images/"
LUCIDA_TIMEOUT = aiohttp.ClientTimeout(total=45, connect=5)


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


def _already_transparent(data: bytes) -> bool:
    with Image.open(BytesIO(data)) as image:
        return has_meaningful_transparency(image)


def _validate_lucida(data: bytes) -> bytes:
    if not data or len(data) > MAX_IMAGE_BYTES:
        raise ValueError("Invalid Lucida response size")
    with Image.open(BytesIO(data)) as image:
        validate_dimensions(image)
        if image.format != "PNG" or image.mode != "RGBA":
            raise ValueError("Lucida did not return an RGBA PNG")
        image.load()
        if not has_meaningful_transparency(image) or image.getchannel("A").getextrema()[1] <= 8:
            raise ValueError("Lucida did not return a visible transparent subject")
    return data


async def _maybe_remove_background(
    hass: HomeAssistant, data: bytes, lucida_url: str | None
) -> bytes:
    if not lucida_url or await hass.async_add_executor_job(_already_transparent, data):
        return data

    def input_format() -> tuple[str, str]:
        with Image.open(BytesIO(data)) as image:
            validate_dimensions(image)
            formats = {"PNG": ("png", "image/png"), "JPEG": ("jpg", "image/jpeg"),
                       "WEBP": ("webp", "image/webp"), "GIF": ("gif", "image/gif")}
            if image.format not in formats:
                raise ValueError("Unsupported image source format")
            image.verify()
            return formats[image.format]

    extension, mime = await hass.async_add_executor_job(input_format)
    try:
        form = aiohttp.FormData()
        form.add_field("file", data, filename=f"image.{extension}", content_type=mime)
        async with async_get_clientsession(hass).post(
            lucida_url, data=form, timeout=LUCIDA_TIMEOUT, allow_redirects=False
        ) as response:
            response.raise_for_status()
            if response.content_length and response.content_length > MAX_IMAGE_BYTES:
                raise ValueError("Lucida response exceeds limit")
            parts = bytearray()
            async for chunk in response.content.iter_chunked(65536):
                parts.extend(chunk)
                if len(parts) > MAX_IMAGE_BYTES:
                    raise ValueError("Lucida response exceeds limit")
        return await hass.async_add_executor_job(_validate_lucida, bytes(parts))
    except (aiohttp.ClientError, TimeoutError, ValueError, OSError, Image.DecompressionBombError, SyntaxError) as exc:
        _LOGGER.warning("Lucida failed (%s); using image fallback", type(exc).__name__)
        return data


def _atomic_save(image: Image.Image, destination: Path, format: str, **kw) -> None:
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


def _process(data: bytes, target: Path) -> str:
    with Image.open(BytesIO(data)) as source:
        result = normalize_generated_image(source)
        normalized = normalize_icon_canvas(result.image)
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
    return result.background_status


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
    hass: HomeAssistant, image_url: str, filename_stem: str, *,
    lucida_url: str | None = None,
) -> SavedGeneratedImage:
    """Save a 512px-height transparent PNG, thumbnail and theme QA preview."""
    if not filename_stem or filename_stem != os.path.basename(filename_stem):
        raise ValueError("Unsafe image name")
    data = await _read_source(hass, image_url)
    data = await _maybe_remove_background(hass, data, lucida_url)
    target = Path(hass.config.path("www", PUBLIC_DIRECTORY, filename_stem + ".png"))
    status = await hass.async_add_executor_job(_process, data, target)
    if status.startswith("review_needed"):
        _LOGGER.warning("Image %s requires background review: %s", filename_stem, status)
    return SavedGeneratedImage(
        image_url=PUBLIC_PREFIX + target.name,
        background_status=status,
        qa_url=PUBLIC_PREFIX + filename_stem + "_qa.webp",
    )

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
import tempfile

import aiohttp
from PIL import Image
from homeassistant.components.media_source import async_resolve_media
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .image_processing import (
    create_theme_qa_preview,
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


async def _read_source(hass: HomeAssistant, source: str) -> bytes:
    """Resolve HA media or an image URL returned by AI Task."""
    if source.startswith("media-source://"):
        resolved = await async_resolve_media(hass, source, None)
        if resolved.path:
            data = await hass.async_add_executor_job(Path(resolved.path).read_bytes)
            if len(data) > MAX_IMAGE_BYTES:
                raise ValueError("Image exceeds size limit")
            return data
        source = resolved.url
    if not source.startswith(("https://", "http://")):
        raise ValueError("AI image must be HA media or a valid HTTP(S) URL")
    async with async_get_clientsession(hass).get(
        source, timeout=aiohttp.ClientTimeout(total=45), allow_redirects=False
    ) as response:
        response.raise_for_status()
        if response.content_length and response.content_length > MAX_IMAGE_BYTES:
            raise ValueError("Image exceeds size limit")
        chunks = bytearray()
        async for part in response.content.iter_chunked(65536):
            chunks.extend(part)
            if len(chunks) > MAX_IMAGE_BYTES:
                raise ValueError("Image exceeds size limit")
        return bytes(chunks)


def _already_transparent(data: bytes) -> bool:
    with Image.open(BytesIO(data)) as image:
        if "A" not in image.getbands() and "transparency" not in image.info:
            return False
        return image.convert("RGBA").getchannel("A").getextrema()[0] < 250


def _validate_lucida(data: bytes) -> bytes:
    if not data or len(data) > MAX_IMAGE_BYTES:
        raise ValueError("Invalid Lucida response size")
    with Image.open(BytesIO(data)) as image:
        if image.format != "PNG" or image.mode != "RGBA":
            raise ValueError("Lucida did not return an RGBA PNG")
        image.load()
    return data


async def _maybe_remove_background(
    hass: HomeAssistant, data: bytes, lucida_url: str | None
) -> bytes:
    if not lucida_url or await hass.async_add_executor_job(_already_transparent, data):
        return data
    try:
        form = aiohttp.FormData()
        form.add_field("file", data, filename="image.png", content_type="image/png")
        async with async_get_clientsession(hass).post(
            lucida_url, data=form, timeout=LUCIDA_TIMEOUT
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
    except (aiohttp.ClientError, TimeoutError, ValueError, OSError) as exc:
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
    _atomic_save(normalized, target, "PNG", optimize=True)
    preview = create_theme_qa_preview(normalized)
    _atomic_save(preview, target.with_name(target.stem + "_qa.webp"), "WEBP", quality=86)
    thumb = normalized.copy()
    thumb.thumbnail((512, 512), Image.Resampling.LANCZOS)
    _atomic_save(thumb, target.with_name(target.stem + "_thumb.webp"), "WEBP", quality=82)
    return result.background_status


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

"""Authenticated reference preview for local Home Assistant media sources.

Only local resolved media paths are accepted. Never proxy arbitrary HTTP URLs
or permit the browser to fetch a selected media source without HA authorization.
"""
from __future__ import annotations

from io import BytesIO
from pathlib import Path

from aiohttp import web
from homeassistant.components.http import KEY_HASS, HomeAssistantView, require_admin
from homeassistant.components.media_source import async_resolve_media
from PIL import Image, UnidentifiedImageError

MAX_REFERENCE_BYTES = 8 * 1024 * 1024
MIME = {"PNG": "image/png", "JPEG": "image/jpeg", "WEBP": "image/webp"}


def read_preview(path: Path) -> tuple[bytes, str]:
    with path.open("rb") as file:
        data = file.read(MAX_REFERENCE_BYTES + 1)
    if not data or len(data) > MAX_REFERENCE_BYTES:
        raise ValueError("Reference file exceeds size limit")
    with Image.open(BytesIO(data)) as image:
        if image.format not in MIME or image.width * image.height > 32_000_000:
            raise ValueError("Only reasonably sized PNG, JPEG or WebP references are supported")
        image.verify()
        content_type = MIME[image.format]
    return data, content_type


class ReferencePreviewView(HomeAssistantView):
    url = "/api/activity_images/settings/reference"
    name = "api:activity_images:settings_reference"
    requires_auth = True

    @require_admin
    async def get(self, request):
        media_id = request.query.get("media_id", "")
        if (not media_id.startswith("media-source://")
                or len(media_id) > 1024 or "\x00" in media_id):
            raise web.HTTPBadRequest(text="Expected a media-source reference")
        hass = request.app[KEY_HASS]
        try:
            resolved = await async_resolve_media(hass, media_id, None)
            if not resolved.path:
                raise ValueError("Remote media previews are not supported")
            data, content_type = await hass.async_add_executor_job(
                read_preview, Path(resolved.path)
            )
        except (ValueError, OSError, UnidentifiedImageError, SyntaxError) as exc:
            raise web.HTTPBadRequest(text="Reference preview unavailable") from exc
        return web.Response(body=data, content_type=content_type, headers={
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
        })

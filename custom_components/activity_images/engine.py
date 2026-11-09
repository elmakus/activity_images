"""Provider-agnostic AI Task activity-image generator."""
from __future__ import annotations

import asyncio
import hashlib
import logging
import time
from collections.abc import Callable
from datetime import UTC, datetime
from uuid import uuid4

from homeassistant.core import HomeAssistant

from .const import (
    CONF_AI_ENTITY,
    CONF_LUCIDA_URL,
    CONF_REFERENCE_MEDIA_ID,
)
from .generated_image import async_save_generated_image
from .image_cache import ActivityImageCache, normalize_title
from .prompts import build_activity_image_instructions
from .retry import async_retry_transient

_LOGGER = logging.getLogger(__name__)


class ActivityImageGenerator:
    def __init__(self, hass: HomeAssistant, cache: ActivityImageCache, settings: dict) -> None:
        self.hass = hass
        self.cache = cache
        self.settings = settings
        self._lock = asyncio.Lock()
        self.image_saved_callback: Callable[[str, str], None] | None = None

    async def async_generate_activity_image(self, title: str) -> str:
        """Preserve the original manual action's always-regenerate contract."""
        result = await self.async_generate(title, force=True)
        return result["image_url"]

    async def async_generate(self, title: str, *, force: bool = False, remember: bool = True) -> dict:
        """Resolve existing or create a new image and return its public URL."""
        title = title.strip()
        if not title or len(title) > 255:
            raise ValueError("Activity title must contain 1–255 characters")
        async with self._lock:
            if not force:
                cached = await self.cache.async_find(title)
                if cached:
                    return {
                        "title": title, "image_url": cached, "reused": True,
                        "background_status": "cached",
                    }
            entity_id = self.settings.get(CONF_AI_ENTITY)
            if not entity_id:
                raise ValueError("Configure an ai_task entity in Activity Images")
            signature = normalize_title(title)
            # Never overwrite a previously published master or its variants.
            suffix = hashlib.sha256(signature.encode("utf-8")).hexdigest()[:16] + "_" + uuid4().hex
            payload = {
                "task_name": f"activity_image_{suffix}",
                "instructions": build_activity_image_instructions(title),
                "entity_id": entity_id,
            }
            reference = self.settings.get(CONF_REFERENCE_MEDIA_ID)
            if reference:
                payload["attachments"] = [{
                    "media_content_id": reference,
                    "media_content_type": "image/png",
                }]
            async def request_image():
                return await self.hass.services.async_call(
                    "ai_task", "generate_image", payload,
                    blocking=True, return_response=True,
                )

            # Retry only the provider request. Never regenerate for a cache hit,
            # and never repeat PNG processing or a successful paid provider call.
            response = await async_retry_transient(request_image) or {}
            image = response.get("image") or {}
            source = response.get("media_source_id") or response.get("url")
            if not source and isinstance(image, dict):
                source = image.get("url")
            if not isinstance(source, str) or not source:
                raise ValueError("ai_task.generate_image did not return an image URL")
            saved = await async_save_generated_image(
                self.hass, source, suffix,
                lucida_url=self.settings.get(CONF_LUCIDA_URL) or None,
            )
            image_url = f"{saved.image_url}?v={time.time_ns()}"
            await self.cache.async_record_image(image_url, {
                "origin": "generated", "created_at": datetime.now(UTC).isoformat(),
            })
            if remember:
                await self.cache.async_remember(title, image_url)
            if remember and self.image_saved_callback is not None:
                try:
                    self.image_saved_callback(title, image_url)
                except Exception:
                    _LOGGER.exception("Unable to notify library about saved image")
            _LOGGER.debug("Generated activity illustration for %s", title)
            return {
                "title": title, "image_url": image_url, "reused": False,
                "background_status": saved.background_status,
                "qa_url": saved.qa_url,
            }

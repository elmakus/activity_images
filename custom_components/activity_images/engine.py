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
    CONF_PROMPT_TEMPLATE,
)
from .generated_image import async_save_generated_image
from .image_cache import ActivityImageCache, normalize_title
from .prompts import build_activity_image_instructions
from .retry import async_retry_transient

_LOGGER = logging.getLogger(__name__)


def generation_stem(title: str, nonce: str) -> str:
    return hashlib.sha256(normalize_title(title).encode("utf-8")).hexdigest()[:16] + "_" + nonce


class ActivityImageGenerator:
    def __init__(self, hass: HomeAssistant, cache: ActivityImageCache, settings: dict) -> None:
        self.hass = hass
        self.cache = cache
        self.settings = settings
        self._lock = asyncio.Lock()
        self.image_saved_callback: Callable[[str, str], None] | None = None

    @property
    def is_generating(self) -> bool:
        """Include direct public generation, not only queue-owned calls."""
        return self._lock.locked()

    async def async_generate_activity_image(self, title: str) -> str:
        """Preserve the original manual action's always-regenerate contract."""
        result = await self.async_generate(title, force=True)
        return result["image_url"]

    async def async_generate(self, title: str, *, force: bool = False, remember: bool = True, deletion: dict | None = None) -> dict:
        """Resolve existing or create a new image and return its public URL."""
        title = title.strip()
        if not title or len(title) > 255:
            raise ValueError("Activity title must contain 1–255 characters")
        async with self._lock:
            if self.cache.reserved(title, operation_id=deletion["operation_id"] if deletion else None):
                raise ValueError("Title is reserved for permanent deletion")
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
            task_id = hashlib.sha256(signature.encode("utf-8")).hexdigest()[:16]
            # Keep the provider task name stable but never overwrite published files.
            suffix = generation_stem(title, deletion["job_id"] if deletion else uuid4().hex)
            payload = {
                "task_name": f"activity_image_{task_id}",
                "instructions": build_activity_image_instructions(title, self.settings.get(CONF_PROMPT_TEMPLATE)),
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
            async def persist_result():
                saved = await async_save_generated_image(
                    self.hass, source, suffix,
                    lucida_url=self.settings.get(CONF_LUCIDA_URL) or None,
                )
                url = f"{saved.image_url}?v={time.time_ns()}"
                metadata = {"origin": "generated", "created_at": datetime.now(UTC).isoformat()}
                if deletion:
                    await self.cache.async_record_candidate(url, metadata, deletion)
                else:
                    await self.cache.async_record_image(url, metadata)
                return saved, url

            if deletion:
                # Executor writes cannot be cancelled: finish and journal paid results before unload.
                persistence = asyncio.create_task(persist_result())
                cancelled = False
                while not persistence.done():
                    try:
                        await asyncio.shield(persistence)
                    except asyncio.CancelledError:
                        cancelled = True
                saved, image_url = await persistence
                if cancelled:
                    raise asyncio.CancelledError
            else:
                saved, image_url = await persist_result()
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

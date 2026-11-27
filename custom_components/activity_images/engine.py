"""Provider-agnostic AI Task activity-image generator."""
from __future__ import annotations

import asyncio
import hashlib
import logging
import time
from collections.abc import Awaitable, Callable, Mapping
from datetime import UTC, datetime
from uuid import uuid4
from typing import NotRequired, TypedDict, TYPE_CHECKING, cast

if TYPE_CHECKING:
    from homeassistant.core import ServiceResponse

from homeassistant.core import HomeAssistant

from .const import (
    CONF_AI_ENTITY,
    CONF_REFERENCE_MEDIA_ID,
    CONF_PROMPT_TEMPLATE,
)
from .generated_image import SavedGeneratedImage, async_save_generated_image
from .image_cache import ActivityImageCache, CandidateContext, ImageMetadata, normalize_title
from .prompts import build_activity_image_instructions
from .retry import async_retry_transient
from .storage import async_drain

_LOGGER = logging.getLogger(__name__)


def generation_stem(title: str, nonce: str) -> str:
    return hashlib.sha256(normalize_title(title).encode("utf-8")).hexdigest()[:16] + "_" + nonce


class GenerationSuppressed(ValueError):
    """An automatic job lost authorization before a new paid provider attempt."""

    def __init__(self, message: str, *, translation_key: str = "generation_suppressed") -> None:
        super().__init__(message)
        self.translation_key = translation_key


class ProviderConfigurationError(ValueError):
    """A local configuration failure whose message contains no provider data."""

    def __init__(self, message: str, *, translation_key: str) -> None:
        super().__init__(message)
        self.translation_key = translation_key


class GenerationResult(TypedDict):
    title: str
    image_url: str
    reused: bool
    background_status: str
    revised_prompt: str
    qa_url: NotRequired[str]


class DeletionContext(CandidateContext):
    job_id: str


class ActivityImageGenerator:
    def __init__(self, hass: HomeAssistant, cache: ActivityImageCache, settings: Mapping[str, object]) -> None:
        self.hass = hass
        self.cache = cache
        self.settings = settings
        self._lock = asyncio.Lock()
        self.image_saved_callback: Callable[[str, str], None] | None = None

    @property
    def is_generating(self) -> bool:
        """Include direct public generation, not only queue-owned calls."""
        return self._lock.locked()

    def cached_result(self, title: str, url: str) -> GenerationResult:
        return {
            "title": title, "image_url": url, "reused": True,
            "background_status": "cached",
            "revised_prompt": build_activity_image_instructions(title, cast(str | None, self.settings.get(CONF_PROMPT_TEMPLATE))),
        }

    async def async_generate_activity_image(self, title: str) -> str:
        """Preserve the original manual action's always-regenerate contract."""
        result = await self.async_generate(title, force=True)
        return result["image_url"]

    async def async_generate(self, title: str, *, force: bool = False, remember: bool = True, deletion: DeletionContext | None = None, authorize: Callable[[], Awaitable[None]] | None = None) -> GenerationResult:
        """Resolve existing or create a new image and return its public URL."""
        from .library import validate_title
        title = validate_title(title)
        async with self._lock:
            # One request, its retries and post-processing use one configuration.
            settings = dict(self.settings)
            if self.cache.reserved(title, operation_id=deletion["operation_id"] if deletion else None):
                raise ValueError("Title is reserved for permanent deletion")
            if not force:
                cached = await self.cache.async_find(title)
                if cached:
                    return self.cached_result(title, cached)
            instructions = build_activity_image_instructions(title, cast(str | None, settings.get(CONF_PROMPT_TEMPLATE)))
            entity_id = cast(str | None, settings.get(CONF_AI_ENTITY))
            if not entity_id:
                raise ProviderConfigurationError("Configure an ai_task entity in Activity Images", translation_key="provider_required")
            from .settings import is_own_provider_entity

            if is_own_provider_entity(self.hass, getattr(self.settings, "entry", None), entity_id):
                raise ProviderConfigurationError("Activity Images cannot use itself as its image provider", translation_key="self_provider")
            signature = normalize_title(title)
            task_id = hashlib.sha256(signature.encode("utf-8")).hexdigest()[:16]
            # Keep the provider task name stable but never overwrite published files.
            suffix = generation_stem(title, deletion["job_id"] if deletion else uuid4().hex)
            payload: dict[str, object] = {
                "task_name": f"activity_image_{task_id}",
                "instructions": instructions,
                "entity_id": entity_id,
            }
            reference = settings.get(CONF_REFERENCE_MEDIA_ID)
            if reference:
                # HA's MediaSelector requires a type string. An empty string lets
                # native AI Task resolution supply the actual JPEG/PNG/WebP MIME.
                payload["attachments"] = [{"media_content_id": reference, "media_content_type": ""}]
            async def request_image() -> ServiceResponse:
                # This runs after all lock/I/O waits, and again before each retry.
                if authorize is not None:
                    await authorize()
                if any(self.settings.get(key) != settings.get(key) for key in (
                        CONF_AI_ENTITY, CONF_REFERENCE_MEDIA_ID, CONF_PROMPT_TEMPLATE)):
                    raise GenerationSuppressed("Image generation settings changed; review the request again", translation_key="generation_settings_changed")
                if self.cache.reserved(title, operation_id=deletion["operation_id"] if deletion else None):
                    raise GenerationSuppressed("Title is reserved for permanent deletion", translation_key="title_reserved")
                return await self.hass.services.async_call(
                    "ai_task", "generate_image", payload,
                    blocking=True, return_response=True,
                )

            # Retry only the provider request. Never regenerate for a cache hit,
            # and never repeat PNG processing or a successful paid provider call.
            response = await async_retry_transient(request_image) or {}
            source = response.get("media_source_id")
            if not isinstance(source, str) or not source.startswith("media-source://"):
                raise ValueError("ai_task.generate_image did not return a local media-source image ID")
            async def persist_result() -> tuple[SavedGeneratedImage, str]:
                saved = await async_save_generated_image(
                    self.hass, source, suffix,
                )
                url = f"{saved.image_url}?v={time.time_ns()}"
                metadata: ImageMetadata = {"origin": "generated", "created_at": datetime.now(UTC).isoformat(),
                            "background_status": saved.background_status}
                if deletion:
                    await self.cache.async_record_candidate(url, metadata, deletion)
                else:
                    automatic_title = title if remember else None
                    await self.cache.async_record_image(url, metadata, title=automatic_title)
                return saved, url

            # Executor writes cannot be cancelled: finish and journal paid results before unload.
            (saved, image_url), cancelled = await async_drain(persist_result())
            if cancelled:
                raise asyncio.CancelledError
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
                "revised_prompt": instructions,
            }

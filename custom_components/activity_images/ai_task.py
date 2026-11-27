"""Home Assistant AI Task image wrapper for unchanged upstream Home Tasks.

This platform delegates to the existing standalone Activity Images engine.
The same original 3D prompt, style reference, normalization and cache
are used for calendar images and Home Tasks pictures.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from . import ActivityImagesConfigEntry
    from homeassistant.core import Event, EventStateChangedData
    from homeassistant.components.conversation.chat_log import ChatLog

from collections.abc import Callable
from io import BytesIO
import logging
from pathlib import Path
from urllib.parse import unquote, urlsplit

from homeassistant.components import ai_task
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.event import async_track_state_change_event
from PIL import Image

from .const import CONF_AI_ENTITY, DOMAIN
from .entity import service_device_info
from .image_cache import ACTIVITY_IMAGE_PREFIX
from .library import _read_owned, _verify
from .settings_diagnostics import provider_diagnostics
from .settings_schema import effective_settings

_LOGGER = logging.getLogger(__name__)
PARALLEL_UPDATES = 1

# Current upstream Home Tasks passes the title in instructions, then this
# aspect-ratio suffix. Its task_name contains a hash, NOT the readable title.
_HOME_TASKS_ASPECT = "\n\nThe image must have a square 1:1 aspect ratio."


def extract_home_tasks_title(task_name: str, instructions: str) -> str:
    """Parse the upstream protocol without mistaking its hash for a title.

    Home Tasks should be configured with an empty prompt_prefix: upstream
    supplies no separate title field, so non-empty prefixes cannot be
    distinguished reliably from the task title.
    """
    if not task_name.startswith("home_tasks_") or not instructions.endswith(
        _HOME_TASKS_ASPECT
    ):
        raise ServiceValidationError(
            "Activity Images wrapper expects the Home Tasks image request format", translation_domain=DOMAIN, translation_key="home_tasks_protocol"
        )
    title = instructions[: -len(_HOME_TASKS_ASPECT)].strip()
    if not title or len(title) > 255 or "\n" in title or "\r" in title:
        raise ServiceValidationError("Home Tasks must provide a valid single-line title", translation_domain=DOMAIN, translation_key="home_tasks_title")
    return title


def _read_png(path: Path) -> tuple[bytes, int, int]:
    """Return bounded local images as PNG without modifying library files."""
    data = _read_owned(path.parent, path.name)
    width, height, format_name = _verify(data)
    if format_name != "PNG":
        with Image.open(BytesIO(data)) as image:
            output = BytesIO()
            image.convert("RGBA").save(output, "PNG")
            data = output.getvalue()
    return data, width, height


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ActivityImagesConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Expose an AI Task image entity, even in no-provider library-only mode."""
    async_add_entities([ActivityImagesAITaskEntity(entry)])


class ActivityImagesAITaskEntity(ai_task.AITaskEntity):
    """Return generated/reused images to Home Tasks through the official API."""

    _attr_has_entity_name = True
    _attr_name = None  # Main feature: use the service device name.
    _attr_translation_key = "generator"
    _attr_should_poll = False
    _attr_supported_features = ai_task.AITaskEntityFeature.GENERATE_IMAGE

    def __init__(self, entry: ActivityImagesConfigEntry) -> None:
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_ai_task"
        self._attr_device_info = service_device_info(entry.entry_id)
        self._provider_unsubscribe: Callable[[], None] | None = None
        self._observed_provider: str | None = None
        self._provider_was_unavailable = False

    def _provider_status(self) -> str:
        """Describe advertised provider readiness, without a billable probe."""
        values = effective_settings(self._entry)
        provider = values[CONF_AI_ENTITY]
        if not provider:
            return "not_configured"
        if provider == self.entity_id:
            return "self_reference"
        if getattr(self, "hass", None) is None:
            return "unavailable"
        return provider_diagnostics(self.hass, self._entry, values)["status"]

    @property
    def available(self) -> bool:
        """Reflect generation capability; cached lookup remains local."""
        return self._provider_status() == "available"

    @callback
    def _async_provider_changed(self, _event: Event[EventStateChangedData] | None = None) -> None:
        status = self._provider_status()
        if status == "not_configured":
            self._provider_was_unavailable = False
        elif status != "available":
            if not self._provider_was_unavailable:
                _LOGGER.warning("Activity Images provider unavailable (%s); cached images remain accessible", status)
            self._provider_was_unavailable = True
        elif self._provider_was_unavailable:
            _LOGGER.info("Activity Images provider is available again")
            self._provider_was_unavailable = False
        self.async_write_ha_state()

    @callback
    def _async_watch_provider(self) -> None:
        provider = effective_settings(self._entry)[CONF_AI_ENTITY] or None
        if provider != self._observed_provider:
            self._async_unwatch_provider()
            self._observed_provider = provider
            self._provider_was_unavailable = False
            if provider and self._provider_status() != "self_reference":
                self._provider_unsubscribe = async_track_state_change_event(
                    self.hass, [provider], self._async_provider_changed
                )
        self._async_provider_changed()

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(self._entry.add_update_listener(self._async_settings_updated))
        self.async_on_remove(self._async_unwatch_provider)
        self._async_watch_provider()

    @callback
    def _async_unwatch_provider(self) -> None:
        if self._provider_unsubscribe is not None:
            self._provider_unsubscribe()
            self._provider_unsubscribe = None
        self._observed_provider = None

    async def _async_settings_updated(self, hass: HomeAssistant, entry: ActivityImagesConfigEntry) -> None:
        self._async_watch_provider()

    async def _async_generate_image(
        self,
        task: ai_task.GenImageTask,
        chat_log: ChatLog,
    ) -> ai_task.GenImageTaskResult:
        """Generate through the existing pipeline and return real PNG bytes."""
        title = extract_home_tasks_title(task.name, task.instructions)
        runtime = self._entry.runtime_data
        if runtime is None:
            raise HomeAssistantError("Activity Images is not loaded", translation_domain=DOMAIN, translation_key="not_loaded")
        provider = cast(str | None, runtime.generator.settings.get(CONF_AI_ENTITY))
        if not provider:
            raise ServiceValidationError(
                "Configure a real AI Task image provider in Activity Images", translation_domain=DOMAIN, translation_key="provider_required"
            )
        from .settings import is_own_provider_entity

        if is_own_provider_entity(self.hass, self._entry, provider) or provider == self.entity_id:
            raise ServiceValidationError(
                "Activity Images cannot use itself as its image provider", translation_domain=DOMAIN, translation_key="self_provider"
            )
        if task.attachments:
            raise ServiceValidationError(
                "Home Tasks wrapper does not accept custom attachments; "
                "configure a shared style reference in Activity Images", translation_domain=DOMAIN, translation_key="home_tasks_attachments"
            )
        result = await runtime.queue.async_generate_for_action(title)
        url = result.get("image_url", "")
        parsed = urlsplit(url)
        if parsed.scheme or parsed.netloc or parsed.fragment:
            raise HomeAssistantError("Activity Images returned a nonlocal image URL", translation_domain=DOMAIN, translation_key="nonlocal_image_url")
        if not parsed.path.startswith(ACTIVITY_IMAGE_PREFIX):
            raise HomeAssistantError("Activity Images returned an invalid image URL", translation_domain=DOMAIN, translation_key="invalid_image_url")
        filename = unquote(parsed.path[len(ACTIVITY_IMAGE_PREFIX):])
        if (
            not filename or Path(filename).name != filename or filename.startswith(".")
            or "\\" in filename or chr(0) in filename
        ):
            raise HomeAssistantError("Invalid image filename", translation_domain=DOMAIN, translation_key="invalid_image_filename")

        path = Path(self.hass.config.path("www", "activity_images", filename))
        try:
            image_data, width, height = await self.hass.async_add_executor_job(
                _read_png, path
            )
        except (OSError, ValueError, SyntaxError, Image.DecompressionBombError) as exc:
            raise HomeAssistantError(
                "Unable to read processed Activity Images PNG", translation_domain=DOMAIN, translation_key="read_png_failed"
            ) from exc

        return ai_task.GenImageTaskResult(
            image_data=image_data,
            conversation_id=chat_log.conversation_id,
            mime_type="image/png",
            width=width,
            height=height,
            revised_prompt=result.get("revised_prompt"),
        )

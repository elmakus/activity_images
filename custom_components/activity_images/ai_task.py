"""Home Assistant AI Task image wrapper for unchanged upstream Home Tasks.

This platform delegates to the existing standalone Activity Images engine.
The same original 3D prompt, style reference, background removal and cache
are used for calendar images and Home Tasks pictures.
"""
from __future__ import annotations

from io import BytesIO
from pathlib import Path
from urllib.parse import unquote, urlsplit

from homeassistant.components import ai_task
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from PIL import Image

from .const import CONF_AI_ENTITY, CONF_PROMPT_TEMPLATE
from .image_cache import ACTIVITY_IMAGE_PREFIX
from .library import _read_owned, _verify
from .prompts import build_activity_image_instructions

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
        raise HomeAssistantError(
            "Activity Images wrapper expects the Home Tasks image request format"
        )
    title = instructions[: -len(_HOME_TASKS_ASPECT)].strip()
    if not title or len(title) > 255 or "\n" in title or "\r" in title:
        raise HomeAssistantError("Home Tasks must provide a valid single-line title")
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
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Expose an AI Task image entity, even in no-provider library-only mode."""
    async_add_entities([ActivityImagesAITaskEntity(entry)])


class ActivityImagesAITaskEntity(ai_task.AITaskEntity):
    """Return generated/reused images to Home Tasks through the official API."""

    _attr_has_entity_name = True
    _attr_name = "Activity Images"
    _attr_icon = "mdi:image-auto-adjust"
    _attr_should_poll = False
    _attr_supported_features = ai_task.AITaskEntityFeature.GENERATE_IMAGE

    def __init__(self, entry: ConfigEntry) -> None:
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_ai_task"

    @property
    def available(self) -> bool:  # pyright: ignore[reportIncompatibleVariableOverride]
        """Keep provider availability dynamic rather than caching its first value."""
        provider = self._entry.options.get(
            CONF_AI_ENTITY, self._entry.data.get(CONF_AI_ENTITY)
        )
        return bool(provider) and provider != self.entity_id

    async def _async_generate_image(
        self,
        task: ai_task.GenImageTask,
        chat_log,
    ) -> ai_task.GenImageTaskResult:
        """Generate through the existing pipeline and return real PNG bytes."""
        title = extract_home_tasks_title(task.name, task.instructions)
        runtime = self._entry.runtime_data
        provider = runtime.generator.settings.get(CONF_AI_ENTITY)
        if not provider:
            raise HomeAssistantError(
                "Configure a real AI Task image provider in Activity Images"
            )
        if provider == self.entity_id:
            raise HomeAssistantError(
                "Activity Images cannot use itself as its image provider"
            )
        if task.attachments:
            raise HomeAssistantError(
                "Home Tasks wrapper does not accept custom attachments; "
                "configure a shared style reference in Activity Images"
            )
        result = await runtime.generator.async_generate(title)
        url = result.get("image_url", "")
        parsed = urlsplit(url)
        if parsed.scheme or parsed.netloc or parsed.fragment:
            raise HomeAssistantError("Activity Images returned a nonlocal image URL")
        if not parsed.path.startswith(ACTIVITY_IMAGE_PREFIX):
            raise HomeAssistantError("Activity Images returned an invalid image URL")
        filename = unquote(parsed.path[len(ACTIVITY_IMAGE_PREFIX):])
        if (
            not filename or Path(filename).name != filename or filename.startswith(".")
            or "\\" in filename or chr(0) in filename
        ):
            raise HomeAssistantError("Invalid image filename")

        path = Path(self.hass.config.path("www", "activity_images", filename))
        try:
            image_data, width, height = await self.hass.async_add_executor_job(
                _read_png, path
            )
        except (OSError, ValueError, SyntaxError, Image.DecompressionBombError) as exc:
            raise HomeAssistantError(
                "Unable to read processed Activity Images PNG"
            ) from exc

        return ai_task.GenImageTaskResult(
            image_data=image_data,
            conversation_id=chat_log.conversation_id,
            mime_type="image/png",
            width=width,
            height=height,
            revised_prompt=build_activity_image_instructions(
                title, runtime.generator.settings.get(CONF_PROMPT_TEMPLATE)
            ),
        )

"""Standalone Activity Images library manager and compatible public actions.

The image engine, matching cache, and graphics pipeline are kept independent
from Home Tasks. Actions are registered in async_setup, as recommended by HA.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path

import probatio as vol
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv

from .calendar_adapter import ActivityCalendarAdapter
from .const import DOMAIN
from .engine import ActivityImageGenerator
from .image_cache import ActivityImageCache, async_get_cache
from .image_queue import ActivityImageQueue
from .library import ActivityImageLibrary, _read_owned, _verify, validate_title
from .library_deletion import ActivityImageDeletions
from .settings import migrate_calendar_options
from .settings_schema import ConfigEntrySettings

_LOGGER = logging.getLogger(__name__)

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

MAX_TITLES_PER_REQUEST = 64
_LOCAL_PNG = re.compile(r"^/local/activity_images/([A-Za-z0-9_.-]+\.png)(?:\?v=[A-Za-z0-9_.-]+)?$")


def _valid_title(value):
    try:
        return validate_title(value)
    except ValueError as exc:
        raise vol.Invalid(str(exc)) from exc


_TITLE = vol.All(str, vol.Length(min=1, max=255), _valid_title)
_TITLES = vol.All(
    cv.ensure_list,
    vol.Length(min=1, max=MAX_TITLES_PER_REQUEST),
    [_TITLE],
)


@dataclass(slots=True)
class ActivityImagesRuntime:
    """Live state of the one configured image generator."""

    generator: ActivityImageGenerator
    queue: ActivityImageQueue
    cache: ActivityImageCache
    calendar: ActivityCalendarAdapter
    library: ActivityImageLibrary
    deletions: ActivityImageDeletions | None = None


def _get_runtime(hass: HomeAssistant) -> ActivityImagesRuntime:
    """Access the configured generator without accepting unconfigured calls."""
    integrations = hass.data.get(DOMAIN, {})
    if not integrations:
        raise HomeAssistantError(
            "Activity Images is not configured. Add it in Settings > Devices & services."
        )
    return next(iter(integrations.values()))


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """Register actions once, regardless of config-entry availability."""
    hass.data.setdefault(DOMAIN, {})

    async def generate(call: ServiceCall) -> dict:
        return await _get_runtime(hass).generator.async_generate(
            call.data["title"], force=call.data.get("force", False)
        )

    async def resolve(call: ServiceCall) -> dict:
        return await _get_runtime(hass).queue.async_resolve(
            call.data["titles"],
            enqueue_missing=call.data.get("enqueue_missing", False),
        )

    async def status(call: ServiceCall) -> dict:
        return _get_runtime(hass).queue.status()

    async def refresh_calendars(call: ServiceCall) -> dict:
        return await _get_runtime(hass).calendar.async_refresh()

    async def cancel(call: ServiceCall) -> dict:
        return {
            "cancelled": await _get_runtime(hass).queue.async_cancel(call.data["titles"])
        }

    async def clear(call: ServiceCall) -> dict:
        return {"cleared": await _get_runtime(hass).queue.async_clear_errors()}

    async def link_existing(call: ServiceCall) -> dict:
        """Remember a verified local PNG, without regenerating it."""
        title = call.data["title"]
        url = call.data["image_url"]
        match = _LOCAL_PNG.fullmatch(url)
        if match is None or match.group(1).startswith("."):
            raise HomeAssistantError("Only /local/activity_images/<filename>.png can be linked")
        filename = match.group(1)
        root = Path(hass.config.path("www", "activity_images"))
        def verify():
            data = _read_owned(root, filename)
            if _verify(data)[2] != "PNG":
                raise ValueError("Expected a valid PNG")
        try:
            await hass.async_add_executor_job(verify)
        except (OSError, ValueError) as exc:
            raise HomeAssistantError("Local PNG is missing or invalid") from exc
        runtime = _get_runtime(hass)
        await runtime.cache.async_remember(title, url)
        await runtime.calendar.async_reconcile()
        return {"title": title, "image_url": url, "linked": True}

    async def forget(call: ServiceCall) -> dict:
        title = call.data.get("title")
        image_url = call.data.get("image_url")
        if not title and not image_url:
            raise HomeAssistantError("Specify a title or image_url to forget")
        runtime = _get_runtime(hass)
        removed = await runtime.cache.async_forget(title=title, url=image_url)
        await runtime.calendar.async_reconcile()
        return {"removed": removed}

    hass.services.async_register(
        DOMAIN, "generate_image", generate,
        schema=vol.Schema({
            vol.Required("title"): _TITLE,
            vol.Optional("force", default=False): cv.boolean,
        }),
        supports_response=SupportsResponse.OPTIONAL,
    )
    hass.services.async_register(
        DOMAIN, "resolve_images", resolve,
        schema=vol.Schema({
            vol.Required("titles"): _TITLES,
            vol.Optional("enqueue_missing", default=False): cv.boolean,
        }),
        supports_response=SupportsResponse.OPTIONAL,
    )
    hass.services.async_register(
        DOMAIN, "queue_status", status,
        supports_response=SupportsResponse.OPTIONAL,
    )
    hass.services.async_register(
        DOMAIN, "cancel_queued", cancel,
        schema=vol.Schema({vol.Required("titles"): _TITLES}),
        supports_response=SupportsResponse.OPTIONAL,
    )
    hass.services.async_register(
        DOMAIN, "clear_errors", clear,
        supports_response=SupportsResponse.OPTIONAL,
    )
    hass.services.async_register(
        DOMAIN, "link_existing_image", link_existing,
        schema=vol.Schema({vol.Required("title"): _TITLE,
                           vol.Required("image_url"): cv.string}),
        supports_response=SupportsResponse.OPTIONAL,
    )
    hass.services.async_register(
        DOMAIN, "refresh_calendars", refresh_calendars,
        supports_response=SupportsResponse.OPTIONAL,
    )
    hass.services.async_register(
        DOMAIN, "forget_image", forget,
        schema=vol.Schema({
            vol.Optional("title"): _TITLE,
            vol.Optional("image_url"): cv.string,
        }),
        supports_response=SupportsResponse.OPTIONAL,
    )
    return True


async def _async_update_options(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Keep paid work and runtime identities intact when native options change."""
    runtime = getattr(entry, "runtime_data", None)
    if runtime is not None:
        await runtime.calendar.async_update_settings(runtime.generator.settings)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Load the one independent image generator and its persistent queue."""
    migrate_calendar_options(hass, entry)
    settings = ConfigEntrySettings(entry)
    cache = await async_get_cache(hass)
    generator = ActivityImageGenerator(hass, cache, settings)
    queue = ActivityImageQueue(hass, generator)
    calendar = ActivityCalendarAdapter(hass, cache, queue, settings)
    runtime = ActivityImagesRuntime(
        generator=generator, queue=queue, cache=cache, calendar=calendar,
        library=ActivityImageLibrary(hass, cache),
    )
    runtime.deletions = ActivityImageDeletions(hass, runtime.library, queue, calendar)
    entry.runtime_data = runtime
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = runtime
    generator.image_saved_callback = calendar.image_saved
    manager_registered = False
    try:
        await _async_register_manager(hass)
        manager_registered = True
        await calendar.async_setup()
        await queue.async_setup()
        await runtime.deletions.async_setup()
        await hass.config_entries.async_forward_entry_setups(entry, ["sensor", "ai_task"])
        await calendar.async_start()
    except Exception:
        if manager_registered:
            _async_remove_manager(hass)
        generator.image_saved_callback = None
        await calendar.async_shutdown()
        await runtime.deletions.async_shutdown()
        await queue.async_shutdown()
        hass.data[DOMAIN].pop(entry.entry_id, None)
        raise
    entry.async_on_unload(entry.add_update_listener(_async_update_options))
    _LOGGER.info("Activity Images standalone generator and calendar library initialized")
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Stop the queue while retaining the configured action registrations."""
    runtime: ActivityImagesRuntime | None = hass.data.get(DOMAIN, {}).get(entry.entry_id)
    if runtime is not None:
        if not await hass.config_entries.async_unload_platforms(entry, ["sensor", "ai_task"]):
            return False
        _async_remove_manager(hass)
        runtime.generator.image_saved_callback = None
        await runtime.calendar.async_shutdown()
        if runtime.deletions is not None:
            await runtime.deletions.async_shutdown()
        await runtime.queue.async_shutdown()
        hass.data[DOMAIN].pop(entry.entry_id, None)
    return True


async def _async_register_manager(hass: HomeAssistant) -> None:
    from .library_api import async_register_manager
    await async_register_manager(hass)


def _async_remove_manager(hass: HomeAssistant) -> None:
    from .library_api import async_remove_manager
    async_remove_manager(hass)

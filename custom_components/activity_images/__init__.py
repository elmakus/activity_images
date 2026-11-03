"""Standalone Activity Images integration and public actions.

The image engine, matching cache, and graphics pipeline are kept independent
from Home Tasks. Actions are registered in async_setup, as recommended by HA.
"""
from __future__ import annotations

from dataclasses import dataclass
import logging
import re
from pathlib import Path

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv

from .calendar_adapter import ActivityCalendarAdapter
from .const import DOMAIN
from .engine import ActivityImageGenerator
from .image_cache import ActivityImageCache, async_get_cache
from .image_queue import ActivityImageQueue

_LOGGER = logging.getLogger(__name__)

MAX_TITLES_PER_REQUEST = 64
_LOCAL_PNG = re.compile(r"^/local/activity_images/([A-Za-z0-9_.-]+\.png)(?:\?v=[A-Za-z0-9_.-]+)?$")
_TITLE = vol.All(cv.string, vol.Length(min=1, max=255))
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
        path = Path(hass.config.path("www", "activity_images", filename))
        if not await hass.async_add_executor_job(path.is_file):
            raise HomeAssistantError("Local PNG does not exist")
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


async def _reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Load the one independent image generator and its persistent queue."""
    settings = dict(entry.data)
    settings.update(entry.options)
    cache = await async_get_cache(hass)
    generator = ActivityImageGenerator(hass, cache, settings)
    queue = ActivityImageQueue(hass, generator)
    await queue.async_setup()
    calendar = ActivityCalendarAdapter(hass, cache, queue, settings)
    runtime = ActivityImagesRuntime(
        generator=generator, queue=queue, cache=cache, calendar=calendar,
    )
    entry.runtime_data = runtime
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = runtime
    generator.image_saved_callback = calendar.image_saved
    try:
        await hass.config_entries.async_forward_entry_setups(entry, ["sensor"])
        await calendar.async_start()
    except Exception:
        generator.image_saved_callback = None
        await calendar.async_shutdown()
        await queue.async_shutdown()
        hass.data[DOMAIN].pop(entry.entry_id, None)
        raise
    entry.async_on_unload(entry.add_update_listener(_reload_entry))
    _LOGGER.info("Activity Images standalone generator and calendar library initialized")
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Stop the queue while retaining the configured action registrations."""
    runtime: ActivityImagesRuntime | None = hass.data.get(DOMAIN, {}).get(entry.entry_id)
    if runtime is not None:
        if not await hass.config_entries.async_unload_platforms(entry, ["sensor"]):
            return False
        runtime.generator.image_saved_callback = None
        await runtime.calendar.async_shutdown()
        await runtime.queue.async_shutdown()
        hass.data[DOMAIN].pop(entry.entry_id, None)
    return True

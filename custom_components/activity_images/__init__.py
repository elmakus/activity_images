"""Activity Images Home Assistant entry point and public services."""
from __future__ import annotations

import logging

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse
from homeassistant.helpers import config_validation as cv

from .const import DOMAIN
from .engine import ActivityImageGenerator
from .image_cache import async_get_cache
from .image_queue import ActivityImageQueue

_LOGGER = logging.getLogger(__name__)
SERVICES = ("generate_image", "resolve_images", "queue_status", "cancel_queued", "clear_errors", "forget_image")


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    hass.data.setdefault(DOMAIN, {})
    return True


async def _reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up a single standalone image generator (no Home Tasks dependency)."""
    settings = dict(entry.data)
    settings.update(entry.options)
    cache = await async_get_cache(hass)
    engine = ActivityImageGenerator(hass, cache, settings)
    queue = ActivityImageQueue(hass, engine)
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = {
        "generator": engine, "queue": queue, "cache": cache,
    }
    await queue.async_setup()
    entry.async_on_unload(entry.add_update_listener(_reload_entry))

    async def generate(call: ServiceCall) -> dict:
        return await engine.async_generate(
            call.data["title"], force=call.data.get("force", False)
        )

    async def resolve(call: ServiceCall) -> dict:
        return await queue.async_resolve(
            call.data["titles"], enqueue_missing=call.data.get("enqueue_missing", False)
        )

    async def status(call: ServiceCall) -> dict:
        return queue.status()

    async def cancel(call: ServiceCall) -> dict:
        return {"cancelled": await queue.async_cancel(call.data["titles"])}

    async def clear(call: ServiceCall) -> dict:
        return {"cleared": await queue.async_clear_errors()}

    async def forget(call: ServiceCall) -> dict:
        return {"removed": await cache.async_forget(
            title=call.data.get("title"), url=call.data.get("image_url")
        )}

    hass.services.async_register(
        DOMAIN, "generate_image", generate,
        schema=vol.Schema({
            vol.Required("title"): vol.All(cv.string, vol.Length(min=1, max=255)),
            vol.Optional("force", default=False): cv.boolean,
        }), supports_response=SupportsResponse.OPTIONAL,
    )
    hass.services.async_register(
        DOMAIN, "resolve_images", resolve,
        schema=vol.Schema({
            vol.Required("titles"): vol.All(cv.ensure_list, [cv.string]),
            vol.Optional("enqueue_missing", default=False): cv.boolean,
        }), supports_response=SupportsResponse.OPTIONAL,
    )
    hass.services.async_register(
        DOMAIN, "queue_status", status,
        supports_response=SupportsResponse.OPTIONAL,
    )
    hass.services.async_register(
        DOMAIN, "cancel_queued", cancel,
        schema=vol.Schema({
            vol.Required("titles"): vol.All(cv.ensure_list, [cv.string]),
        }), supports_response=SupportsResponse.OPTIONAL,
    )
    hass.services.async_register(
        DOMAIN, "clear_errors", clear,
        supports_response=SupportsResponse.OPTIONAL,
    )
    hass.services.async_register(
        DOMAIN, "forget_image", forget,
        schema=vol.Schema({
            vol.Optional("title"): cv.string,
            vol.Optional("image_url"): cv.string,
        }), supports_response=SupportsResponse.OPTIONAL,
    )
    _LOGGER.info("Activity Images stage 1 engine initialized")
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    state = hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)
    if state:
        await state["queue"].async_shutdown()
    for name in SERVICES:
        if hass.services.has_service(DOMAIN, name):
            hass.services.async_remove(DOMAIN, name)
    return True

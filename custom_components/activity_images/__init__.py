"""Standalone Activity Images library manager and compatible public actions.

The image engine, matching cache, and graphics pipeline are kept independent
from Home Tasks. Actions are registered in async_setup, as recommended by HA.
"""
from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from collections.abc import Awaitable, Callable

import probatio as vol
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from homeassistant.core import ServiceResponse
    from homeassistant.helpers.typing import ConfigType
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.service import async_register_admin_service

from .calendar_adapter import ActivityCalendarAdapter
from .const import DOMAIN
from .engine import ActivityImageGenerator
from .image_cache import ActivityImageCache, MappingConflict
from .image_queue import ActivityImageQueue
from .library import ActivityImageLibrary, _read_owned, _verify, validate_title
from .library_deletion import ActivityImageDeletions
from .provider_repair import ProviderRepair
from .settings import get_runtime as _get_runtime, migrate_settings
from .settings_schema import ConfigEntrySettings

_LOGGER = logging.getLogger(__name__)

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

MAX_TITLES_PER_REQUEST = 64
_LOCAL_PNG = re.compile(r"^/local/activity_images/([A-Za-z0-9_.-]+\.png)(?:\?v=[A-Za-z0-9_.-]+)?$")


def _valid_title(value: str) -> str:
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
    provider_repair: ProviderRepair
    deletions: ActivityImageDeletions | None = None
    ready: bool = False


type ActivityImagesConfigEntry = ConfigEntry[ActivityImagesRuntime | None]


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register actions once, regardless of config-entry availability."""

    async def generate(call: ServiceCall) -> ServiceResponse:
        result = await _get_runtime(hass).queue.async_generate_for_action(
            call.data["title"], force=call.data.get("force", False)
        )
        # GenerationResult contains only JSON scalar values.
        return cast("ServiceResponse", result)

    async def resolve(call: ServiceCall) -> ServiceResponse:
        result = await _get_runtime(hass).queue.async_resolve(
            call.data["titles"],
            enqueue_missing=call.data.get("enqueue_missing", False),
        )
        return cast("ServiceResponse", result)

    async def status(call: ServiceCall) -> ServiceResponse:
        return cast("ServiceResponse", _get_runtime(hass).queue.status())

    async def refresh_calendars(call: ServiceCall) -> ServiceResponse:
        result = await _get_runtime(hass).calendar.async_refresh()
        if result["error"]:
            raise HomeAssistantError("Calendar refresh failed; cached images retained. Check Activity Images diagnostics", translation_domain=DOMAIN, translation_key="calendar_refresh_failed")
        return cast("ServiceResponse", result)

    async def cancel(call: ServiceCall) -> ServiceResponse:
        return {
            "cancelled": await _get_runtime(hass).queue.async_cancel(call.data["titles"])
        }

    async def clear(call: ServiceCall) -> ServiceResponse:
        return {"cleared": await _get_runtime(hass).queue.async_clear_errors(preserve_retry_blocks=True)}

    async def link_existing(call: ServiceCall) -> ServiceResponse:
        """Remember a verified local PNG, without regenerating it."""
        title = call.data["title"]
        url = call.data["image_url"]
        match = _LOCAL_PNG.fullmatch(url)
        if match is None or match.group(1).startswith("."):
            raise ServiceValidationError("Only /local/activity_images/<filename>.png can be linked", translation_domain=DOMAIN, translation_key="invalid_link_url")
        filename = match.group(1)
        root = Path(hass.config.path("www", "activity_images"))
        def verify() -> None:
            data = _read_owned(root, filename)
            if _verify(data)[2] != "PNG":
                raise ValueError("Expected a valid PNG")
        try:
            await hass.async_add_executor_job(verify)
        except (OSError, ValueError) as exc:
            raise ServiceValidationError("Local PNG is missing or invalid", translation_domain=DOMAIN, translation_key="invalid_local_png") from exc
        runtime = _get_runtime(hass)
        try:
            await runtime.cache.async_link(title, url, replace=call.data.get("replace", False))
        except MappingConflict as exc:
            raise ServiceValidationError(
                str(exc), translation_domain=DOMAIN,
                translation_key="confirm_replacement" if str(exc) == "The title is already linked; confirm replacement" else "mapping_conflict",
            ) from exc
        await runtime.calendar.async_reconcile()
        return {"title": title, "image_url": url, "linked": True}

    async def forget(call: ServiceCall) -> ServiceResponse:
        title = call.data.get("title")
        image_url = call.data.get("image_url")
        if not title and not image_url:
            raise ServiceValidationError("Specify a title or image_url to forget", translation_domain=DOMAIN, translation_key="forget_requires_target")
        runtime = _get_runtime(hass)
        removed = await runtime.cache.async_forget(title=title, url=image_url)
        await runtime.calendar.async_reconcile()
        return {"removed": removed}

    def register(name: str, handler: Callable[[ServiceCall], Awaitable[ServiceResponse]], *, supports_response: SupportsResponse, schema: vol.Schema = vol.Schema({}, extra=vol.PREVENT_EXTRA)) -> None:
        async def handle(call: ServiceCall) -> ServiceResponse:
            try:
                return await handler(call)
            except HomeAssistantError:
                raise
            except MappingConflict as exc:
                raise ServiceValidationError(str(exc), translation_domain=DOMAIN, translation_key="mapping_conflict") from exc
            except ValueError as exc:
                raise ServiceValidationError("Activity Images rejected the supplied action values", translation_domain=DOMAIN, translation_key="invalid_action_values") from exc
            except OSError as exc:
                raise HomeAssistantError("Activity Images could not access or persist local data", translation_domain=DOMAIN, translation_key="local_storage_failed") from exc
        async_register_admin_service(hass, DOMAIN, name, handle, schema=schema, supports_response=supports_response)

    register(
        "generate_image", generate,
        schema=vol.Schema({
            vol.Required("title"): _TITLE,
            vol.Optional("force", default=False): cv.boolean,
        }),
        supports_response=SupportsResponse.OPTIONAL,
    )
    register(
        "resolve_images", resolve,
        schema=vol.Schema({
            vol.Required("titles"): _TITLES,
            vol.Optional("enqueue_missing", default=False): cv.boolean,
        }),
        supports_response=SupportsResponse.OPTIONAL,
    )
    register(
        "queue_status", status,
        supports_response=SupportsResponse.OPTIONAL,
    )
    register(
        "cancel_queued", cancel,
        schema=vol.Schema({vol.Required("titles"): _TITLES}),
        supports_response=SupportsResponse.OPTIONAL,
    )
    register(
        "clear_errors", clear,
        supports_response=SupportsResponse.OPTIONAL,
    )
    register(
        "link_existing_image", link_existing,
        schema=vol.Schema({vol.Required("title"): _TITLE,
                           vol.Required("image_url"): cv.string,
                           vol.Optional("replace", default=False): cv.boolean}),
        supports_response=SupportsResponse.OPTIONAL,
    )
    register(
        "refresh_calendars", refresh_calendars,
        supports_response=SupportsResponse.OPTIONAL,
    )
    register(
        "forget_image", forget,
        schema=vol.Schema({
            vol.Optional("title"): _TITLE,
            vol.Optional("image_url"): cv.string,
        }),
        supports_response=SupportsResponse.OPTIONAL,
    )
    return True


async def _async_update_options(hass: HomeAssistant, entry: ActivityImagesConfigEntry) -> None:
    """Keep paid work and runtime identities intact when native options change."""
    runtime: ActivityImagesRuntime | None = getattr(entry, "runtime_data", None)
    if runtime is not None:
        runtime.provider_repair.update()
        await runtime.calendar.async_update_settings(runtime.generator.settings)


async def async_setup_entry(hass: HomeAssistant, entry: ActivityImagesConfigEntry) -> bool:
    """Load the one independent image generator and its persistent queue."""
    migrate_settings(hass, entry)
    settings = ConfigEntrySettings(entry)
    cache = ActivityImageCache(hass)
    await cache.async_setup()
    generator = ActivityImageGenerator(hass, cache, settings)
    queue = ActivityImageQueue(hass, generator)
    calendar = ActivityCalendarAdapter(hass, cache, queue, settings)
    runtime = ActivityImagesRuntime(
        generator=generator, queue=queue, cache=cache, calendar=calendar,
        library=ActivityImageLibrary(hass, cache),
        provider_repair=ProviderRepair(hass, entry),
    )
    runtime.deletions = ActivityImageDeletions(hass, runtime.library, queue, calendar)
    entry.runtime_data = runtime
    generator.image_saved_callback = calendar.image_saved
    manager_registered = False
    platforms_loaded = False
    try:
        await _async_register_manager(hass)
        manager_registered = True
        await calendar.async_setup()
        await queue.async_setup(start_worker=False)
        await runtime.deletions.async_setup()
        await hass.config_entries.async_forward_entry_setups(entry, ["sensor", "ai_task"])
        platforms_loaded = True
        runtime.provider_repair.update()
        await calendar.async_start()
        runtime.ready = True
        queue.start()
    except (Exception, asyncio.CancelledError):
        runtime.provider_repair.close()
        if platforms_loaded:
            await hass.config_entries.async_unload_platforms(entry, ["sensor", "ai_task"])
        if manager_registered:
            _async_remove_manager(hass)
        generator.image_saved_callback = None
        await calendar.async_shutdown()
        await runtime.deletions.async_shutdown()
        await queue.async_shutdown()
        runtime.ready = False
        entry.runtime_data = None
        raise
    entry.async_on_unload(entry.add_update_listener(_async_update_options))
    _LOGGER.info("Activity Images standalone generator and calendar library initialized")
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ActivityImagesConfigEntry) -> bool:
    """Stop the queue while retaining the configured action registrations."""
    runtime: ActivityImagesRuntime | None = getattr(entry, "runtime_data", None)
    if runtime is not None:
        if not await hass.config_entries.async_unload_platforms(entry, ["sensor", "ai_task"]):
            return False
        _async_remove_manager(hass)
        runtime.provider_repair.close()
        runtime.ready = False
        runtime.generator.image_saved_callback = None
        await runtime.calendar.async_shutdown()
        if runtime.deletions is not None:
            await runtime.deletions.async_shutdown()
        await runtime.queue.async_shutdown()
        entry.runtime_data = None
    return True


async def _async_register_manager(hass: HomeAssistant) -> None:
    from .library_api import async_register_manager
    await async_register_manager(hass)


def _async_remove_manager(hass: HomeAssistant) -> None:
    from .library_api import async_remove_manager
    async_remove_manager(hass)

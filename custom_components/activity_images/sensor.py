"""Read-only sensor exposing image URLs and Calendar Card Pro rules."""
from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from . import ActivityImagesConfigEntry

from homeassistant.components.sensor import SensorEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .calendar_adapter import ActivityCalendarAdapter
from .entity import service_device_info

# A local read-only snapshot; no platform I/O or outbound actions.
PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ActivityImagesConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Create the singleton image-library sensor for the config entry."""
    runtime = entry.runtime_data
    assert runtime is not None  # Platform setup follows runtime initialization.
    async_add_entities([ActivityImagesLibrarySensor(entry.entry_id, runtime.calendar)])


class ActivityImagesLibrarySensor(SensorEntity):
    """Expose the local map even when optional calendar sources are stale.

    Scan freshness and errors are reported separately; cached links stay usable.
    """

    _attr_has_entity_name = True
    _attr_translation_key = "library"
    _attr_should_poll = False

    def __init__(self, entry_id: str, calendar: ActivityCalendarAdapter) -> None:
        self._attr_unique_id = f"{entry_id}_library"
        self._attr_device_info = service_device_info(entry_id)
        self._calendar = calendar
        self._unsubscribe: Callable[[], None] | None = None

    @property
    def native_value(self) -> int:  # pyright: ignore[reportIncompatibleVariableOverride]
        """Keep the count dynamic rather than caching the initial calendar map."""
        return len(self._calendar.images)

    @property
    def extra_state_attributes(self) -> dict[str, object]:  # pyright: ignore[reportIncompatibleVariableOverride]
        """Keep the bounded image map and Calendar Card Pro rules dynamic."""
        return {
            "images": dict(self._calendar.images),
            "calendar_rules": self._calendar.rules,
            "tracked_titles": self._calendar.status()["tracked_titles"],
            "calendars": list(self._calendar.calendars),
            "automatic_generation": self._calendar.auto_generate,
            "last_scan": self._calendar.last_scan,
            "scan_error": self._calendar.last_error,
        }

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self._unsubscribe = self._calendar.add_listener(self._calendar_changed)

    @callback
    def _calendar_changed(self) -> None:
        self.async_write_ha_state()

    async def async_will_remove_from_hass(self) -> None:
        if self._unsubscribe is not None:
            self._unsubscribe()
            self._unsubscribe = None
        await super().async_will_remove_from_hass()

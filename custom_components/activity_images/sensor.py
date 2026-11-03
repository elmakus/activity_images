"""Read-only sensor exposing image URLs and Calendar Card Pro rules."""
from __future__ import annotations

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .calendar_adapter import ActivityCalendarAdapter


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Create the singleton image-library sensor for the config entry."""
    async_add_entities([ActivityImagesLibrarySensor(entry.entry_id, entry.runtime_data.calendar)])


class ActivityImagesLibrarySensor(SensorEntity):
    """Expose only bounded, pre-rendered data, never performing I/O in properties."""

    _attr_has_entity_name = True
    _attr_name = "Activity Images Library"
    _attr_icon = "mdi:image-multiple"
    _attr_should_poll = False

    def __init__(self, entry_id: str, calendar: ActivityCalendarAdapter) -> None:
        self._attr_unique_id = f"{entry_id}_library"
        self._calendar = calendar
        self._unsubscribe = None

    @property
    def native_value(self) -> int:
        """Number of calendar titles with a usable image."""
        return len(self._calendar.images)

    @property
    def extra_state_attributes(self) -> dict:
        """Expose a safe, bounded map and ready-to-render Calendar Card Pro rules."""
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

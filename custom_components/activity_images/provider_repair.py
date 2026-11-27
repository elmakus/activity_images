"""Actionable configuration repairs, independent of the AI Task entity."""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from . import ActivityImagesConfigEntry
    from homeassistant.core import Event, EventStateChangedData

from collections.abc import Callable

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.event import async_track_state_change_event

from .const import CONF_AI_ENTITY, DOMAIN
from .settings_diagnostics import provider_diagnostics
from .settings_schema import effective_settings

_REPAIR_KEYS = {
    "self_reference": "provider_self_reference",
    "image_generation_unsupported": "provider_image_unsupported",
    "attachments_unsupported": "provider_attachments_unsupported",
}


class ProviderRepair:
    """Monitor selected provider capabilities without probing or dispatching."""

    def __init__(self, hass: HomeAssistant, entry: ActivityImagesConfigEntry) -> None:
        self.hass = hass
        self.entry = entry
        self.issue_id = f"provider_configuration_{entry.entry_id}"
        self._provider: str | None = None
        self._unsubscribe: Callable[[], None] | None = None
        self._closed = False

    @callback
    def update(self) -> None:
        """Rebind after settings edits and evaluate current passive evidence."""
        if self._closed:
            return
        provider = effective_settings(self.entry)[CONF_AI_ENTITY] or None
        if provider != self._provider:
            self._unwatch()
            self._provider = provider
            if provider:
                self._unsubscribe = async_track_state_change_event(
                    self.hass, [provider], self._changed
                )
        self._changed()

    @callback
    def _changed(self, _event: Event[EventStateChangedData] | None = None) -> None:
        if self._closed:
            return
        values = effective_settings(self.entry)
        status = provider_diagnostics(self.hass, self.entry, values)["status"]
        if key := _REPAIR_KEYS.get(status):
            ir.async_create_issue(
                self.hass, DOMAIN, self.issue_id,
                config_entry_id=self.entry.entry_id,
                is_fixable=False, severity=ir.IssueSeverity.WARNING,
                translation_key=key,
            )
        else:
            # No provider is an intentional library-only mode. A transient
            # outage is not evidence of an actionable configuration problem.
            ir.async_delete_issue(self.hass, DOMAIN, self.issue_id)

    @callback
    def _unwatch(self) -> None:
        if self._unsubscribe is not None:
            self._unsubscribe()
            self._unsubscribe = None
        self._provider = None

    @callback
    def close(self) -> None:
        """Remove listeners and the integration-owned issue on unload."""
        self._closed = True
        self._unwatch()
        ir.async_delete_issue(self.hass, DOMAIN, self.issue_id)

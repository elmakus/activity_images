"""Shared settings transactions for the panel and native Home Assistant flows."""

from __future__ import annotations

import hashlib
import json
from contextlib import asynccontextmanager

from homeassistant.exceptions import HomeAssistantError

from .const import CONF_REFERENCE_MEDIA_ID, DOMAIN
from .settings_schema import effective_settings, validate_settings


class StaleSettingsError(ValueError):
    """Another editor changed this entry, or the entry was replaced."""


def get_entry(hass):
    entries = hass.config_entries.async_entries(DOMAIN)
    if len(entries) != 1:
        raise HomeAssistantError("Exactly one Activity Images config entry is required")
    return entries[0]


def revision(entry) -> str:
    serial = json.dumps([entry.entry_id, effective_settings(entry)], sort_keys=True,
                        separators=(",", ":"))
    return hashlib.sha256(serial.encode("utf-8")).hexdigest()


def read_settings(hass) -> dict:
    entry = get_entry(hass)
    return {"values": effective_settings(entry), "revision": revision(entry)}


@asynccontextmanager
async def settings_lock(entry):
    """Serialize calendar/reference changes with scans and deletion commits."""
    runtime = getattr(entry, "runtime_data", None)
    if runtime is None:
        yield
        return
    # This is the first lock in deletion's existing calendar -> queue -> engine order.
    async with runtime.calendar._lock:
        if runtime.calendar._closed:
            raise HomeAssistantError("Activity Images is unloading; retry after setup")
        yield


def options_for_update(entry, changes: dict, expected_revision: str) -> dict:
    """Validate both editors identically; callers hold settings_lock until commit."""
    if revision(entry) != expected_revision:
        raise StaleSettingsError("Settings changed elsewhere. Reload before saving")
    before = effective_settings(entry)
    after = validate_settings(changes, before)
    runtime = getattr(entry, "runtime_data", None)
    if runtime is not None and after[CONF_REFERENCE_MEDIA_ID] != before[CONF_REFERENCE_MEDIA_ID]:
        runtime.deletions.validate_reference(after[CONF_REFERENCE_MEDIA_ID])
    if before == after:
        return dict(entry.options)
    return {**entry.options, **after}


async def save_settings(hass, changes: dict, expected_revision: str) -> dict:
    """Persist through HA once; its update listener applies calendar settings live."""
    entry = get_entry(hass)
    async with settings_lock(entry):
        options = options_for_update(entry, changes, expected_revision)
        changed = options != entry.options
        if changed:
            hass.config_entries.async_update_entry(entry, options=options)
        return {"changed": changed, "revision": revision(entry), "reload": "none"}

"""Shared settings transactions for the panel and native Home Assistant flows."""

from __future__ import annotations

import hashlib
import json
from contextlib import asynccontextmanager
from collections.abc import AsyncIterator, Mapping, Iterable
from typing import TYPE_CHECKING, TypedDict, cast

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from . import ActivityImagesConfigEntry, ActivityImagesRuntime

from homeassistant.exceptions import HomeAssistantError

from .const import (
    CALENDAR_BINDINGS,
    CONF_AI_ENTITY,
    CONF_AUTO_CALENDARS,
    CONF_CALENDARS,
    CONF_REFERENCE_MEDIA_ID,
    DOMAIN,
)
from .settings_schema import Settings, effective_settings, validate_settings


class StaleSettingsError(ValueError):
    """Another editor changed this entry, or the entry was replaced."""


def get_entry(hass: HomeAssistant) -> ActivityImagesConfigEntry:
    entries = hass.config_entries.async_entries(DOMAIN)
    if len(entries) != 1:
        raise HomeAssistantError("Exactly one Activity Images config entry is required", translation_domain=DOMAIN, translation_key="entry_required")
    return entries[0]


def get_runtime(hass: HomeAssistant) -> ActivityImagesRuntime:
    """Resolve the singleton native runtime only after successful setup."""
    entries = hass.config_entries.async_entries(DOMAIN)
    runtime: ActivityImagesRuntime | None = getattr(entries[0], "runtime_data", None) if len(entries) == 1 else None
    if runtime is None or not runtime.ready:
        raise HomeAssistantError("Activity Images is not configured or ready", translation_domain=DOMAIN, translation_key="not_ready")
    return runtime


def calendar_identity(hass: HomeAssistant, entity_id: str) -> str | None:
    """Entity IDs can be reused; registry IDs identify the selected calendar."""
    from homeassistant.helpers import entity_registry as er
    entity = er.async_get(hass).async_get(entity_id)
    return entity.id if entity is not None else None


def is_own_provider_entity(hass: HomeAssistant, entry: ActivityImagesConfigEntry | None, entity_id: str | None) -> bool:
    """True when a provider is this entry's own wrapper (rename-safe).

    Ownership uses registry platform/config_entry, never a hardcoded entity ID.
    Unknown/unregistered providers return False and remain savable; entry None
    matches any Activity Images wrapper for pre-entry config creation.
    A registry failure raises and must refuse the write/dispatch, never allow it.
    """
    if not entity_id or not entity_id.startswith("ai_task."):
        return False
    from homeassistant.helpers import entity_registry as er
    entity = er.async_get(hass).async_get(entity_id)
    if entity is None:
        return False
    if getattr(entity, "platform", None) != DOMAIN:
        return False
    if entry is None:
        return True
    return bool(getattr(entity, "config_entry_id", None) == entry.entry_id)


def own_provider_entity_ids(hass: HomeAssistant, entry: ActivityImagesConfigEntry) -> list[str]:
    """Current wrapper entity IDs for selector/panel exclusions (additive)."""
    from homeassistant.helpers import entity_registry as er
    registry = er.async_get(hass)
    return sorted(
        entity.entity_id for entity in er.async_entries_for_config_entry(registry, entry.entry_id)
        if entity.entity_id.startswith("ai_task.") and entity.platform == DOMAIN
    )


def migrate_settings(hass: HomeAssistant, entry: ActivityImagesConfigEntry) -> None:
    """Migrate stored settings before any queue/scanner can authorize work."""
    values = validate_settings(effective_settings(entry))
    options = dict(entry.options)
    data = dict(entry.data)
    # The removed endpoint must not survive in active config-entry storage.
    data.pop("lucida_url", None)
    options.pop("lucida_url", None)
    if CONF_AUTO_CALENDARS not in entry.options and CONF_AUTO_CALENDARS not in entry.data:
        # Preserve unknown options, and neutralize the old global flag for rollback.
        bindings = {entity: calendar_identity(hass, entity) for entity in values[CONF_CALENDARS]}
        options.update(values)
        options[CALENDAR_BINDINGS] = bindings
    if data != entry.data:
        hass.config_entries.async_update_entry(entry, data=data, options=options)
    elif options != entry.options:
        hass.config_entries.async_update_entry(entry, options=options)


def revision(entry: ActivityImagesConfigEntry) -> str:
    serial = json.dumps([entry.entry_id, effective_settings(entry),
                         entry.options.get(CALENDAR_BINDINGS, entry.data.get(CALENDAR_BINDINGS, {}))], sort_keys=True,
                        separators=(",", ":"))
    return hashlib.sha256(serial.encode("utf-8")).hexdigest()


class SettingsSnapshot(TypedDict):
    values: Settings
    revision: str
    excluded_providers: list[str]


class SettingsSaveResult(TypedDict):
    changed: bool
    revision: str
    reload: str


def read_settings(hass: HomeAssistant) -> SettingsSnapshot:
    entry = get_entry(hass)
    return {"values": effective_settings(entry), "revision": revision(entry),
            "excluded_providers": own_provider_entity_ids(hass, entry)}


@asynccontextmanager
async def settings_lock(entry: ActivityImagesConfigEntry) -> AsyncIterator[None]:
    """Serialize calendar/reference changes with scans and deletion commits."""
    runtime: ActivityImagesRuntime | None = getattr(entry, "runtime_data", None)
    if runtime is None:
        yield
        return
    # This is the first lock in deletion's existing calendar -> queue -> engine order.
    async with runtime.calendar._lock:
        if runtime.calendar._closed:
            raise HomeAssistantError("Activity Images is unloading; retry after setup", translation_domain=DOMAIN, translation_key="unloading")
        yield


def options_for_update(hass: HomeAssistant, entry: ActivityImagesConfigEntry, changes: Mapping[str, object], expected_revision: str) -> dict[str, object]:
    """Validate both editors identically; callers hold settings_lock until commit."""
    if revision(entry) != expected_revision:
        raise StaleSettingsError("Settings changed elsewhere. Reload before saving")
    before = effective_settings(entry)
    after = validate_settings(changes, before)
    if after[CONF_AI_ENTITY] and is_own_provider_entity(hass, entry, after[CONF_AI_ENTITY]):
        raise ValueError("Activity Images cannot use itself as its image provider")
    runtime: ActivityImagesRuntime | None = getattr(entry, "runtime_data", None)
    if runtime is not None and after[CONF_REFERENCE_MEDIA_ID] != before[CONF_REFERENCE_MEDIA_ID]:
        assert runtime.deletions is not None
        runtime.deletions.validate_reference(after[CONF_REFERENCE_MEDIA_ID])
    if before == after:
        return dict(entry.options)
    bindings = entry.options.get(CALENDAR_BINDINGS, entry.data.get(CALENDAR_BINDINGS, {}))
    bindings = bindings if isinstance(bindings, dict) else {}
    selected = {
        entity: bindings[entity] for entity in after[CONF_CALENDARS]
        if entity in before[CONF_CALENDARS] and entity in bindings
    }
    selected.update({entity: calendar_identity(hass, entity) for entity in after[CONF_CALENDARS]
                     if entity not in before[CONF_CALENDARS]})
    return {**entry.options, **after, CALENDAR_BINDINGS: selected}


def automatic_confirmation_ids(before: Mapping[str, object], after: Mapping[str, object]) -> set[str]:
    automatic = set(cast(Iterable[str], after.get(CONF_AUTO_CALENDARS, [])))
    if before.get(CONF_AI_ENTITY) != after.get(CONF_AI_ENTITY):
        return automatic
    return automatic - set(cast(Iterable[str], before.get(CONF_AUTO_CALENDARS, [])))


async def save_settings(hass: HomeAssistant, changes: Mapping[str, object], expected_revision: str, *, confirm_auto: bool = False) -> SettingsSaveResult:
    """Persist through HA once; its update listener applies calendar settings live."""
    entry = get_entry(hass)
    async with settings_lock(entry):
        options = options_for_update(hass, entry, changes, expected_revision)
        before = effective_settings(entry)
        added = automatic_confirmation_ids(before, {**before, **options})
        if added and confirm_auto is not True:
            raise ValueError("Enabling calendar automatic generation requires explicit paid confirmation")
        changed = options != entry.options
        if changed:
            hass.config_entries.async_update_entry(entry, options=options)
        return {"changed": changed, "revision": revision(entry), "reload": "none"}

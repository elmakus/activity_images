"""Administrator-only Settings WebSocket API.

ConfigEntry.options is the sole persistence layer. No parallel .storage file is
created. The existing options update listener owns integration reloads.
"""
from __future__ import annotations

import hashlib
import json

import probatio as vol
from homeassistant.components import websocket_api
from homeassistant.exceptions import HomeAssistantError

from .const import DOMAIN
from .settings_schema import effective_settings, validate_settings


class StaleSettingsError(ValueError):
    """A different editor saved settings after the client read them."""


def _entry(hass):
    entries = hass.config_entries.async_entries(DOMAIN)
    if len(entries) != 1:
        raise HomeAssistantError("Exactly one Activity Images config entry is required")
    return entries[0]


def _revision(values: dict) -> str:
    serial = json.dumps(values, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serial.encode("utf-8")).hexdigest()


def read_settings(hass) -> dict:
    """Return an immutable-by-convention UI snapshot and optimistic revision."""
    values = effective_settings(_entry(hass))
    return {"values": values, "revision": _revision(values)}


def save_settings(hass, changes: dict, revision: str) -> dict:
    """Validate, compare revisions and update options exactly once if changed."""
    entry = _entry(hass)
    before = effective_settings(entry)
    if _revision(before) != revision:
        raise StaleSettingsError("Settings changed elsewhere. Refresh before saving")
    after = validate_settings(changes, before)
    if before == after:
        return {"changed": False, "revision": revision, "reload": "none"}
    # Existing unrelated options are retained; initial entry.data is never mutated.
    hass.config_entries.async_update_entry(entry, options={**entry.options, **after})
    return {"changed": True, "revision": _revision(after), "reload": "scheduled"}


def _get_handler():
    @websocket_api.websocket_command({"type": f"{DOMAIN}/settings/get"})
    @websocket_api.require_admin
    @websocket_api.async_response
    async def handle(hass, connection, msg):
        try:
            connection.send_result(msg["id"], read_settings(hass))
        except HomeAssistantError as exc:
            connection.send_error(msg["id"], "settings_error", str(exc))
    return handle


def _save_handler():
    @websocket_api.websocket_command({
        "type": f"{DOMAIN}/settings/save",
        vol.Required("values"): dict,
        vol.Required("revision"): vol.All(str, vol.Length(min=64, max=64)),
    })
    @websocket_api.require_admin
    @websocket_api.async_response
    async def handle(hass, connection, msg):
        try:
            result = save_settings(hass, msg["values"], msg["revision"])
        except StaleSettingsError as exc:
            connection.send_error(msg["id"], "settings_conflict", str(exc))
        except (ValueError, HomeAssistantError) as exc:
            connection.send_error(msg["id"], "settings_error", str(exc))
        else:
            connection.send_result(msg["id"], result)
    return handle


def async_register_settings(hass):
    websocket_api.async_register_command(hass, _get_handler())
    websocket_api.async_register_command(hass, _save_handler())

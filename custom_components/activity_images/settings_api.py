"""Administrator-only Settings WebSocket transport; no persistence policy here."""
from __future__ import annotations

import probatio as vol
from homeassistant.components import websocket_api
from homeassistant.exceptions import HomeAssistantError

from .const import DOMAIN
from .settings import StaleSettingsError, get_entry, read_settings, save_settings
from .settings_diagnostics import diagnostics


@websocket_api.websocket_command({"type": f"{DOMAIN}/settings/get"})
@websocket_api.require_admin
@websocket_api.async_response
async def settings_get_handler(hass, connection, msg):
    try:
        connection.send_result(msg["id"], read_settings(hass))
    except HomeAssistantError as exc:
        connection.send_error(msg["id"], "settings_error", str(exc))


@websocket_api.websocket_command({
    "type": f"{DOMAIN}/settings/save",
    vol.Required("values"): dict,
    vol.Required("revision"): vol.All(str, vol.Length(min=64, max=64)),
    vol.Optional("confirm_auto", default=False): bool,
})
@websocket_api.require_admin
@websocket_api.async_response
async def settings_save_handler(hass, connection, msg):
    try:
        result = await save_settings(hass, msg["values"], msg["revision"], confirm_auto=msg.get("confirm_auto", False))
    except StaleSettingsError as exc:
        connection.send_error(msg["id"], "settings_conflict", str(exc))
    except (ValueError, HomeAssistantError) as exc:
        connection.send_error(msg["id"], "settings_error", str(exc))
    else:
        connection.send_result(msg["id"], result)


@websocket_api.websocket_command({"type": f"{DOMAIN}/settings/diagnostics"})
@websocket_api.require_admin
@websocket_api.async_response
async def settings_diagnostics_handler(hass, connection, msg):
    try:
        connection.send_result(msg["id"], diagnostics(hass, get_entry(hass)))
    except HomeAssistantError as exc:
        connection.send_error(msg["id"], "settings_error", str(exc))


def async_register_settings(hass):
    for handler in (settings_get_handler, settings_save_handler, settings_diagnostics_handler):
        websocket_api.async_register_command(hass, handler)

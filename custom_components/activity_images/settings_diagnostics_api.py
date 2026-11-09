"""Authenticated, read-only Settings diagnostics WebSocket command."""
from __future__ import annotations

from homeassistant.components import websocket_api
from homeassistant.exceptions import HomeAssistantError

from .const import DOMAIN
from .settings_api import _entry
from .settings_diagnostics import diagnostics


@websocket_api.websocket_command({"type": f"{DOMAIN}/settings/diagnostics"})
@websocket_api.require_admin
@websocket_api.async_response
async def settings_diagnostics_handler(hass, connection, msg):
    try:
        connection.send_result(msg["id"], diagnostics(hass, _entry(hass)))
    except HomeAssistantError as exc:
        connection.send_error(msg["id"], "settings_error", str(exc))


def async_register_settings_diagnostics(hass):
    websocket_api.async_register_command(hass, settings_diagnostics_handler)

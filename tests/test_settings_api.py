"""Admin Settings API behavior without a live Home Assistant instance."""
import importlib
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1] / "custom_components" / "activity_images"
for name, root in (("custom_components", ROOT.parent), ("custom_components.activity_images", ROOT)):
    if name not in sys.modules:
        pkg = types.ModuleType(name)
        pkg.__path__ = [str(root)]
        sys.modules[name] = pkg

# The HA-free transport decorators are passive; the production decorators
# perform authentication and schema validation on actual WebSocket messages.
if "homeassistant" not in sys.modules:
    pkg = types.ModuleType("homeassistant")
    pkg.__path__ = []
    sys.modules["homeassistant"] = pkg
if "homeassistant.components" not in sys.modules:
    pkg = types.ModuleType("homeassistant.components")
    pkg.__path__ = []
    sys.modules["homeassistant.components"] = pkg
ws = sys.modules.setdefault("homeassistant.components.websocket_api", types.ModuleType("websocket_api"))
for name in ("websocket_command", "require_admin", "async_response"):
    setattr(ws, name, (lambda fn: fn) if name != "websocket_command" else (lambda schema: lambda fn: fn))
sys.modules["homeassistant.components"].websocket_api = ws
exc = sys.modules.setdefault("homeassistant.exceptions", types.ModuleType("homeassistant.exceptions"))
if not hasattr(exc, "HomeAssistantError"):
    exc.HomeAssistantError = type("HomeAssistantError", (Exception,), {})

api = importlib.import_module("custom_components.activity_images.settings_api")
const = importlib.import_module("custom_components.activity_images.const")


class Entries:
    def __init__(self):
        self.entry = types.SimpleNamespace(data={const.CONF_AI_ENTITY: "ai_task.mock"}, options={})
        self.updates = []

    def async_entries(self, domain):
        assert domain == const.DOMAIN
        return [self.entry]

    def async_update_entry(self, entry, *, options):
        self.updates.append(options)
        entry.options = options


def test_read_noop_update_and_real_update():
    hass = types.SimpleNamespace(config_entries=Entries())
    start = api.read_settings(hass)
    assert start["values"][const.CONF_AI_ENTITY] == "ai_task.mock"
    result = api.save_settings(hass, {}, start["revision"])
    assert result["changed"] is False
    assert not hass.config_entries.updates
    changed = api.save_settings(hass, {const.CONF_LOOKAHEAD_DAYS: 14}, start["revision"])
    assert changed["changed"] is True
    assert changed["reload"] == "scheduled"
    assert len(hass.config_entries.updates) == 1
    assert hass.config_entries.entry.data == {const.CONF_AI_ENTITY: "ai_task.mock"}
    assert api.read_settings(hass)["values"][const.CONF_LOOKAHEAD_DAYS] == 14


def test_stale_editor_and_invalid_changes_do_not_persist():
    hass = types.SimpleNamespace(config_entries=Entries())
    original = api.read_settings(hass)["revision"]
    api.save_settings(hass, {const.CONF_AUTO_GENERATE: True}, original)
    with pytest.raises(api.StaleSettingsError):
        api.save_settings(hass, {const.CONF_LOOKAHEAD_DAYS: 3}, original)
    with pytest.raises(ValueError):
        api.save_settings(hass, {const.CONF_AI_ENTITY: "sensor.unsafe"}, api.read_settings(hass)["revision"])
    assert len(hass.config_entries.updates) == 1


def test_no_config_entry_rejects_request():
    hass = types.SimpleNamespace(config_entries=types.SimpleNamespace(async_entries=lambda _: []))
    with pytest.raises(exc.HomeAssistantError):
        api.read_settings(hass)

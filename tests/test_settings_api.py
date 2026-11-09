"""Admin Settings API behavior without a live Home Assistant instance."""
import asyncio
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
sys.modules["homeassistant.components"].__dict__["websocket_api"] = ws
exc = sys.modules.setdefault("homeassistant.exceptions", types.ModuleType("homeassistant.exceptions"))
if not hasattr(exc, "HomeAssistantError"):
    exc.__dict__["HomeAssistantError"] = type("HomeAssistantError", (Exception,), {})

api = importlib.import_module("custom_components.activity_images.settings_api")
const = importlib.import_module("custom_components.activity_images.const")


class Entries:
    def __init__(self):
        self.entry = types.SimpleNamespace(entry_id="current", data={const.CONF_AI_ENTITY: "ai_task.mock"}, options={})
        self.updates = []

    def async_entries(self, domain):
        assert domain == const.DOMAIN
        return [self.entry]

    def async_update_entry(self, entry, *, options):
        self.updates.append(options)
        entry.options = options


@pytest.mark.asyncio
async def test_read_noop_update_and_real_update():
    hass = types.SimpleNamespace(config_entries=Entries())
    start = api.read_settings(hass)
    assert start["values"][const.CONF_AI_ENTITY] == "ai_task.mock"
    result = await api.save_settings(hass, {}, start["revision"])
    assert result["changed"] is False
    assert not hass.config_entries.updates
    changed = await api.save_settings(hass, {const.CONF_LOOKAHEAD_DAYS: 14}, start["revision"])
    assert changed["changed"] is True
    assert changed["reload"] == "none"
    assert len(hass.config_entries.updates) == 1
    assert hass.config_entries.entry.data == {const.CONF_AI_ENTITY: "ai_task.mock"}
    assert api.read_settings(hass)["values"][const.CONF_LOOKAHEAD_DAYS] == 14


@pytest.mark.asyncio
async def test_stale_editor_and_invalid_changes_do_not_persist():
    hass = types.SimpleNamespace(config_entries=Entries())
    original = api.read_settings(hass)["revision"]
    await api.save_settings(hass, {const.CONF_AUTO_GENERATE: True}, original)
    with pytest.raises(api.StaleSettingsError):
        await api.save_settings(hass, {const.CONF_LOOKAHEAD_DAYS: 3}, original)
    with pytest.raises(ValueError):
        await api.save_settings(hass, {const.CONF_AI_ENTITY: "sensor.unsafe"}, api.read_settings(hass)["revision"])
    assert len(hass.config_entries.updates) == 1


def test_no_config_entry_rejects_request():
    hass = types.SimpleNamespace(config_entries=types.SimpleNamespace(async_entries=lambda _: []))
    with pytest.raises(api.HomeAssistantError):
        api.read_settings(hass)


@pytest.mark.asyncio
async def test_saving_waits_for_calendar_boundary_without_stopping_generation():
    entries = Entries()
    entries.entry.runtime_data = types.SimpleNamespace(
        generator=types.SimpleNamespace(is_generating=True),
        calendar=types.SimpleNamespace(_lock=asyncio.Lock(), _closed=False),
    )
    entries.entry.options = {"unrelated": "retained"}
    hass = types.SimpleNamespace(config_entries=entries)
    revision = api.read_settings(hass)["revision"]
    async with entries.entry.runtime_data.calendar._lock:
        pending = asyncio.create_task(api.save_settings(hass, {const.CONF_LOOKAHEAD_DAYS: 5}, revision))
        await asyncio.sleep(0)
        assert not pending.done() and entries.updates == []
    result = await pending
    assert result["changed"] and result["reload"] == "none"
    assert entries.entry.options["unrelated"] == "retained"
    assert entries.entry.runtime_data.generator.is_generating


@pytest.mark.asyncio
async def test_revision_rejects_a_recreated_entry_with_identical_settings():
    hass = types.SimpleNamespace(config_entries=Entries())
    revision = api.read_settings(hass)["revision"]
    hass.config_entries.entry.entry_id = "replacement"
    with pytest.raises(api.StaleSettingsError):
        await api.save_settings(hass, {const.CONF_LOOKAHEAD_DAYS: 5}, revision)
    assert hass.config_entries.updates == []

"""Shared Settings transaction behavior; transport auth is tested in real HA."""
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

if "homeassistant" not in sys.modules:
    pkg = types.ModuleType("homeassistant")
    pkg.__path__ = []
    sys.modules["homeassistant"] = pkg
exc = sys.modules.setdefault("homeassistant.exceptions", types.ModuleType("homeassistant.exceptions"))
if not hasattr(exc, "HomeAssistantError"):
    exc.__dict__["HomeAssistantError"] = type("HomeAssistantError", (Exception,), {})
# Faithful empty entity registry for ownership checks; the shared fake suite
# provides the same mock when the full session already stubbed this module.
sys.modules.setdefault("homeassistant.helpers", types.ModuleType("homeassistant.helpers"))
er = sys.modules.setdefault("homeassistant.helpers.entity_registry", types.ModuleType("homeassistant.helpers.entity_registry"))
if not hasattr(er, "async_get"):
    er.__dict__["async_get"] = lambda hass: types.SimpleNamespace(entities=hass.registry, async_get=hass.registry.get)
if not hasattr(er, "async_entries_for_config_entry"):
    er.__dict__["async_entries_for_config_entry"] = (
        lambda registry, config_entry_id: [entry for entry in registry.entities.values()
                                            if entry.config_entry_id == config_entry_id]
    )

api = importlib.import_module("custom_components.activity_images.settings")
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
    hass = types.SimpleNamespace(config_entries=Entries(), registry={})
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
    hass = types.SimpleNamespace(config_entries=Entries(), registry={})
    original = api.read_settings(hass)["revision"]
    await api.save_settings(hass, {const.CONF_LOOKAHEAD_DAYS: 14}, original)
    with pytest.raises(api.StaleSettingsError):
        await api.save_settings(hass, {const.CONF_LOOKAHEAD_DAYS: 3}, original)
    with pytest.raises(ValueError):
        await api.save_settings(hass, {const.CONF_AI_ENTITY: "sensor.unsafe"}, api.read_settings(hass)["revision"])
    assert len(hass.config_entries.updates) == 1


def test_no_config_entry_rejects_request():
    hass = types.SimpleNamespace(config_entries=types.SimpleNamespace(async_entries=lambda _: []), registry={})
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
    hass = types.SimpleNamespace(config_entries=entries, registry={})
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
    hass = types.SimpleNamespace(config_entries=Entries(), registry={})
    revision = api.read_settings(hass)["revision"]
    hass.config_entries.entry.entry_id = "replacement"
    with pytest.raises(api.StaleSettingsError):
        await api.save_settings(hass, {const.CONF_LOOKAHEAD_DAYS: 5}, revision)
    assert hass.config_entries.updates == []

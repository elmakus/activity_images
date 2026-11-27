"""Native entity and device registry integration, with no paid calls."""
from __future__ import annotations

import asyncio
import logging
import sys
import tempfile
from datetime import timedelta
from pathlib import Path
from types import MappingProxyType, SimpleNamespace
from unittest.mock import AsyncMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from homeassistant import loader  # noqa: E402
from homeassistant.config_entries import ConfigEntries, ConfigEntry  # noqa: E402
from homeassistant.core import HomeAssistant  # noqa: E402
from homeassistant.helpers import device_registry as dr, entity_registry as er, icon  # noqa: E402
from homeassistant.helpers.entity_platform import EntityPlatform  # noqa: E402
from homeassistant.helpers import restore_state  # noqa: E402

from custom_components.activity_images import ai_task, sensor  # noqa: E402
from custom_components.activity_images.const import DOMAIN  # noqa: E402
from smoke_library_ha import prepare_http  # noqa: E402


async def check(existing: bool) -> None:
    with tempfile.TemporaryDirectory(prefix="activity-entities-native-") as directory:
        hass = HomeAssistant(directory)
        hass.config.language = "en"
        # Expose source to native custom-component translation discovery.
        Path(directory, "custom_components").mkdir()
        Path(directory, "custom_components", DOMAIN).symlink_to(ROOT / "custom_components" / DOMAIN, target_is_directory=True)
        loader.async_setup(hass)
        await prepare_http(hass)
        manager = hass.config_entries = ConfigEntries(hass, {})
        await manager.async_initialize()
        await er.async_load(hass)
        await restore_state.async_load(hass, load_empty=True)
        entry = ConfigEntry(domain=DOMAIN, title="Activity Images", version=1, minor_version=1,
                            source="user", unique_id=DOMAIN, subentries_data=None, discovery_keys=MappingProxyType({}),
                            data={}, options={})
        with patch.object(manager, "async_setup", AsyncMock(return_value=True)):
            await manager.async_add(entry)
        entry.runtime_data = SimpleNamespace(calendar=SimpleNamespace(
            images={}, rules=[], calendars=(), auto_generate=False, last_scan=None, last_error=None,
            status=lambda: {"tracked_titles": 0}, add_listener=lambda _: lambda: None))
        registry = er.async_get(hass)
        if existing:
            for domain, suffix, name in (("sensor", "library", "Activity Images Library"), ("ai_task", "ai_task", "Activity Images")):
                previous = registry.async_get_or_create(domain, DOMAIN, f"{entry.entry_id}_{suffix}",
                    config_entry=entry, original_name=name, suggested_object_id=name)
                if domain == "sensor":
                    registry.async_update_entity(previous.entity_id, new_entity_id="sensor.custom_library", name="My custom name")
        platforms = []
        for domain, module in (("sensor", sensor), ("ai_task", ai_task)):
            platform = EntityPlatform(hass=hass, logger=logging.getLogger(__name__), domain=domain,
                                     platform_name=DOMAIN, platform=module,
                                     scan_interval=timedelta(seconds=30), entity_namespace=None)
            platforms.append(platform)
            assert await platform.async_setup_entry(entry), domain
            assert len(platform.entities) == 1, platform.entities
        icons = await icon.async_get_icons(hass, "entity", {DOMAIN})
        assert icons[DOMAIN]["sensor"]["library"]["default"] == "mdi:image-multiple"
        assert icons[DOMAIN]["ai_task"]["generator"]["default"] == "mdi:image-auto-adjust"
        library_id = "sensor.custom_library" if existing else "sensor.activity_images_library"
        sensor_entry = registry.async_get(library_id)
        ai_entry = registry.async_get("ai_task.activity_images")
        assert sensor_entry is not None and ai_entry is not None, list(registry.entities)
        assert sensor_entry.unique_id == f"{entry.entry_id}_library"
        assert ai_entry.unique_id == f"{entry.entry_id}_ai_task"
        assert sensor_entry.translation_key == "library" and ai_entry.translation_key == "generator"
        assert sensor_entry.original_name == "Library" and ai_entry.original_name is None
        assert sensor_entry.device_id == ai_entry.device_id
        assert sensor_entry.disabled_by is None and ai_entry.disabled_by is None
        assert sensor_entry.entity_category is None and ai_entry.entity_category is None
        assert sensor_entry.original_device_class is None and ai_entry.original_device_class is None
        if existing:
            assert sensor_entry.name == "My custom name"
        device = dr.async_get(hass).async_get(sensor_entry.device_id)
        assert device.name == "Activity Images" and device.entry_type is dr.DeviceEntryType.SERVICE
        assert device.identifiers == {(DOMAIN, entry.entry_id)}
        assert len(dr.async_entries_for_config_entry(dr.async_get(hass), entry.entry_id)) == 1
        assert hass.states.get(library_id).attributes["friendly_name"] == ("Activity Images My custom name" if existing else "Activity Images Library"), hass.states.get(library_id).attributes
        assert hass.states.get("ai_task.activity_images").attributes["friendly_name"] == "Activity Images"
        # Real reload must reuse the same device/entity registry identifiers.
        device_id = device.id
        for platform in platforms:
            await platform.async_reset()
        for platform in platforms:
            assert await platform.async_setup_entry(entry)
        assert registry.async_get(library_id).device_id == device_id
        assert registry.async_get("ai_task.activity_images").device_id == device_id
        for platform in platforms:
            await platform.async_reset()
        await manager.async_remove(entry.entry_id)
        assert dr.async_get(hass).async_get(device_id) is None
        assert registry.async_get(library_id) is None and registry.async_get("ai_task.activity_images") is None
        await hass.async_stop()


async def run():
    await check(False)
    await check(True)
    print("NATIVE_ENTITY_REGISTRY_PASS: new IDs, existing renamed IDs/names, translations, service device and reload")


if __name__ == "__main__":
    asyncio.run(run())

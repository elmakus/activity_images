"""Native repair registry, service errors and translation resources; no paid AI."""
from __future__ import annotations

import asyncio
import json
import sys
import tempfile
from pathlib import Path
from types import MappingProxyType, SimpleNamespace
from unittest.mock import AsyncMock, patch

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from homeassistant import loader  # noqa: E402
from homeassistant.config_entries import ConfigEntries, ConfigEntry  # noqa: E402
from homeassistant.core import HomeAssistant, SupportsResponse  # noqa: E402
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError  # noqa: E402
from homeassistant.helpers import entity_registry as er, issue_registry as ir, translation  # noqa: E402

from custom_components.activity_images import async_setup, async_setup_entry, async_unload_entry  # noqa: E402
from custom_components.activity_images.const import CONF_AI_ENTITY, CONF_REFERENCE_MEDIA_ID, DOMAIN  # noqa: E402
from custom_components.activity_images.settings import read_settings, save_settings  # noqa: E402
from smoke_library_ha import prepare_http  # noqa: E402


async def run():
    with tempfile.TemporaryDirectory(prefix="activity-repairs-native-") as directory:
        hass = HomeAssistant(directory)
        Path(directory, "custom_components").mkdir()
        Path(directory, "custom_components", DOMAIN).symlink_to(ROOT / "custom_components" / DOMAIN, target_is_directory=True)
        loader.async_setup(hass)
        await prepare_http(hass)
        manager = hass.config_entries = ConfigEntries(hass, {})
        await manager.async_initialize()
        await er.async_load(hass)
        await ir.async_load(hass)
        entry = ConfigEntry(domain=DOMAIN, title="Activity Images", version=1, minor_version=1,
                            source="user", unique_id=DOMAIN, subentries_data=None, discovery_keys=MappingProxyType({}),
                            data={}, options={})
        with patch.object(manager, "async_setup", AsyncMock(return_value=True)):
            await manager.async_add(entry)

        await async_setup(hass, {})
        source = Path(directory, "provider.png")
        provider = AsyncMock(return_value={"media_source_id":"media-source://test/provider.png"})
        hass.services.async_register("ai_task", "generate_image", provider, supports_response=SupportsResponse.ONLY)

        with patch.object(manager, "async_forward_entry_setups", AsyncMock()):
            assert await async_setup_entry(hass, entry)
        runtime = entry.runtime_data
        monitor = runtime.provider_repair
        registry = ir.async_get(hass)
        def issue():
            return registry.async_get_issue(DOMAIN, monitor.issue_id)

        async def settings(changes):
            await save_settings(hass, changes, read_settings(hass)["revision"])
            await hass.async_block_till_done()

        async def state(entity_id, status, features=0):
            hass.states.async_set(entity_id, status, {"supported_features": features})
            await hass.async_block_till_done()

        def check_issue(key):
            current = issue()
            assert current and current.translation_key == key
            assert current.config_entry_id == entry.entry_id
            assert not current.is_fixable and current.severity == ir.IssueSeverity.WARNING
            return current

        assert issue() is None
        await settings({CONF_AI_ENTITY:"ai_task.first"})
        assert issue() is None  # Missing/unavailable provider is not a repair.
        await state("ai_task.first", "unavailable")
        assert issue() is None
        await state("ai_task.first", "idle", 1)
        before = check_issue("provider_image_unsupported")
        await state("ai_task.first", "busy", 1)
        assert issue() is before  # Stable issue, no noisy recreation.
        await state("ai_task.first", "idle", 4)
        assert issue() is None
        await settings({CONF_REFERENCE_MEDIA_ID:"media-source://media_source/local/style.png"})
        check_issue("provider_attachments_unsupported")
        await state("ai_task.first", "idle", 6)
        assert issue() is None
        await state("ai_task.first", "unavailable")
        assert issue() is None
        await settings({CONF_AI_ENTITY:"ai_task.second", CONF_REFERENCE_MEDIA_ID:""})
        await state("ai_task.first", "idle", 1)
        assert issue() is None  # Old listener removed.
        await state("ai_task.second", "idle", 1)
        check_issue("provider_image_unsupported")
        await settings({CONF_AI_ENTITY:""})
        assert issue() is None
        own = er.async_get(hass).async_get_or_create("ai_task", DOMAIN, f"{entry.entry_id}_ai_task", config_entry=entry)
        # Model a legacy invalid stored setting; ordinary editors reject this.
        manager.async_update_entry(entry, options={**entry.options, CONF_AI_ENTITY:own.entity_id})
        await hass.async_block_till_done()
        check_issue("provider_self_reference")
        await settings({CONF_AI_ENTITY:""})
        assert issue() is None
        provider.assert_not_called()

        resources = await translation.async_get_translations(hass, "en", "exceptions", {DOMAIN})
        issues = await translation.async_get_translations(hass, "en", "issues", {DOMAIN})
        strings = json.loads((ROOT / "custom_components" / DOMAIN / "strings.json").read_text())
        for key, definition in strings["exceptions"].items():
            assert resources[f"component.{DOMAIN}.exceptions.{key}.message"] == definition["message"]
        for key, definition in strings["issues"].items():
            assert issues[f"component.{DOMAIN}.issues.{key}.title"] == definition["title"]
            assert issues[f"component.{DOMAIN}.issues.{key}.description"] == definition["description"]

        async def expect_error(action, data, error_type, key):
            try:
                await hass.services.async_call(DOMAIN, action, data, blocking=True, return_response=True)
            except error_type as exc:
                assert exc.translation_domain == DOMAIN and exc.translation_key == key, str(exc)
                assert resources[f"component.{DOMAIN}.exceptions.{key}.message"]
                return exc
            raise AssertionError("Expected native action error")

        await expect_error("forget_image", {}, ServiceValidationError, "forget_requires_target")
        await expect_error("link_existing_image", {"title":"A title","image_url":"/local/other.png"}, ServiceValidationError, "invalid_link_url")
        await expect_error("generate_image", {"title":"No provider"}, ServiceValidationError, "provider_required")
        provider.assert_not_called()
        await settings({CONF_AI_ENTITY:"ai_task.second"})
        await state("ai_task.second", "idle", 6)
        assert issue() is None
        async def local_media(*_args):
            return SimpleNamespace(path=source, url="")
        with patch("custom_components.activity_images.generated_image.async_resolve_media", local_media):
            await hass.async_add_executor_job(lambda: Image.new("RGB", (30, 30), "red").save(source))
            await expect_error("generate_image", {"title":"Opaque test"}, HomeAssistantError, "opaque_image")
            await expect_error("generate_image", {"title":"Opaque test"}, ServiceValidationError, "explicit_retry")
            await hass.async_add_executor_job(lambda: Image.new("RGBA", (30, 30), (0,0,0,0)).save(source))
            await expect_error("generate_image", {"title":"Empty test"}, HomeAssistantError, "empty_image")
        assert provider.call_count == 2 and runtime.cache.exact("Opaque test") is None
        assert runtime.cache.exact("Empty test") is None
        await state("ai_task.second", "idle", 1)
        check_issue("provider_image_unsupported")
        with patch.object(manager, "async_unload_platforms", AsyncMock(return_value=True)):
            assert await async_unload_entry(hass, entry)
        assert issue() is None
        # Late callback and settings updates cannot recreate an issue after unload.
        monitor.update()
        monitor._changed()
        await state("ai_task.second", "busy", 1)
        assert issue() is None
        await hass.async_stop()
        print("NATIVE_REPAIRS_EXCEPTIONS_PASS: registry, rebind, cleanup, translated actions; local fake providers only")


if __name__ == "__main__":
    asyncio.run(run())

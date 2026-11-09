"""Native HA 2026.11 options, persistence and live-work regressions, fully isolated.

Only platform discovery/loading is bypassed. ConfigEntry, ConfigEntries, options
flow completion, update listeners, storage, services and image processing are real.
All provider/calendar responses and Lucida processing are local test doubles.
"""
from __future__ import annotations

import asyncio
import json
import sys
import tempfile
from pathlib import Path
from types import MappingProxyType, SimpleNamespace
from unittest.mock import AsyncMock, patch

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from homeassistant import loader  # noqa: E402
from homeassistant.components.ai_task import GenImageTask  # noqa: E402
from homeassistant.components.conversation.chat_log import ChatLog  # noqa: E402
from homeassistant.config_entries import ConfigEntries, ConfigEntry  # noqa: E402
from homeassistant.const import EVENT_HOMEASSISTANT_FINAL_WRITE, __version__  # noqa: E402
from homeassistant.core import (  # noqa: E402
    CoreState,
    HomeAssistant,
    ServiceResponse,
    SupportsResponse,
)
from smoke_library_ha import prepare_http  # noqa: E402

from custom_components.activity_images import (  # noqa: E402
    async_setup,
    async_setup_entry,
    async_unload_entry,
)
from custom_components.activity_images.ai_task import ActivityImagesAITaskEntity  # noqa: E402
from custom_components.activity_images.config_flow import ActivityImagesConfigFlow  # noqa: E402
from custom_components.activity_images.const import (  # noqa: E402
    CONF_AI_ENTITY,
    CONF_AUTO_GENERATE,
    CONF_CALENDARS,
    CONF_LOOKAHEAD_DAYS,
    CONF_LUCIDA_URL,
    CONF_PROMPT_TEMPLATE,
    CONF_REFERENCE_MEDIA_ID,
    DOMAIN,
)
from custom_components.activity_images.settings import (  # noqa: E402
    StaleSettingsError,
    read_settings,
    save_settings,
)


def form_input(values):
    """Use the same optional entity/media value shapes as HA's native form."""
    data = dict(values)
    if not data.get(CONF_AI_ENTITY):
        data.pop(CONF_AI_ENTITY, None)
    reference = data.pop(CONF_REFERENCE_MEDIA_ID, "")
    if reference:
        data[CONF_REFERENCE_MEDIA_ID] = {"media_content_id":reference, "media_content_type":"image"}
    return data


async def run():
    assert __version__ == "2026.11.0"
    with tempfile.TemporaryDirectory(prefix="activity-settings-native-") as directory:
        hass = HomeAssistant(directory)
        loader.async_setup(hass)
        await prepare_http(hass)
        manager = hass.config_entries = ConfigEntries(hass, {})
        await manager.async_initialize()
        entry = ConfigEntry(domain=DOMAIN, title="Activity Images", version=1, minor_version=1,
                            source="user", unique_id=DOMAIN, subentries_data=None, discovery_keys=MappingProxyType({}),
                            data={CONF_AI_ENTITY:"ai_task.before", CONF_AUTO_GENERATE:True},
                            options={"unrelated_option":{"preserved":True}, CONF_AUTO_GENERATE:False})
        # Only registration/setup discovery is bypassed; all mutations use the real manager.
        with patch.object(manager, "async_setup", AsyncMock(return_value=True)):
            await manager.async_add(entry)
        await async_setup(hass, {})
        with patch.object(manager, "async_forward_entry_setups", AsyncMock()):
            await async_setup_entry(hass, entry)
        runtime = entry.runtime_data
        identities = runtime, runtime.generator, runtime.queue, runtime.deletions, runtime.calendar
        notifications = []
        async def notified(_hass, current):
            notifications.append(dict(current.options))
        unsubscribe = entry.add_update_listener(notified)
        flow_factory = patch("homeassistant.config_entries._async_get_flow_handler",
                             AsyncMock(return_value=ActivityImagesConfigFlow))
        with flow_factory, patch.object(manager, "async_reload", AsyncMock()) as reload_entry:
            with patch.object(manager, "async_update_entry", wraps=manager.async_update_entry) as update:
                original = read_settings(hass)
                assert not (await save_settings(hass, {}, original["revision"]))["changed"]
                assert update.call_count == 0
                stale_form = await manager.options.async_init(entry.entry_id)
                assert stale_form.get("type") == "form"
                panel = await save_settings(hass, {CONF_PROMPT_TEMPLATE:"First {title}"}, original["revision"])
                assert panel["changed"] and panel["reload"] == "none"
                stale_values = {**original["values"], CONF_LOOKAHEAD_DAYS:14}
                stale = await manager.options.async_configure(stale_form["flow_id"], form_input(stale_values))
                assert stale.get("type") == "form"
                errors = stale.get("errors")
                assert errors and errors.get("base") == "settings_conflict"
                assert entry.options[CONF_PROMPT_TEMPLATE] == "First {title}"
                assert not entry.options[CONF_AUTO_GENERATE]  # Stale native form cannot restore paid automation.
                manager.options.async_abort(stale_form["flow_id"])
                form = await manager.options.async_init(entry.entry_id)
                values = {**read_settings(hass)["values"], CONF_LOOKAHEAD_DAYS:14}
                finished = await manager.options.async_configure(form["flow_id"], form_input(values))
                assert finished.get("type") == "create_entry"
                assert update.call_count == 2  # One panel commit, one native flow commit.
                await hass.async_block_till_done()
                assert len(notifications) == 2
                assert entry.options["unrelated_option"] == {"preserved":True}
                assert entry.data == {CONF_AI_ENTITY:"ai_task.before", CONF_AUTO_GENERATE:True}
                noop = await manager.options.async_init(entry.entry_id)
                await manager.options.async_configure(noop["flow_id"], form_input(values))
                await hass.async_block_till_done()
                assert len(notifications) == 2  # HA detects the native no-op too.

            # Native selector objects must round-trip as the existing string ID.
            reference = "media-source://media_source/local/native-reference.webp"
            media_form = await manager.options.async_init(entry.entry_id)
            selected = {**read_settings(hass)["values"], CONF_REFERENCE_MEDIA_ID:reference}
            saved_media = await manager.options.async_configure(media_form["flow_id"], form_input(selected))
            assert saved_media.get("type") == "create_entry"
            assert entry.options[CONF_REFERENCE_MEDIA_ID] == reference
            media_form = await manager.options.async_init(entry.entry_id)
            cleared_media = {**read_settings(hass)["values"], CONF_REFERENCE_MEDIA_ID:""}
            saved_media = await manager.options.async_configure(media_form["flow_id"], form_input(cleared_media))
            assert saved_media.get("type") == "create_entry"
            assert entry.options[CONF_REFERENCE_MEDIA_ID] == ""
            await hass.async_block_till_done()

            source = Path(directory) / "fake-provider.png"
            Image.new("RGBA", (24, 24), (200, 40, 60, 255)).save(source)
            started, release = asyncio.Event(), asyncio.Event()
            calls, processing = [], []
            async def fake_provider(call) -> ServiceResponse:
                calls.append(dict(call.data))
                if len(calls) == 1:
                    started.set()
                    await release.wait()
                return {"media_source_id":"media-source://test/generated.png"}
            async def local_result(*_args):
                return SimpleNamespace(path=source, url="")
            async def local_processing(_hass, data, lucida):
                processing.append(lucida)
                return data
            hass.services.async_register("ai_task", "generate_image", fake_provider,
                                         supports_response=SupportsResponse.ONLY)
            wrapper = ActivityImagesAITaskEntity(entry)
            wrapper.hass = hass
            wrapper.entity_id = "ai_task.activity_images"
            task = GenImageTask(name="home_tasks_test", attachments=None,
                                   instructions="First image\n\nThe image must have a square 1:1 aspect ratio.")
            await save_settings(hass, {CONF_REFERENCE_MEDIA_ID:"media-source://media_source/local/old.jpg",
                                       CONF_LUCIDA_URL:"http://before.invalid/remove-bg"}, read_settings(hass)["revision"])
            await hass.async_block_till_done()
            with patch("custom_components.activity_images.generated_image.async_resolve_media", local_result), \
                 patch("custom_components.activity_images.generated_image._maybe_remove_background", local_processing):
                running = asyncio.create_task(wrapper._async_generate_image(task, ChatLog(hass, "test")))
                await asyncio.wait_for(started.wait(), 5)
                manual = await runtime.queue.async_candidate("Waiting manual")
                await runtime.queue.async_enqueue(["Waiting background"])
                await save_settings(hass, {CONF_AI_ENTITY:"ai_task.after", CONF_PROMPT_TEMPLATE:"New {title}",
                                           CONF_REFERENCE_MEDIA_ID:"media-source://media_source/local/new.webp",
                                           CONF_LUCIDA_URL:"http://after.invalid/remove-bg"}, read_settings(hass)["revision"])
                assert not running.done() and runtime.generator.is_generating
                assert identities == (entry.runtime_data, runtime.generator, runtime.queue, runtime.deletions, runtime.calendar)
                release.set()
                result = await asyncio.wait_for(running, 10)
                await hass.async_block_till_done()
                assert result.revised_prompt == "First First image"
                assert len(calls) == 3
                assert calls[0]["entity_id"] == "ai_task.before"
                assert calls[0]["attachments"] == [{"media_content_id":"media-source://media_source/local/old.jpg"}]
                assert processing == ["http://before.invalid/remove-bg", "http://after.invalid/remove-bg", "http://after.invalid/remove-bg"]
                assert all(call["entity_id"] == "ai_task.after" and call["instructions"].startswith("New ")
                           and call["attachments"] == [{"media_content_id":"media-source://media_source/local/new.webp"}]
                           for call in calls[1:])
                assert runtime.queue._job(manual["id"])["state"] == "review"
                assert runtime.queue.status()["pending"] == []

            # Empty -> selected -> changed -> empty uses the same sensor-facing adapter.
            hass.state = CoreState.running
            scans = []
            scan_started, finish_scan = asyncio.Event(), asyncio.Event()
            hold_scan = False
            async def calendar(call) -> ServiceResponse:
                scans.append(call.data)
                if hold_scan:
                    scan_started.set()
                    await finish_scan.wait()
                return {name:{"events":[]} for name in call.data["entity_id"]}
            hass.services.async_register("calendar", "get_events", calendar,
                                         supports_response=SupportsResponse.ONLY)
            hass.states.async_set("calendar.first", "off")
            hass.states.async_set("calendar.second", "off")
            sensor_updates = []
            runtime.calendar.add_listener(lambda: sensor_updates.append(runtime.calendar.status()))
            await save_settings(hass, {CONF_CALENDARS:["calendar.first"]}, read_settings(hass)["revision"])
            await hass.async_block_till_done()
            assert runtime.calendar._state_unsubscribe and runtime.calendar.last_scan and sensor_updates
            initial_scans = len(scans)
            await save_settings(hass, {CONF_PROMPT_TEMPLATE:"Other {title}"}, read_settings(hass)["revision"])
            await hass.async_block_till_done()
            assert len(scans) == initial_scans  # Unrelated settings never rescan calendars.
            hold_scan = True
            scanning = asyncio.create_task(runtime.calendar.async_refresh())
            await asyncio.wait_for(scan_started.wait(), 5)
            saving = asyncio.create_task(save_settings(hass, {CONF_CALENDARS:["calendar.second"]}, read_settings(hass)["revision"]))
            await asyncio.sleep(0)
            assert not saving.done()  # The old scan completes before the application boundary.
            hold_scan = False
            finish_scan.set()
            await scanning
            await saving
            await hass.async_block_till_done()
            assert runtime.calendar.calendars == ("calendar.second",)
            after_change = len(scans)
            hass.states.async_set("calendar.first", "on")
            await hass.async_block_till_done()
            assert len(scans) == after_change  # Removed calendar subscription was detached.
            await save_settings(hass, {CONF_CALENDARS:[]}, read_settings(hass)["revision"])
            await hass.async_block_till_done()
            assert not runtime.calendar.calendar_titles and not runtime.calendar.images
            assert runtime.calendar._state_unsubscribe is None
            assert await runtime.calendar._store.async_load() == {}
            assert len(calls) == 3  # No implicit AI calls from saving, previews or scans.
            reload_entry.assert_not_called()

            # Native clearing must override installation data, not revive the old provider.
            before_clear = read_settings(hass)
            clear_form = await manager.options.async_init(entry.entry_id)
            cleared = dict(before_clear["values"])
            cleared.pop(CONF_AI_ENTITY)
            await manager.options.async_configure(clear_form["flow_id"], form_input(cleared))
            await hass.async_block_till_done()
            assert entry.options[CONF_AI_ENTITY] == "" and not wrapper.available
            try:
                await save_settings(hass, {CONF_LOOKAHEAD_DAYS:20}, before_clear["revision"])
            except StaleSettingsError:
                pass
            else:
                raise AssertionError("Stale panel editor overwrote a native Configure save")

            # Both writers reject selecting a synthetic original reserved for deletion.
            reserved = Path(directory, "www/activity_images/reserved.png")
            await hass.async_add_executor_job(lambda: Image.new("RGB", (12, 12), "green").save(reserved))
            with patch.object(runtime.deletions, "_kick", lambda: None):
                plan = await runtime.deletions.async_plan([runtime.library.image_id(reserved.name)])
                await runtime.deletions.async_delete(plan["token"], False, True)
            reference = "media-source://media_source/local/reserved.png"
            before_reference = dict(entry.options)
            try:
                await save_settings(hass, {CONF_REFERENCE_MEDIA_ID:reference}, read_settings(hass)["revision"])
            except ValueError as exc:
                assert "reserved" in str(exc)
            else:
                raise AssertionError("Panel selected a reserved reference")
            native = await manager.options.async_init(entry.entry_id)
            reserved_values = {**read_settings(hass)["values"], CONF_REFERENCE_MEDIA_ID:reference}
            reserved_values.pop(CONF_AI_ENTITY)
            rejected = await manager.options.async_configure(native["flow_id"], form_input(reserved_values))
            errors = rejected.get("errors")
            assert errors and errors.get("base") == "invalid_settings"
            manager.options.async_abort(native["flow_id"])
            assert dict(entry.options) == before_reference and reserved.is_file()
            reload_entry.assert_not_called()

            # Flush HA's native delayed persistence and reload it with a new manager.
            hass.bus.async_fire(EVENT_HOMEASSISTANT_FINAL_WRITE)
            await hass.async_block_till_done()
            stored = await hass.async_add_executor_job(
                lambda: json.loads(Path(directory, ".storage/core.config_entries").read_text()))
            saved_entry = next(item for item in stored["data"]["entries"] if item["entry_id"] == entry.entry_id)
            assert saved_entry["options"] == dict(entry.options)
            assert saved_entry["data"] == dict(entry.data)
            with tempfile.TemporaryDirectory(prefix="activity-settings-reopen-") as unused:
                reopened = HomeAssistant(unused)
                reopened.config.config_dir = directory
                loader.async_setup(reopened)
                reopened.config_entries = ConfigEntries(reopened, {})
                await reopened.config_entries.async_initialize()
                restored = reopened.config_entries.async_get_known_entry(entry.entry_id)
                assert dict(restored.options) == dict(entry.options)
                assert read_settings(reopened)["values"][CONF_PROMPT_TEMPLATE] == "Other {title}"
                await reopened.async_stop()
        unsubscribe()
        with patch.object(manager, "async_unload_platforms", AsyncMock(return_value=True)):
            await async_unload_entry(hass, entry)
        await hass.async_stop()
        print("NATIVE_SETTINGS_PASS: real options manager, conflicts, no-op, persistence/reopen, live paid-work snapshots, calendar lifecycle; fake providers only")


if __name__ == "__main__":
    asyncio.run(run())

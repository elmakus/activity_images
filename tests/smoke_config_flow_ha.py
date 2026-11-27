"""Native configuration flow: validate, confirm and prevent duplicate setup."""
import asyncio
import sys
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from homeassistant import loader  # noqa: E402
from homeassistant.config_entries import ConfigEntries  # noqa: E402
from homeassistant.core import HomeAssistant  # noqa: E402
from homeassistant.helpers import entity_registry as er  # noqa: E402
from smoke_library_ha import prepare_http  # noqa: E402
from custom_components.activity_images.config_flow import ActivityImagesConfigFlow, ActivityImagesOptionsFlow  # noqa: E402
from custom_components.activity_images.const import (  # noqa: E402
    CONF_AI_ENTITY, CONF_AUTO_CALENDARS, CONF_CALENDARS, CONF_LOOKAHEAD_DAYS, CONF_PROMPT_TEMPLATE, DOMAIN,
)


async def run():
    with tempfile.TemporaryDirectory(prefix="activity-config-flow-") as directory:
        hass = HomeAssistant(directory)
        loader.async_setup(hass)
        await prepare_http(hass)
        manager = hass.config_entries = ConfigEntries(hass, {})
        await manager.async_initialize()
        await er.async_load(hass)
        provider = AsyncMock()
        hass.services.async_register("ai_task", "generate_image", provider)
        with patch("homeassistant.config_entries._async_get_flow_handler", AsyncMock(return_value=ActivityImagesConfigFlow)), patch.object(manager, "async_setup", AsyncMock(return_value=True)):
            flow = await manager.flow.async_init(DOMAIN, context={"source": "user"})
            assert flow["type"] == "form"
            flow_id = flow["flow_id"]
            from custom_components.activity_images.prompts import DEFAULT_PROMPT_TEMPLATE
            base = {CONF_LOOKAHEAD_DAYS: 30, CONF_PROMPT_TEMPLATE: DEFAULT_PROMPT_TEMPLATE}
            invalid = await manager.flow.async_configure(flow_id, {**base, CONF_PROMPT_TEMPLATE: "Missing placeholder"})
            assert invalid["errors"]["base"] == "invalid_settings"
            own = er.async_get(hass).async_get_or_create("ai_task", DOMAIN, "self")
            self_ref = await manager.flow.async_configure(flow_id, {**base, CONF_AI_ENTITY: own.entity_id})
            assert self_ref["errors"]["base"] == "invalid_settings"
            unavailable = await manager.flow.async_configure(flow_id, {**base, CONF_AI_ENTITY: "ai_task.fake"})
            assert unavailable["errors"]["base"] == "provider_unavailable", unavailable.get("errors")
            hass.states.async_set("ai_task.fake", "idle", {"supported_features": 6})
            paid = await manager.flow.async_configure(flow_id, {**base, CONF_AI_ENTITY: "ai_task.fake",
                CONF_CALENDARS: ["calendar.demo"], CONF_AUTO_CALENDARS: ["calendar.demo"]})
            assert paid["step_id"] == "confirm_auto"
            unconfirmed = await manager.flow.async_configure(flow_id, {"confirm_paid": False})
            assert unconfirmed["step_id"] == "confirm_auto"
            assert not manager.async_entries(DOMAIN)
            confirmed = await manager.flow.async_configure(flow_id, {"confirm_paid": True})
            assert confirmed["type"] == "create_entry"
            assert len(manager.async_entries(DOMAIN)) == 1
            duplicate = await manager.flow.async_init(DOMAIN, context={"source": "user"})
            assert duplicate["type"] == "abort" and duplicate["reason"] == "already_configured"
            entry = manager.async_entries(DOMAIN)[0]
            from custom_components.activity_images.settings_schema import effective_settings
            from custom_components.activity_images.settings import revision
            def form_values():
                values = effective_settings(entry)
                values.pop("generate_missing_calendar_images", None)
                values.pop("reference_media_content_id", None)
                return values
            before = form_values()
            change = {**before, CONF_AI_ENTITY: "ai_task.other"}
            option = await manager.options.async_init(entry.entry_id)
            step = await manager.options.async_configure(option["flow_id"], change)
            assert step["step_id"] == "confirm_auto", step
            manager.async_update_entry(entry, options={CONF_LOOKAHEAD_DAYS: 14})
            stale = await manager.options.async_configure(option["flow_id"], {"confirm_paid": True})
            assert stale["type"] == "abort" and stale["reason"] == "settings_conflict", stale
            option = await manager.options.async_init(entry.entry_id)
            change = {**form_values(), CONF_AI_ENTITY: "ai_task.other"}
            step = await manager.options.async_configure(option["flow_id"], change)
            assert step["step_id"] == "confirm_auto"
            with patch("custom_components.activity_images.config_flow.options_for_update", side_effect=ValueError("Configuration changed")):
                invalid = await manager.options.async_configure(option["flow_id"], {"confirm_paid": True})
            assert invalid["type"] == "abort" and invalid["reason"] == "invalid_settings"
            assert entry.options[CONF_LOOKAHEAD_DAYS] == 14
            out_of_order = ActivityImagesOptionsFlow()
            out_of_order.hass = hass
            assert (await out_of_order.async_step_confirm_auto({"confirm_paid": True}))["reason"] == "invalid_settings"
            # Real reconfigure flow shares revision checks and paid confirmation.
            out_of_order = ActivityImagesConfigFlow()
            out_of_order.hass = hass
            out_of_order.handler = DOMAIN
            out_of_order.context = {"source":"reconfigure", "entry_id":entry.entry_id}
            assert (await out_of_order.async_step_confirm_reconfigure({"confirm_paid":True}))["reason"] == "invalid_settings"
            async def reconfigure():
                return await manager.flow.async_init(DOMAIN, context={"source":"reconfigure", "entry_id":entry.entry_id})
            hass.states.async_set("ai_task.other", "idle", {"supported_features":6})
            original_data = dict(entry.data)
            original_id = entry.entry_id
            with patch.object(manager, "async_schedule_reload") as reload:
                form = await reconfigure()
                assert form["step_id"] == "reconfigure"
                flow_id = form["flow_id"]
                rejected = await manager.flow.async_configure(flow_id, {**form_values(), CONF_AI_ENTITY:"ai_task.missing"})
                assert rejected["errors"]["base"] == "provider_unavailable"
                rejected = await manager.flow.async_configure(flow_id, {**form_values(), CONF_PROMPT_TEMPLATE:"Bad prompt"})
                assert rejected["errors"]["base"] == "invalid_settings"
                step = await manager.flow.async_configure(flow_id, {**form_values(), CONF_AI_ENTITY:"ai_task.other"})
                assert step["step_id"] == "confirm_reconfigure"
                again = await manager.flow.async_configure(flow_id, {"confirm_paid":False})
                assert again["step_id"] == "confirm_reconfigure"
                assert effective_settings(entry)[CONF_AI_ENTITY] == "ai_task.fake"
                hass.states.async_set("ai_task.other", "unavailable")
                aborted = await manager.flow.async_configure(flow_id, {"confirm_paid":True})
                assert aborted["reason"] == "provider_unavailable"
                hass.states.async_set("ai_task.other", "idle", {"supported_features":6})
                form = await reconfigure()
                flow_id = form["flow_id"]
                step = await manager.flow.async_configure(flow_id, {**form_values(), CONF_AI_ENTITY:"ai_task.other"})
                assert step["step_id"] == "confirm_reconfigure"
                manager.async_update_entry(entry, options={CONF_LOOKAHEAD_DAYS:15})
                aborted = await manager.flow.async_configure(flow_id, {"confirm_paid":True})
                assert aborted["reason"] == "settings_conflict"
                form = await reconfigure()
                flow_id = form["flow_id"]
                step = await manager.flow.async_configure(flow_id, {**form_values(), CONF_AI_ENTITY:"ai_task.other"})
                assert step["step_id"] == "confirm_reconfigure"
                result = await manager.flow.async_configure(flow_id, {"confirm_paid":True})
                assert result["reason"] == "reconfigure_successful"
                assert effective_settings(entry)[CONF_AI_ENTITY] == "ai_task.other"
                assert effective_settings(entry)[CONF_AUTO_CALENDARS] == ["calendar.demo"]
                form = await reconfigure()
                flow_id = form["flow_id"]
                # No new paid authorization: update lookahead in place.
                result = await manager.flow.async_configure(flow_id, {**form_values(), CONF_LOOKAHEAD_DAYS:25})
                assert result["reason"] == "reconfigure_successful"
                assert effective_settings(entry)[CONF_LOOKAHEAD_DAYS] == 25
                form = await reconfigure()
                values = form_values()
                values.pop(CONF_AI_ENTITY)
                values[CONF_AUTO_CALENDARS] = []
                result = await manager.flow.async_configure(form["flow_id"], values)
                assert result["reason"] == "reconfigure_successful"
                assert effective_settings(entry)[CONF_AI_ENTITY] == ""
                assert not effective_settings(entry)[CONF_AUTO_CALENDARS]
                assert dict(entry.data) == original_data and entry.entry_id == original_id
                assert len(manager.async_entries(DOMAIN)) == 1
                reload.assert_not_called()
            assert revision(entry)
            assert provider.call_count == 0
        await hass.async_stop()
    with tempfile.TemporaryDirectory(prefix="activity-library-only-flow-") as directory:
        hass = HomeAssistant(directory)
        loader.async_setup(hass)
        await prepare_http(hass)
        manager = hass.config_entries = ConfigEntries(hass, {})
        await manager.async_initialize()
        await er.async_load(hass)
        with patch("homeassistant.config_entries._async_get_flow_handler", AsyncMock(return_value=ActivityImagesConfigFlow)), patch.object(manager, "async_setup", AsyncMock(return_value=True)):
            flow = await manager.flow.async_init(DOMAIN, context={"source": "user"})
            created = await manager.flow.async_configure(flow["flow_id"], {CONF_LOOKAHEAD_DAYS: 30})
            assert created["type"] == "create_entry" and created["data"][CONF_AI_ENTITY] == ""
        await hass.async_stop()
    print("CONFIG_FLOW_NATIVE_PASS: unavailable, paid consent, singleton; no provider calls")


if __name__ == "__main__":
    asyncio.run(run())

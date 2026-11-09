"""Real Home Assistant Core 2026.10.4 action-registry smoke test.

No connected Home Assistant modified; no real AI provider calls.
"""
from __future__ import annotations

import asyncio
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
from unittest.mock import patch

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from homeassistant.core import HomeAssistant, SupportsResponse  # noqa: E402
from homeassistant.exceptions import HomeAssistantError  # noqa: E402
from custom_components.activity_images import (  # noqa: E402
    async_setup, async_setup_entry, async_unload_entry,
)
from custom_components.activity_images.sensor import ActivityImagesLibrarySensor  # noqa: E402


class TestEntry:
    entry_id = "isolated_smoke_entry"
    data = {"ai_task_entity_id": "ai_task.mock"}
    options = {}
    runtime_data = None

    def add_update_listener(self, callback):
        return lambda: None

    def async_on_unload(self, callback):
        self.callback = callback


async def run():
    with tempfile.TemporaryDirectory(prefix="ha-activity-images-smoke-") as tempdir:
        hass = HomeAssistant(tempdir)
        from smoke_library_ha import prepare_http
        await prepare_http(hass)

        assert await async_setup(hass, {})
        assert hass.services.has_service("activity_images", "generate_image")
        assert hass.services.has_service("activity_images", "queue_status")
        try:
            await hass.services.async_call(
                "activity_images", "queue_status", {}, blocking=True, return_response=True,
            )
        except HomeAssistantError as exc:
            assert "not configured" in str(exc)
        else:
            raise AssertionError("Service should reject missing config")

        source = Path(tempdir) / "fake-provider-result.png"
        picture = Image.new("RGBA", (128, 128), (0, 0, 0, 0))
        picture.putpixel((40, 40), (200, 30, 60, 255))
        picture.save(source)
        provider_calls = []

        async def mock_provider(call):
            provider_calls.append(call.data)
            return {"media_source_id": "media-source://ai_task/image/test.png"}

        hass.services.async_register(
            "ai_task", "generate_image", mock_provider,
            supports_response=SupportsResponse.ONLY,
        )

        async def mock_resolve(hass, media, user):
            assert media == "media-source://ai_task/image/test.png"
            return SimpleNamespace(path=source, url="/api/ai_task/image/test.png")

        async def mock_get_events(call):
            assert call.data["entity_id"] == ["calendar.demo"]
            return {"calendar.demo": {"events": [
                {"summary": "Green Waste Collection", "start": "2026-11-03T09:00:00"}
            ]}}

        hass.services.async_register(
            "calendar", "get_events", mock_get_events,
            supports_response=SupportsResponse.ONLY,
        )
        entry = TestEntry()
        entry.data = {
            "ai_task_entity_id": "ai_task.mock",
            "calendar_entity_ids": ["calendar.demo"],
            "calendar_lookahead_days": 30,
            "generate_missing_calendar_images": False,
        }
        async def fake_forward(*_args, **_kwargs):
            return None

        async def fake_unload(*_args, **_kwargs):
            return True

        hass.config_entries = SimpleNamespace(
            async_forward_entry_setups=fake_forward,
            async_unload_platforms=fake_unload,
        )
        with patch(
            "custom_components.activity_images.generated_image.async_resolve_media",
            side_effect=mock_resolve,
        ):
            assert await async_setup_entry(hass, entry)
            first = await hass.services.async_call(
                "activity_images", "generate_image",
                {"title": "Green Waste Collection"},
                blocking=True, return_response=True,
            )
            assert first["reused"] is False
            assert entry.runtime_data.calendar.calendars == ("calendar.demo",)
            await asyncio.sleep(0.02)
            sensor = ActivityImagesLibrarySensor(entry.entry_id, entry.runtime_data.calendar)
            assert sensor.native_value == 1
            assert sensor.extra_state_attributes["images"]["Green Waste Collection"] == first["image_url"]
            assert sensor.extra_state_attributes["calendar_rules"][0]["label_type"] == "image"
            second = await hass.services.async_call(
                "activity_images", "generate_image",
                {"title": "Green Waste Collection"},
                blocking=True, return_response=True,
            )
            assert second["reused"] is True
            assert len(provider_calls) == 1
            found = await hass.services.async_call(
                "activity_images", "resolve_images",
                {"titles": ["Green Waste Collection"], "enqueue_missing": False},
                blocking=True, return_response=True,
            )
            assert found["images"]["Green Waste Collection"] == first["image_url"]
            assert found["image_metadata"]["Green Waste Collection"]["height"] == 512
            assert await async_unload_entry(hass, entry)
            try:
                await hass.services.async_call(
                    "activity_images", "queue_status", {},
                    blocking=True, return_response=True,
                )
            except HomeAssistantError as exc:
                assert "not configured" in str(exc)
            else:
                raise AssertionError("Service should reject calls after unload")

        print("ACTUAL_HA_2026_10_4_CALENDAR_SMOKE_PASS", len(provider_calls), first["image_url"])

asyncio.run(run())

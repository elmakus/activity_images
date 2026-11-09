"""Smoke-test the real Home Assistant AI Task entity contract, no live AI calls.

Usage: in an isolated venv with Home Assistant Core installed:
    python tests/smoke_ai_wrapper_ha.py
"""
from __future__ import annotations

import asyncio
from io import BytesIO
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
from unittest.mock import patch

from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from homeassistant.components.ai_task import GenImageTask, AITaskEntityFeature  # noqa: E402
from homeassistant.core import HomeAssistant, SupportsResponse  # noqa: E402
from custom_components.activity_images import (  # noqa: E402
    async_setup, async_setup_entry, async_unload_entry,
)
from custom_components.activity_images.ai_task import (  # noqa: E402
    ActivityImagesAITaskEntity,
    extract_home_tasks_title,
)


class TestEntry:
    entry_id = "isolated_ai_wrapper_smoke"
    data = {"ai_task_entity_id": "ai_task.fake_real_provider"}
    options = {}
    runtime_data = None

    def add_update_listener(self, callback):
        return lambda: None

    def async_on_unload(self, callback):
        self.callback = callback


async def run():
    assert extract_home_tasks_title(
        "home_tasks_f1e2d3",
        "Green Waste Collection\n\nThe image must have a square 1:1 aspect ratio.",
    ) == "Green Waste Collection"

    with tempfile.TemporaryDirectory(prefix="ha-wrapper-smoke-") as directory:
        hass = HomeAssistant(directory)

        from smoke_library_ha import prepare_http
        await prepare_http(hass)

        async def no_forward(*_args):
            return None

        async def no_unload(*_args):
            return True

        hass.config_entries = SimpleNamespace(
            async_forward_entry_setups=no_forward,
            async_unload_platforms=no_unload,
        )

        assert await async_setup(hass, {})
        raw_source = Path(directory) / "raw-image.png"
        art = Image.new("RGBA", (150, 200), (0, 0, 0, 0))
        ImageDraw.Draw(art).ellipse((25, 25, 125, 160), fill=(200, 60, 40, 255))
        art.save(raw_source)
        seen = []

        async def fake_ai_provider(call):
            seen.append(call.data)
            return {"media_source_id": "media-source://ai_task/image/smoke.png"}

        hass.services.async_register(
            "ai_task", "generate_image",
            fake_ai_provider,
            supports_response=SupportsResponse.ONLY,
        )

        async def resolve_media(_hass, media, _user):
            assert media == "media-source://ai_task/image/smoke.png"
            return SimpleNamespace(path=raw_source, url="/api/ai_task/image/smoke.png")

        entry = TestEntry()
        with patch(
            "custom_components.activity_images.generated_image.async_resolve_media",
            side_effect=resolve_media,
        ):
            assert await async_setup_entry(hass, entry)
            wrapper = ActivityImagesAITaskEntity(entry)
            wrapper.hass = hass
            wrapper.entity_id = "ai_task.activity_images"
            assert wrapper.available
            assert AITaskEntityFeature.GENERATE_IMAGE in wrapper.supported_features
            task = GenImageTask(
                name="home_tasks_f1e2d3",
                instructions=(
                    "Green Waste Collection\n\n"
                    "The image must have a square 1:1 aspect ratio."
                ),
            )
            session = SimpleNamespace(conversation_id="isolated-session")
            first = await wrapper._async_generate_image(task, session)
            assert first.conversation_id == "isolated-session"
            assert first.mime_type == "image/png"
            assert first.image_data[:8] == bytes.fromhex("89504E470D0A1A0A")
            assert first.height == 512
            assert len(seen) == 1
            with Image.open(BytesIO(first.image_data)) as image:
                assert image.size == (first.width, first.height)
            assert seen[0]["entity_id"] == "ai_task.fake_real_provider"
            assert "Green Waste Collection" in first.revised_prompt
            assert "square 1:1 aspect ratio" not in first.revised_prompt

            # Second invocation must reuse the independent cache, not call AI.
            second = await wrapper._async_generate_image(task, session)
            assert second.image_data == first.image_data
            assert len(seen) == 1
            assert await async_unload_entry(hass, entry)
            print("ACTUAL_HA_CORE_AI_TASK_WRAPPER_PASS", len(seen), first.width, first.height)

asyncio.run(run())

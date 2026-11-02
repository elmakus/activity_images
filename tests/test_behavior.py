"""Behavioral tests for the extracted engine without a live Home Assistant.

Fake HA storage, media and AI Task service exercise real generator, cache, queue,
Pillow pipeline and persistence. No real provider calls or existing HA data.
"""
from __future__ import annotations

import asyncio
import importlib
import sys
import types
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image, ImageDraw


ROOT = Path(__file__).resolve().parents[1] / "custom_components" / "activity_images"


def _package(name, path=None):
    package = types.ModuleType(name)
    package.__path__ = [str(path)] if path else []
    sys.modules[name] = package
    return package


# Import the real extracted code, avoiding Home Assistant's runtime initializer.
_package("custom_components")
_package("custom_components.activity_images", ROOT)
_package("homeassistant")
_package("homeassistant.core")
_package("homeassistant.helpers")
_package("homeassistant.helpers.storage")
_package("homeassistant.helpers.aiohttp_client")
_package("homeassistant.components")
_package("homeassistant.components.media_source")


class MemoryStore:
    def __init__(self, hass, version, key):
        self.hass = hass
        self.key = key

    async def async_load(self):
        import copy
        return copy.deepcopy(self.hass.saved.get(self.key))

    async def async_save(self, value):
        import copy
        self.hass.saved[self.key] = copy.deepcopy(value)


async def fake_resolve_media(hass, source, _user):
    return SimpleNamespace(path=hass.medias[source], url="")


class TestHA:
    def __init__(self, temp):
        self.base = Path(temp)
        self.data = {}
        self.saved = {}
        self.medias = {}
        self.config = SimpleNamespace(path=self.path)
        self.services = FakeServices(self)

    def path(self, *parts):
        return str(self.base.joinpath(*parts))

    async def async_add_executor_job(self, function, *args):
        return await asyncio.to_thread(function, *args)

    def async_create_task(self, coroutine):
        return asyncio.create_task(coroutine)


class FakeServices:
    def __init__(self, hass):
        self.hass = hass
        self.calls = []
        self.fail = False
        self.response = None

    async def async_call(self, domain, service, data, **kwargs):
        self.calls.append((domain, service, data, kwargs))
        if self.fail:
            raise RuntimeError("Provider unavailable")
        if self.response is not None:
            return self.response
        path = self.hass.base / "source.png"
        picture = Image.new("RGBA", (128, 128), (0, 0, 0, 0))
        ImageDraw.Draw(picture).ellipse((25, 25, 100, 100), fill=(240, 50, 20, 255))
        picture.save(path)
        self.hass.medias["media-source://generated/test"] = path
        return {"media_source_id": "media-source://generated/test"}


sys.modules["homeassistant.core"].HomeAssistant = TestHA
sys.modules["homeassistant.helpers.storage"].Store = MemoryStore
sys.modules["homeassistant.components.media_source"].async_resolve_media = fake_resolve_media
sys.modules["homeassistant.helpers.aiohttp_client"].async_get_clientsession = lambda hass: None

cache_module = importlib.import_module("custom_components.activity_images.image_cache")
engine_module = importlib.import_module("custom_components.activity_images.engine")
queue_module = importlib.import_module("custom_components.activity_images.image_queue")
generated_module = importlib.import_module("custom_components.activity_images.generated_image")
const_module = importlib.import_module("custom_components.activity_images.const")
prompts_module = importlib.import_module("custom_components.activity_images.prompts")


@pytest.fixture
def ha(tmp_path):
    return TestHA(tmp_path)


@pytest.fixture
async def configured(ha):
    cache = cache_module.ActivityImageCache(ha)
    await cache.async_setup()
    engine = engine_module.ActivityImageGenerator(ha, cache, {
        const_module.CONF_AI_ENTITY: "ai_task.mock",
        const_module.CONF_MIN_INTERVAL: 0,
        const_module.CONF_MAX_INTERVAL: 0,
    })
    return cache, engine


def test_normalizer_polish_german_accents():
    assert cache_module.normalize_title(" Żółć, odkurzanie! ") == "zołc odkurzanie"
    assert cache_module.normalize_title("GRÜN / grün") == "grun grun"


@pytest.mark.asyncio
async def test_cache_dedup_and_disk_persistence(ha):
    path = ha.base / "www/activity_images/existing.png"
    path.parent.mkdir(parents=True)
    Image.new("RGBA", (10, 10), "red").save(path)
    cache = cache_module.ActivityImageCache(ha)
    await cache.async_setup()
    await cache.async_remember("Odkurzanie!", "/local/activity_images/existing.png")
    assert await cache.async_find("odkurzanie") == "/local/activity_images/existing.png"
    restored = cache_module.ActivityImageCache(ha)
    await restored.async_setup()
    assert await restored.async_find("ODKURZANIE") == "/local/activity_images/existing.png"


@pytest.mark.asyncio
async def test_cache_rejects_missing_file(ha):
    cache = cache_module.ActivityImageCache(ha)
    await cache.async_setup()
    await cache.async_remember("Sprzątanie", "/local/activity_images/missing.png")
    assert await cache.async_find("Sprzątanie") is None
    assert "sprzatanie" not in cache._entries


@pytest.mark.asyncio
async def test_similar_matching_not_different_task(ha):
    path = ha.base / "www/activity_images/icon.png"
    path.parent.mkdir(parents=True)
    Image.new("RGBA", (5, 5)).save(path)
    cache = cache_module.ActivityImageCache(ha)
    await cache.async_setup()
    await cache.async_remember("Pokój dziecięcy", "/local/activity_images/icon.png")
    assert await cache.async_find("Pokój dziecięcy!") is not None
    assert await cache.async_find("Pranie samochodu") is None


@pytest.mark.asyncio
async def test_cache_lru_preserves_files(ha, monkeypatch):
    monkeypatch.setattr(cache_module, "MAX_CACHED_IMAGES", 2)
    cache = cache_module.ActivityImageCache(ha)
    await cache.async_setup()
    names = ("a", "b", "c")
    for name in names:
        path = ha.base / f"www/activity_images/{name}.png"
        path.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGBA", (5, 5)).save(path)
        await cache.async_remember(name, f"/local/activity_images/{name}.png")
        await asyncio.sleep(0.005)
    assert len(cache._entries) == 2
    assert all((ha.base / f"www/activity_images/{n}.png").exists() for n in names)


@pytest.mark.asyncio
async def test_generate_real_png_thumbnail_and_qa(ha, configured):
    cache, engine = configured
    output = await engine.async_generate("Odkurzanie")
    assert output["reused"] is False
    assert output["image_url"].startswith("/local/activity_images/")
    filename = output["image_url"].split("/")[-1].split("?")[0]
    folder = ha.base / "www/activity_images"
    with Image.open(folder / filename) as png:
        assert png.format == "PNG"
        assert png.mode == "RGBA"
        assert png.height == 512
    assert (folder / filename.replace(".png", "_thumb.webp")).is_file()
    assert (folder / filename.replace(".png", "_qa.webp")).is_file()
    assert len(ha.services.calls) == 1
    again = await engine.async_generate("odkurzanie")
    assert again["reused"] is True
    assert len(ha.services.calls) == 1


@pytest.mark.asyncio
async def test_force_regeneration(ha, configured):
    cache, engine = configured
    await engine.async_generate("Odkurzanie")
    result = await engine.async_generate("Odkurzanie", force=True)
    assert result["reused"] is False
    assert len(ha.services.calls) == 2


@pytest.mark.asyncio
async def test_reference_and_room_prompt(ha, configured):
    cache, engine = configured
    engine.settings[const_module.CONF_REFERENCE_MEDIA_ID] = "media-source://media_source/local/reference.png"
    result = await engine.async_generate("Pokój Hani")
    payload = ha.services.calls[-1][2]
    assert "isometric" in payload["instructions"].lower()
    assert payload["attachments"][0]["media_content_id"].startswith("media-source://")
    assert result["image_url"].startswith("/local/")


@pytest.mark.asyncio
async def test_provider_missing_image_error(ha, configured):
    cache, engine = configured
    ha.services.response = {}
    with pytest.raises(ValueError, match="image URL"):
        await engine.async_generate("Niewykonalne")


@pytest.mark.asyncio
async def test_provider_failure_does_not_write_cache(ha, configured):
    cache, engine = configured
    ha.services.fail = True
    with pytest.raises(RuntimeError, match="unavailable"):
        await engine.async_generate("Awaria")
    assert await cache.async_find("Awaria") is None


@pytest.mark.asyncio
async def test_concurrent_calls_use_one_generation(ha, configured):
    cache, engine = configured
    a, b = await asyncio.gather(
        engine.async_generate("Odkurzanie"), engine.async_generate("Odkurzanie")
    )
    assert {a["reused"], b["reused"]} == {True, False}
    assert len(ha.services.calls) == 1


@pytest.mark.asyncio
async def test_queue_dedupe_persistence_and_cancellation(ha, configured, monkeypatch):
    _, engine = configured
    queue = queue_module.ActivityImageQueue(ha, engine)
    monkeypatch.setattr(queue, "_kick", lambda: None)
    await queue.async_setup()
    accepted = await queue.async_enqueue(["Odkurzanie", "odkurzanie!", "Mycie"])
    assert accepted == ["Odkurzanie", "Mycie"]
    assert queue.status()["pending"] == accepted
    reloaded = queue_module.ActivityImageQueue(ha, engine)
    monkeypatch.setattr(reloaded, "_kick", lambda: None)
    await reloaded.async_setup()
    assert reloaded.status()["pending"] == accepted
    assert await reloaded.async_cancel(["Odkurzanie"]) == 1
    assert reloaded.status()["pending"] == ["Mycie"]


@pytest.mark.asyncio
async def test_queue_processes_jobs(ha, configured):
    _, engine = configured
    queue = queue_module.ActivityImageQueue(ha, engine)
    await queue.async_setup()
    await queue.async_enqueue(["Pranie", "Mycie auta"])
    await asyncio.wait_for(queue._worker, 5)
    assert not queue.status()["pending"]
    assert not queue.status()["failed"]
    assert len(ha.services.calls) == 2
    await queue.async_shutdown()


@pytest.mark.asyncio
async def test_queue_provider_error_persists(ha, configured):
    _, engine = configured
    ha.services.fail = True
    queue = queue_module.ActivityImageQueue(ha, engine)
    await queue.async_setup()
    await queue.async_enqueue(["Niepowodzenie"])
    await asyncio.wait_for(queue._worker, 5)
    assert len(queue.status()["failed"]) == 1
    restored = queue_module.ActivityImageQueue(ha, engine)
    await restored.async_setup()
    assert not restored.status()["pending"]
    assert len(restored.status()["failed"]) == 1
    assert await restored.async_clear_errors() == 1
    await queue.async_shutdown()
    await restored.async_shutdown()


@pytest.mark.asyncio
async def test_queue_shutdown_keeps_pending_job(ha, configured):
    _, engine = configured
    queue = queue_module.ActivityImageQueue(ha, engine)
    queue._next_run = 9999999999
    await queue.async_setup()
    queue._next_run = 9999999999
    await queue.async_enqueue(["Poczekaj"])
    await asyncio.sleep(0.025)
    assert queue.status()["current"] == "Poczekaj"
    await queue.async_shutdown()
    restored = queue_module.ActivityImageQueue(ha, engine)
    # Do not run a restored job while inspecting stored state.
    restored._kick = lambda: None
    await restored.async_setup()
    assert restored.status()["pending"] == ["Poczekaj"]


@pytest.mark.asyncio
async def test_invalid_filename_rejected(ha):
    with pytest.raises(ValueError, match="Unsafe"):
        await generated_module.async_save_generated_image(
            ha, "media-source://generated/test", "../escape"
        )


def test_generated_image_alpha_detection():
    from io import BytesIO
    image = Image.new("RGBA", (8, 8), (20, 30, 40, 255))
    out = BytesIO()
    image.save(out, format="PNG")
    assert generated_module._already_transparent(out.getvalue()) is False
    image.putpixel((0, 0), (0, 0, 0, 0))
    out = BytesIO()
    image.save(out, format="PNG")
    assert generated_module._already_transparent(out.getvalue()) is True

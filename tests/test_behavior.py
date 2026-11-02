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
import pytest_asyncio
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


class MockHA:
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


sys.modules["homeassistant.core"].HomeAssistant = MockHA
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
    return MockHA(tmp_path)


@pytest_asyncio.fixture
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
    # The private fork keeps failed activity jobs for later cooldown retry.
    # A failed entry must survive restart instead of being discarded.
    await queue.async_shutdown()
    restored = queue_module.ActivityImageQueue(ha, engine)
    restored._kick = lambda: None
    await restored.async_setup()
    assert restored.status()["pending"] == ["Niepowodzenie"]
    assert len(restored.status()["failed"]) == 1
    assert await restored.async_clear_errors() == 1
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


@pytest.mark.asyncio
async def test_batch_lookup_updates_cache_once(ha):
    path = ha.base / "www/activity_images/same.png"
    path.parent.mkdir(parents=True)
    Image.new("RGBA", (5, 5)).save(path)
    cache = cache_module.ActivityImageCache(ha)
    await cache.async_setup()
    await cache.async_remember("Odkurzanie", "/local/activity_images/same.png")
    await cache.async_remember("Sprzątanie", "/local/activity_images/same.png")
    writes = 0
    original_save = cache._async_save

    async def count_writes():
        nonlocal writes
        writes += 1
        await original_save()

    cache._async_save = count_writes
    found = await cache.async_find_many(["Odkurzanie", "Sprzątanie"])
    assert len(found) == 2
    assert writes == 1


@pytest.mark.asyncio
async def test_cancel_running_does_not_cancel_active_job(ha, configured, monkeypatch):
    _, engine = configured
    queue = queue_module.ActivityImageQueue(ha, engine)
    monkeypatch.setattr(queue, "_kick", lambda: None)
    await queue.async_setup()
    await queue.async_enqueue(["Active", "Waiting"])
    queue._current = "Active"
    assert await queue.async_cancel(["Active", "Waiting"]) == 1
    assert queue.status()["pending"] == ["Active"]


@pytest.mark.asyncio
async def test_corrupt_cooldown_recovers(ha, configured, monkeypatch):
    _, engine = configured
    ha.saved["activity_images_jobs"] = {"next_run": "not-a-number", "pending": []}
    queue = queue_module.ActivityImageQueue(ha, engine)
    monkeypatch.setattr(queue, "_kick", lambda: None)
    await queue.async_setup()
    assert queue.status()["next_run_at"] is None


@pytest.mark.asyncio
async def test_opaque_background_fallback(ha):
    from io import BytesIO

    image = Image.new("RGB", (128, 128), (255, 0, 255))
    ImageDraw.Draw(image).ellipse((30, 30, 98, 98), fill=(25, 90, 180))
    raw = BytesIO()
    image.save(raw, format="PNG")
    data = await generated_module._maybe_remove_background(ha, raw.getvalue(), None)
    target = ha.base / "www/activity_images/opaque.png"
    status = await ha.async_add_executor_job(generated_module._process, data, target)
    assert status in ("background_removed", "review_needed_unsafe_mask", "review_needed_complex_background")
    with Image.open(target) as normalized:
        assert normalized.mode == "RGBA"
        assert normalized.height == 512


@pytest.mark.asyncio
async def test_media_response_without_file_is_rejected(ha, configured):
    _, engine = configured
    ha.services.response = {"url": "/api/media_source/proxy/bad-token"}
    with pytest.raises(ValueError, match="HTTP"):
        await engine.async_generate("Malformed URL")


def test_prompts_from_fork_are_kept():
    isolated = prompts_module.build_activity_image_instructions("Posprzątaj zabawki")
    room = prompts_module.build_activity_image_instructions("Pokój Hani")
    assert "chroma-key" in isolated
    assert "chroma-key" in room
    assert "isometric" in room
    assert "isometric" not in isolated


@pytest.mark.asyncio
async def test_lucida_valid_transparency_is_used(ha, monkeypatch):
    from io import BytesIO

    opaque = Image.new("RGB", (64, 64), (255, 255, 255))
    raw = BytesIO()
    opaque.save(raw, format="PNG")
    original = raw.getvalue()

    cutout = Image.new("RGBA", (64, 64), (25, 50, 100, 0))
    cutout.putpixel((20, 20), (25, 50, 100, 255))
    out = BytesIO()
    cutout.save(out, format="PNG")
    returned = out.getvalue()

    class Response:
        content_length = len(returned)

        @property
        def content(self):
            return self

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            pass

        def raise_for_status(self):
            pass

        async def iter_chunked(self, _size):
            yield returned

    class Client:
        def post(self, url, *, data, timeout):
            assert url == "http://lucida.test/remove"
            return Response()

    monkeypatch.setattr(generated_module, "async_get_clientsession", lambda _: Client())
    result = await generated_module._maybe_remove_background(
        ha, original, "http://lucida.test/remove"
    )
    assert result == returned


@pytest.mark.asyncio
async def test_lucida_failure_uses_original_fallback(ha, monkeypatch):
    import aiohttp
    from io import BytesIO

    opaque = Image.new("RGB", (64, 64), (255, 255, 255))
    raw = BytesIO()
    opaque.save(raw, format="PNG")

    class Client:
        def post(self, *args, **kwargs):
            raise aiohttp.ClientError("Unavailable")

    monkeypatch.setattr(generated_module, "async_get_clientsession", lambda _: Client())
    result = await generated_module._maybe_remove_background(
        ha, raw.getvalue(), "http://lucida.test/remove"
    )
    assert result == raw.getvalue()


@pytest.mark.asyncio
async def test_lucida_is_skipped_for_existing_alpha(ha, monkeypatch):
    from io import BytesIO

    image = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    image.putpixel((20, 20), (50, 20, 100, 255))
    raw = BytesIO()
    image.save(raw, format="PNG")

    def must_not_call(_):
        raise AssertionError("Lucida should not run for transparent PNG")

    monkeypatch.setattr(generated_module, "async_get_clientsession", must_not_call)
    assert await generated_module._maybe_remove_background(
        ha, raw.getvalue(), "http://lucida.test/remove"
    ) == raw.getvalue()


@pytest.mark.asyncio
async def test_selective_resolve_matches_original_calendar_contract(ha, configured, monkeypatch):
    cache, engine = configured
    queue = queue_module.ActivityImageQueue(ha, engine)
    monkeypatch.setattr(queue, "_kick", lambda: None)
    await queue.async_setup()
    path = ha.base / "www/activity_images/known.png"
    path.parent.mkdir(parents=True)
    Image.new("RGBA", (40, 30)).save(path)
    await cache.async_remember("Znane", "/local/activity_images/known.png")
    images, added = await queue.async_resolve_activity_images(
        ["Znane", "Historyczne", "Nowe", "Nowe"], enqueue_titles=["Nowe"]
    )
    assert images == {"Znane": "/local/activity_images/known.png"}
    assert added == ["Nowe"]
    assert queue.status()["pending"] == ["Nowe"]
    images, added = await queue.async_resolve_activity_images(
        ["Historyczne"], enqueue_titles=[]
    )
    assert images == {} and added == []
    resolved = await queue.async_resolve(["Znane"], enqueue_missing=False)
    assert resolved["image_metadata"]["Znane"] == {"width": 40, "height": 30}
    assert resolved["queue_size"] == 1


@pytest.mark.asyncio
async def test_failed_job_remains_pending_and_schedules_retry(ha, configured):
    _, engine = configured
    ha.services.fail = True
    queue = queue_module.ActivityImageQueue(ha, engine)
    await queue.async_setup()
    await queue.async_enqueue(["Pozniej"])
    await asyncio.wait_for(queue._worker, 5)
    assert queue.status()["pending"] == ["Pozniej"]
    assert queue.status()["next_run_at"] > 0
    assert queue._retry_task is not None
    ha.services.fail = False
    # Simulate the cooldown elapsed, without delaying the suite for a minute.
    queue._retry_task.cancel()
    try:
        await queue._retry_task
    except asyncio.CancelledError:
        pass
    queue._retry_task = None
    queue._next_run = 0
    await queue._run()
    assert queue.status()["pending"] == []
    assert queue.status()["failed"] == {}
    await queue.async_shutdown()


@pytest.mark.asyncio
async def test_legacy_home_tasks_image_urls_still_resolve(ha):
    """The original cache accepted legacy local Home Tasks pictures."""
    source = ha.base / "www/home_tasks/legacy.png"
    source.parent.mkdir(parents=True)
    Image.new("RGBA", (32, 32), (20, 40, 60, 255)).save(source)
    cache = cache_module.ActivityImageCache(ha)
    await cache.async_setup()
    await cache.async_remember("Stare zadanie", "/local/home_tasks/legacy.png")
    assert await cache.async_find("Stare zadanie") == "/local/home_tasks/legacy.png"


@pytest.mark.asyncio
async def test_external_image_url_resolves_without_network(ha):
    """The original fork accepted external URLs from already-linked tasks."""
    cache = cache_module.ActivityImageCache(ha)
    await cache.async_setup()
    await cache.async_remember("Zajecia", "https://example.com/icon.png")
    assert await cache.async_find("Zajecia") == "https://example.com/icon.png"


@pytest.mark.asyncio
async def test_disable_fuzzy_cache_lookup_without_disabling_exact_match(ha):
    source = ha.base / "www/activity_images/test.png"
    source.parent.mkdir(parents=True)
    Image.new("RGBA", (32, 32)).save(source)
    cache = cache_module.ActivityImageCache(ha)
    await cache.async_setup()
    await cache.async_remember("Odkurzanie pokoju", "/local/activity_images/test.png")
    assert await cache.async_find("Odkurzanie pokoj", similar=False) is None
    assert await cache.async_find("ODKURZANIE POKOJU", similar=False) is not None
    assert await cache.async_find("Odkurzanie pokoj") is not None


@pytest.mark.asyncio
async def test_forget_by_url_preserves_external_references(ha):
    source = ha.base / "www/activity_images/keep.png"
    source.parent.mkdir(parents=True)
    Image.new("RGBA", (32, 32)).save(source)
    cache = cache_module.ActivityImageCache(ha)
    await cache.async_setup()
    await cache.async_remember("Pierwsze", "/local/activity_images/keep.png")
    await cache.async_remember("Drugie", "/local/activity_images/keep.png")
    assert cache.references_url("/local/activity_images/keep.png?v=123")
    await cache.async_forget_url("/local/activity_images/keep.png?v=456")
    assert not cache.references_url("/local/activity_images/keep.png")
    assert source.is_file()


@pytest.mark.asyncio
async def test_new_storage_namespace_keeps_old_home_tasks_data_unchanged(ha):
    import copy
    original = {"entries": {"old": {"image_url": "https://example.com/icon.png"}}}
    ha.saved["home_tasks_image_cache"] = copy.deepcopy(original)
    cache = cache_module.ActivityImageCache(ha)
    await cache.async_setup()
    await cache.async_remember("Nowe", "https://example.com/new.png")
    assert ha.saved["home_tasks_image_cache"] == original
    assert "activity_images_image_cache" in ha.saved


@pytest.mark.asyncio
async def test_recovery_from_malformed_cache_metadata(ha):
    ha.saved["activity_images_image_cache"] = {
        "entries": {
            "test": {
                "title": "Test", "image_url": "https://example.com/test.png",
                "last_used_at": "not-a-number",
            }
        },
        "canvas_normalization_version": 2,
    }
    cache = cache_module.ActivityImageCache(ha)
    await cache.async_setup()
    assert cache._entries["test"]["last_used_at"] == 0
    assert await cache.async_find("test") == "https://example.com/test.png"


@pytest.mark.asyncio
async def test_explicit_generation_always_calls_provider(ha, configured):
    _, engine = configured
    first = await engine.async_generate("Rozwijanie")
    second = await engine.async_generate_activity_image("Rozwijanie")
    assert isinstance(second, str) and second.startswith("/local/activity_images/")
    assert second != first["image_url"]
    assert len(ha.services.calls) == 2


@pytest.mark.asyncio
async def test_existing_image_normalizer_creates_derived_files(ha):
    source = ha.base / "www/activity_images/legacy.png"
    source.parent.mkdir(parents=True)
    image = Image.new("RGBA", (80, 80), (0, 0, 0, 0))
    ImageDraw.Draw(image).rectangle((20, 20, 60, 60), fill=(25, 150, 80, 255))
    image.save(source)
    stamp = await ha.async_add_executor_job(
        generated_module.normalize_existing_generated_image, source
    )
    assert stamp > 0
    with Image.open(source) as rendered:
        assert rendered.height == 512
    assert source.with_name("legacy_thumb.webp").exists()
    assert source.with_name("legacy_qa.webp").exists()


@pytest.mark.asyncio
async def test_cancelling_failed_retry_clears_failure_marker(ha, configured, monkeypatch):
    _, engine = configured
    queue = queue_module.ActivityImageQueue(ha, engine)
    monkeypatch.setattr(queue, "_kick", lambda: None)
    await queue.async_setup()
    await queue.async_enqueue(["Powtorka"])
    queue._failed["powtorka"] = "Network unavailable"
    assert await queue.async_cancel(["Powtorka"]) == 1
    assert not queue.status()["failed"]
    assert not queue.status()["pending"]

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
        self.config_entries = SimpleNamespace(
            async_forward_entry_setups=self._forward_platforms,
            async_unload_platforms=self._unload_platforms,
        )
        self.forwarded = []

    async def _forward_platforms(self, entry, platforms):
        self.forwarded.append(("load", tuple(platforms)))

    async def _unload_platforms(self, entry, platforms):
        self.forwarded.append(("unload", tuple(platforms)))
        return True

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
        self.registered = {}

    def async_register(self, domain, service, func, *, schema=None, supports_response=None):
        self.registered[(domain, service)] = (func, schema, supports_response)

    def has_service(self, domain, service):
        return (domain, service) in self.registered

    def async_remove(self, domain, service):
        self.registered.pop((domain, service), None)

    async def async_call(self, domain, service, data, **kwargs):
        if (domain, service) in self.registered:
            func, schema, _supports_response = self.registered[(domain, service)]
            if schema is not None:
                data = schema(data)
            return await func(SimpleNamespace(hass=self.hass, data=data))
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
    engine = engine_module.ActivityImageGenerator(
        ha, cache, {const_module.CONF_AI_ENTITY: "ai_task.mock"}
    )
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
async def test_reference_and_shared_activity_prompt(ha, configured):
    cache, engine = configured
    engine.settings[const_module.CONF_REFERENCE_MEDIA_ID] = "media-source://media_source/local/reference.png"
    result = await engine.async_generate("Pokój Hani")
    payload = ha.services.calls[-1][2]
    assert "isometric" not in payload["instructions"].lower()
    assert "glossy" in payload["instructions"].lower()
    assert "Design first for immediate recognition at 25 px." in payload["instructions"]
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
    # Terminal errors are recorded, not retried again after a cooldown/restart.
    assert queue.status()["pending"] == []
    assert len(ha.services.calls) == 1
    await queue.async_shutdown()
    restored = queue_module.ActivityImageQueue(ha, engine)
    restored._kick = lambda: None
    await restored.async_setup()
    assert restored.status()["pending"] == []
    assert len(restored.status()["failed"]) == 1
    assert await restored.async_enqueue(["Niepowodzenie"]) == []
    assert await restored.async_clear_errors() == 1
    assert await restored.async_enqueue(["Niepowodzenie"]) == ["Niepowodzenie"]
    await restored.async_shutdown()


@pytest.mark.asyncio
async def test_queue_shutdown_keeps_pending_job(ha, configured, monkeypatch):
    _, engine = configured
    started = asyncio.Event()

    async def wait_for_provider(*_args, **_kwargs):
        started.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(ha.services, "async_call", wait_for_provider)
    queue = queue_module.ActivityImageQueue(ha, engine)
    await queue.async_setup()
    await queue.async_enqueue(["Poczekaj"])
    await asyncio.wait_for(started.wait(), 2)
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
async def test_legacy_cooldown_is_ignored(ha, configured, monkeypatch):
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


def test_unabridged_normal_activity_prompt_and_no_room_variant():
    isolated = prompts_module.build_activity_image_instructions("Posprzątaj zabawki")
    room = prompts_module.build_activity_image_instructions("Pokój Hani")
    assert "chroma-key" in isolated
    assert "chroma-key" in room
    assert "isometric" not in room
    assert "isometric" not in isolated
    assert isolated.split("Posprzątaj zabawki", 1)[-1] == room.split("Pokój Hani", 1)[-1]
    assert "do not draw or simulate transparency" in isolated.lower()


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
async def test_failed_job_is_not_implicitly_retried(ha, configured):
    _, engine = configured
    ha.services.fail = True
    queue = queue_module.ActivityImageQueue(ha, engine)
    await queue.async_setup()
    await queue.async_enqueue(["Pozniej"])
    await asyncio.wait_for(queue._worker, 5)
    assert queue.status()["pending"] == []
    assert queue.status()["next_run_at"] is None
    assert len(queue.status()["failed"]) == 1
    assert await queue.async_enqueue(["Pozniej"]) == []
    ha.services.fail = False
    assert await queue.async_clear_errors() == 1
    assert await queue.async_enqueue(["Pozniej"]) == ["Pozniej"]
    # Let the new job run: a manual clear makes one fresh cycle possible.
    if queue._worker and not queue._worker.done():
        await asyncio.wait_for(queue._worker, 5)
    else:
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
@pytest.mark.asyncio
async def test_engine_429_retries_provider_three_total_calls_only(ha, configured, monkeypatch):
    """End-to-end fake AI provider: cache and processing happen once after retries."""
    from custom_components.activity_images import retry as retry_module

    async def instant_sleep(_seconds):
        return None

    monkeypatch.setattr(retry_module.asyncio, "sleep", instant_sleep)
    cache, engine = configured
    original_call = ha.services.async_call
    requests = 0

    class Http429(Exception):
        status = 429
        headers = {"Retry-After": "1"}

    async def flaky_call(*args, **kwargs):
        nonlocal requests
        requests += 1
        if requests <= 2:
            raise Http429("Rate limited")
        return await original_call(*args, **kwargs)

    monkeypatch.setattr(ha.services, "async_call", flaky_call)
    out = await engine.async_generate("Test retry generator")
    assert requests == 3
    assert out["reused"] is False
    assert (ha.base / "www" / out["image_url"].split("?", 1)[0].removeprefix("/local/")).exists()
    await engine.async_generate("Test retry generator")
    assert requests == 3
    assert await cache.async_find("Test retry generator") == out["image_url"]


@pytest.mark.asyncio
async def test_engine_401_does_not_retry_or_cache(ha, configured, monkeypatch):
    from custom_components.activity_images import retry as retry_module

    async def instant_sleep(_seconds):
        raise AssertionError("No delay or retry expected for 401")

    monkeypatch.setattr(retry_module.asyncio, "sleep", instant_sleep)
    cache, engine = configured
    requests = 0

    class Http401(Exception):
        status = 401

    async def permanent(*args, **kwargs):
        nonlocal requests
        requests += 1
        raise Http401("Invalid provider token")

    monkeypatch.setattr(ha.services, "async_call", permanent)
    with pytest.raises(Http401):
        await engine.async_generate("Test permanent error")
    assert requests == 1
    assert await cache.async_find("Test permanent error") is None


# Isolated integration-entrypoint tests: exercise the actual public HA service
# registrations with a fake HA service registry, without touching the user's HA.
def _load_integration_entrypoint():
    import importlib.util
    import voluptuous as vol

    if "homeassistant.config_entries" not in sys.modules:
        _package("homeassistant.config_entries")
    if "homeassistant.exceptions" not in sys.modules:
        _package("homeassistant.exceptions")
    if "homeassistant.helpers.config_validation" not in sys.modules:
        _package("homeassistant.helpers.config_validation")
    core = sys.modules["homeassistant.core"]
    core.ServiceCall = SimpleNamespace
    core.callback = lambda func: func
    if "homeassistant.helpers.event" not in sys.modules:
        _package("homeassistant.helpers.event")
    sys.modules["homeassistant.helpers.event"].async_track_state_change_event = (
        lambda hass, ids, listener: lambda: None
    )
    sys.modules["homeassistant.helpers.event"].async_track_time_interval = (
        lambda hass, listener, interval: lambda: None
    )
    if "homeassistant.util" not in sys.modules:
        _package("homeassistant.util")
    if "homeassistant.util.dt" not in sys.modules:
        _package("homeassistant.util.dt")
    from datetime import datetime, timezone
    sys.modules["homeassistant.util.dt"].now = lambda: datetime.now(timezone.utc)
    sys.modules["homeassistant.util"].dt = sys.modules["homeassistant.util.dt"]
    core.SupportsResponse = SimpleNamespace(OPTIONAL="optional")
    sys.modules["homeassistant.config_entries"].ConfigEntry = object

    class HomeAssistantError(Exception):
        pass

    sys.modules["homeassistant.exceptions"].HomeAssistantError = HomeAssistantError
    validation = sys.modules["homeassistant.helpers.config_validation"]
    validation.string = str
    validation.boolean = vol.Boolean()
    validation.ensure_list = lambda value: value if isinstance(value, list) else [value]
    spec = importlib.util.spec_from_file_location(
        "custom_components.activity_images._entrypoint_test", ROOT / "__init__.py"
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


class MockConfigEntry:
    def __init__(self):
        self.entry_id = "single_config_entry"
        self.data = {const_module.CONF_AI_ENTITY: "ai_task.mock"}
        self.options = {}
        self.runtime_data = None
        self.callbacks = []

    def add_update_listener(self, listener):
        return lambda: None

    def async_on_unload(self, callback):
        self.callbacks.append(callback)


@pytest.mark.asyncio
async def test_actions_register_once_in_setup_before_config_entry(ha, configured):
    integration = _load_integration_entrypoint()
    assert await integration.async_setup(ha, {})
    assert len(ha.services.registered) == 8
    for name in ("generate_image", "resolve_images", "queue_status",
                 "cancel_queued", "clear_errors", "forget_image",
                 "refresh_calendars"):
        assert ha.services.has_service("activity_images", name)
    with pytest.raises(sys.modules["homeassistant.exceptions"].HomeAssistantError,
                       match="not configured"):
        await ha.services.async_call("activity_images", "queue_status", {})

    entry = MockConfigEntry()
    assert await integration.async_setup_entry(ha, entry)
    assert entry.runtime_data is not None
    assert entry.runtime_data.cache is not None
    assert len(ha.services.registered) == 8
    first = await ha.services.async_call(
        "activity_images", "generate_image", {"title": "Green Waste Collection"}
    )
    assert first["reused"] is False
    assert first["qa_url"].endswith("_qa.webp")
    again = await ha.services.async_call(
        "activity_images", "generate_image", {"title": "Green Waste Collection"}
    )
    assert again["reused"] is True
    assert len(ha.services.calls) == 1

    lookup = await ha.services.async_call(
        "activity_images", "resolve_images",
        {"titles": ["Green Waste Collection", "Different Event"]}
    )
    assert lookup["images"] == {"Green Waste Collection": first["image_url"]}
    assert lookup["queued"] == []
    assert lookup["image_metadata"]["Green Waste Collection"]["height"] == 512
    assert lookup["queue_size"] == 0

    status = await ha.services.async_call("activity_images", "queue_status", {})
    assert status["pending"] == []
    assert status["failed"] == {}

    with pytest.raises(sys.modules["homeassistant.exceptions"].HomeAssistantError,
                       match="Specify a title"):
        await ha.services.async_call("activity_images", "forget_image", {})
    removed = await ha.services.async_call(
        "activity_images", "forget_image", {"title": "Green Waste Collection"}
    )
    assert removed == {"removed": 1}
    assert await entry.runtime_data.cache.async_find("Green Waste Collection") is None
    assert (ha.base / "www" / first["image_url"].split("?", 1)[0].removeprefix("/local/")).is_file()

    assert await integration.async_unload_entry(ha, entry)
    assert len(ha.services.registered) == 8
    assert entry.entry_id not in ha.data["activity_images"]
    with pytest.raises(sys.modules["homeassistant.exceptions"].HomeAssistantError,
                       match="not configured"):
        await ha.services.async_call("activity_images", "queue_status", {})


@pytest.mark.asyncio
async def test_actions_queue_and_validate_request_limits(ha, monkeypatch):
    import voluptuous as vol

    integration = _load_integration_entrypoint()
    assert await integration.async_setup(ha, {})
    # Disable automatic worker kickoff to inspect queued data deterministically.
    monkeypatch.setattr(queue_module.ActivityImageQueue, "_kick", lambda self: None)
    entry = MockConfigEntry()
    await integration.async_setup_entry(ha, entry)
    queue = entry.runtime_data.queue
    result = await ha.services.async_call(
        "activity_images", "resolve_images",
        {"titles": ["Jazz", "Jazz", "Turnen"], "enqueue_missing": True}
    )
    assert result["queued"] == ["Jazz", "Turnen"]
    assert result["queue_size"] == 2

    cancel = await ha.services.async_call(
        "activity_images", "cancel_queued", {"titles": ["Jazz"]}
    )
    assert cancel == {"cancelled": 1}
    assert queue.status()["pending"] == ["Turnen"]
    queue._failed["turnen"] = "Mock failure"
    assert await ha.services.async_call("activity_images", "clear_errors", {}) == {"cleared": 1}
    assert queue.status()["failed"] == {}

    with pytest.raises(vol.MultipleInvalid):
        await ha.services.async_call(
            "activity_images", "resolve_images",
            {"titles": [f"Activity {i}" for i in range(65)]}
        )
    with pytest.raises(vol.MultipleInvalid):
        await ha.services.async_call(
            "activity_images", "generate_image", {"title": "x" * 256}
        )
    await integration.async_unload_entry(ha, entry)


@pytest.mark.asyncio
async def test_unload_and_reconfigure_keep_services_and_cached_images(ha):
    integration = _load_integration_entrypoint()
    await integration.async_setup(ha, {})
    entry = MockConfigEntry()
    await integration.async_setup_entry(ha, entry)
    first = await ha.services.async_call(
        "activity_images", "generate_image", {"title": "Jazz Class"}
    )
    assert first["reused"] is False
    await integration.async_unload_entry(ha, entry)
    assert ha.services.has_service("activity_images", "resolve_images")
    entry.options = {const_module.CONF_REFERENCE_MEDIA_ID: "media-source://media_source/local/style.png"}
    await integration.async_setup_entry(ha, entry)
    assert entry.runtime_data.generator.settings[const_module.CONF_REFERENCE_MEDIA_ID].endswith("style.png")
    second = await ha.services.async_call(
        "activity_images", "generate_image", {"title": "Jazz Class"}
    )
    assert second["reused"] is True
    assert len(ha.services.calls) == 1
    assert len(ha.services.registered) == 8
    await integration.async_unload_entry(ha, entry)


def _calendar_test_setup(ha, configured, monkeypatch, events, *,
                         enabled=False, calendars=("calendar.school",)):
    integration = _load_integration_entrypoint()
    from custom_components.activity_images.calendar_adapter import ActivityCalendarAdapter

    cache, engine = configured
    queue = queue_module.ActivityImageQueue(ha, engine)
    monkeypatch.setattr(queue, "_kick", lambda: None)

    async def fake_calendar_service(call):
        return events

    ha.services.async_register("calendar", "get_events", fake_calendar_service)
    settings = {
        const_module.CONF_CALENDARS: list(calendars),
        const_module.CONF_LOOKAHEAD_DAYS: 30,
        const_module.CONF_AUTO_GENERATE: enabled,
    }
    adapter = ActivityCalendarAdapter(ha, cache, queue, settings)
    return adapter, queue, integration


def test_calendar_rules_escaped_for_js_regex():
    _load_integration_entrypoint()
    import re
    from custom_components.activity_images.calendar_adapter import escape_event_title
    samples = [
        "Dentist (Hanna) + checkup?",
        "Escape [square] {braces} .*",
        r"Windows\home & kids | family",
        "Green Waste Collection",
        "Pfadi – Musikschule",
    ]
    for title in samples:
        escaped = escape_event_title(title)
        assert re.fullmatch(escaped, title)
        assert re.fullmatch(escaped, "Not " + title) is None
    assert escape_event_title("Green Waste Collection") == "Green Waste Collection"


@pytest.mark.asyncio
async def test_calendar_adapter_reads_opt_in_events_and_reuses_cache(ha, configured, monkeypatch):
    cache, _engine = configured
    img = ha.base / "www/activity_images/known.png"
    img.parent.mkdir(parents=True)
    Image.new("RGBA", (40, 30)).save(img)
    await cache.async_remember(
        "Green Waste Collection", "/local/activity_images/known.png"
    )
    data = {
        "calendar.school": {"events": [
            {"summary": "Green Waste Collection", "start": "2026-11-10"},
            {"summary": "Green Waste Collection", "start": "2026-11-17"},
            {"summary": "Dentist (Hanna) + checkup?", "start": "2026-11-11"},
            {"summary": "", "start": "2026-11-11"},
            {"summary": "Cancelled", "status": "cancelled"},
        ]},
        "calendar.work": {"events": [
            {"summary": "Green Waste Collection", "start": "2026-11-19"},
            {"summary": "Lunch", "start": "2026-11-20"},
        ]},
    }
    adapter, queue, _integration = _calendar_test_setup(
        ha, configured, monkeypatch, data,
        calendars=("calendar.school", "calendar.work"),
    )
    seen = []
    adapter.add_listener(lambda: seen.append(dict(adapter.images)))
    result = await adapter.async_refresh()
    assert result["tracked_titles"] == 4  # Counts calendar/title pairs, not unique titles.
    assert result["images"] == 1
    assert result["automatic_generation"] is False
    assert queue.status()["pending"] == []
    assert adapter.images == {
        "Green Waste Collection": "/local/activity_images/known.png"
    }
    assert len(seen) == 1
    rules = adapter.rules
    assert len(rules) == 4  # One image & fallback on each selected calendar.
    assert len([r for r in rules if r.get("label_type") == "image"]) == 2
    assert all(r["entity"].startswith("calendar.") for r in rules)
    assert all(r.get("blocklist") != "(?!)" for r in rules)
    assert adapter.last_scan
    await adapter.async_shutdown()


@pytest.mark.asyncio
async def test_calendar_adapter_generation_opt_in_and_no_duplicate_queue(ha, configured, monkeypatch):
    events = {"calendar.school": {"events": [
        {"summary": "Jazz Class"},
        {"summary": "Jazz Class"},
        {"summary": "Swimming Lessons"},
        {"summary": "School"},
    ]}}
    adapter, queue, _ = _calendar_test_setup(ha, configured, monkeypatch, events, enabled=True)
    await adapter.async_refresh()
    assert queue.status()["pending"] == ["Jazz Class", "Swimming Lessons", "School"]
    await adapter.async_refresh()
    assert queue.status()["pending"] == ["Jazz Class", "Swimming Lessons", "School"]
    assert queue.status()["next_run_at"] is None
    await adapter.async_shutdown()


@pytest.mark.asyncio
async def test_calendar_adapter_image_saved_refreshes_map_without_polling(ha, configured, monkeypatch):
    events = {"calendar.school": {"events": [{"summary": "Doctor (Hanna) + Checkup?"}]}}
    adapter, queue, _ = _calendar_test_setup(ha, configured, monkeypatch, events)
    calls = 0
    original_call = ha.services.async_call

    async def track_call(*args, **kwargs):
        nonlocal calls
        if args[:2] == ("calendar", "get_events"):
            calls += 1
        return await original_call(*args, **kwargs)

    monkeypatch.setattr(ha.services, "async_call", track_call)
    await adapter.async_refresh()
    assert calls == 1
    assert adapter.images == {}
    captured = []
    adapter.add_listener(lambda: captured.append(dict(adapter.images)))
    img = ha.base / "www/activity_images/new.png"
    img.parent.mkdir(parents=True)
    Image.new("RGBA", (60, 40)).save(img)
    url = "/local/activity_images/new.png?v=23"
    await configured[0].async_remember("Doctor (Hanna) + Checkup?", url)
    adapter.image_saved("Doctor (Hanna) + Checkup?", url)
    await asyncio.wait_for(adapter._reconcile_task, 3)
    assert calls == 1  # Refreshing the library never re-queries calendar provider.
    assert adapter.images == {"Doctor (Hanna) + Checkup?": url}
    image_rule = [r for r in adapter.rules if r.get("label_type") == "image"][0]
    assert image_rule["allowlist"] == r"^Doctor \(Hanna\) \+ Checkup\?$"
    assert image_rule["label"] == url
    assert captured[-1] == adapter.images
    await adapter.async_shutdown()


@pytest.mark.asyncio
async def test_calendar_refresh_failure_preserves_last_known_icons(ha, configured, monkeypatch):
    events = {"calendar.school": {"events": [{"summary": "First icon"}]}}
    adapter, queue, _ = _calendar_test_setup(ha, configured, monkeypatch, events)
    await adapter.async_refresh()
    old_titles = dict(adapter.calendar_titles)

    async def unavailable(*args, **kwargs):
        raise RuntimeError("Temporary calendar failure")

    monkeypatch.setattr(ha.services, "async_call", unavailable)
    result = await adapter.async_refresh()
    assert result["error"] == "RuntimeError: Temporary calendar failure"
    assert adapter.calendar_titles == old_titles
    assert adapter.rules[0]["entity"] == "calendar.school"
    await adapter.async_shutdown()


@pytest.mark.asyncio
async def test_calendar_title_limit_and_shutdown_unsubscribe(ha, configured, monkeypatch):
    events = {"calendar.school": {"events": [
        {"summary": f"Event {i}"} for i in range(150)
    ]}}
    adapter, queue, _ = _calendar_test_setup(ha, configured, monkeypatch, events, enabled=True)
    await adapter.async_refresh()
    assert len(adapter.calendar_titles["calendar.school"]) == 64
    assert len(queue.status()["pending"]) == 64
    await adapter.async_shutdown()


@pytest.mark.asyncio
async def test_calendar_start_tracks_state_and_periodic_scan(ha, configured, monkeypatch):
    events = {"calendar.school": {"events": [{"summary": "An event"}]}}
    adapter, queue, _ = _calendar_test_setup(ha, configured, monkeypatch, events)
    import custom_components.activity_images.calendar_adapter as adapter_module

    tracked = []
    unsubscribed = []
    callbacks = []

    def fake_track_state(hass, ids, callback):
        tracked.append(("state", tuple(ids)))
        callbacks.append(callback)
        return lambda: unsubscribed.append("state")

    def fake_track_time(hass, callback, interval):
        tracked.append(("time", interval.total_seconds()))
        callbacks.append(callback)
        return lambda: unsubscribed.append("time")

    monkeypatch.setattr(adapter_module, "async_track_state_change_event", fake_track_state)
    monkeypatch.setattr(adapter_module, "async_track_time_interval", fake_track_time)
    await adapter.async_start()
    assert ("state", ("calendar.school",)) in tracked
    assert ("time", 15 * 60) in tracked
    assert adapter.calendar_titles["calendar.school"] == ["An event"]
    callbacks[0](None)
    await asyncio.wait_for(adapter._scan_task, 3)
    await adapter.async_shutdown()
    assert set(unsubscribed) == {"state", "time"}


@pytest.mark.asyncio
async def test_calendar_service_response_must_be_mapping(ha, configured, monkeypatch):
    adapter, queue, _ = _calendar_test_setup(ha, configured, monkeypatch, [])
    result = await adapter.async_refresh()
    assert "did not return a calendar mapping" in result["error"]
    await adapter.async_shutdown()


@pytest.mark.asyncio
async def test_real_engine_callback_updates_adapter_after_generation(ha, configured, monkeypatch):
    cache, engine = configured
    events = {"calendar.school": {"events": [{"summary": "New activity"}]}}
    adapter, queue, _ = _calendar_test_setup(ha, configured, monkeypatch, events)
    await adapter.async_refresh()
    engine.image_saved_callback = adapter.image_saved
    result = await engine.async_generate("New activity")
    assert result["reused"] is False
    await asyncio.wait_for(adapter._reconcile_task, 3)
    assert adapter.images["New activity"] == result["image_url"]
    assert len(ha.services.calls) == 1
    engine.image_saved_callback = None
    await adapter.async_shutdown()


@pytest.mark.asyncio
async def test_integration_calendar_service_and_platform_lifecycle(ha, monkeypatch):
    integration = _load_integration_entrypoint()
    await integration.async_setup(ha, {})
    entry = MockConfigEntry()
    entry.options = {
        const_module.CONF_CALENDARS: ["calendar.school"],
        const_module.CONF_AUTO_GENERATE: False,
        const_module.CONF_LOOKAHEAD_DAYS: 30,
    }
    async def get_events(call):
        return {"calendar.school": {"events": [{"summary": "New event"}]}}

    ha.services.async_register("calendar", "get_events", get_events)
    assert await integration.async_setup_entry(ha, entry)
    assert ("load", ("sensor",)) in ha.forwarded
    assert entry.runtime_data.calendar.calendar_titles["calendar.school"] == ["New event"]
    assert len(ha.services.registered) == 9  # Eight activity actions + calendar service.
    result = await ha.services.async_call("activity_images", "refresh_calendars", {})
    assert result["tracked_titles"] == 1
    assert await integration.async_unload_entry(ha, entry)
    assert ("unload", ("sensor",)) in ha.forwarded
    assert entry.entry_id not in ha.data["activity_images"]

@pytest.mark.asyncio
async def test_existing_png_can_be_linked_without_provider(ha, monkeypatch):
    integration = _load_integration_entrypoint()
    await integration.async_setup(ha, {})
    entry = MockConfigEntry()
    entry.data = {}
    entry.options = {
        const_module.CONF_CALENDARS: ["calendar.school"],
        const_module.CONF_AUTO_GENERATE: False,
    }

    async def get_events(call):
        return {"calendar.school": {"events": [{"summary": "Green Waste Collection"}]}}

    ha.services.async_register("calendar", "get_events", get_events)
    await integration.async_setup_entry(ha, entry)
    assert entry.runtime_data.generator.settings.get(const_module.CONF_AI_ENTITY) is None
    assert entry.runtime_data.calendar.images == {}

    file = ha.base / "www" / "activity_images" / "existing.png"
    file.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGBA", (32, 32), (25, 20, 10, 255)).save(file)
    response = await ha.services.async_call(
        "activity_images", "link_existing_image",
        {"title": "Green Waste Collection", "image_url": "/local/activity_images/existing.png"},
    )
    assert response["linked"] is True
    assert entry.runtime_data.calendar.images == {
        "Green Waste Collection": "/local/activity_images/existing.png"
    }
    assert len(ha.services.calls) == 0
    assert file.exists()
    with pytest.raises(sys.modules["homeassistant.exceptions"].HomeAssistantError):
        await ha.services.async_call(
            "activity_images", "link_existing_image",
            {"title": "Bad path", "image_url": "/local/activity_images/../../secret.png"},
        )
    with pytest.raises(sys.modules["homeassistant.exceptions"].HomeAssistantError):
        await ha.services.async_call(
            "activity_images", "link_existing_image",
            {"title": "Missing", "image_url": "/local/activity_images/no-such-file.png"},
        )
    assert file.exists()
    await integration.async_unload_entry(ha, entry)

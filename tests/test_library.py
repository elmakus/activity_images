"""Library inventory, candidate lifecycle and persistence failure regressions."""
from __future__ import annotations

import asyncio
from io import BytesIO
from types import SimpleNamespace
from urllib.parse import quote

import pytest
import test_behavior as behavior
from PIL import Image

from custom_components.activity_images.image_cache import MappingConflict
from custom_components.activity_images.library import ActivityImageLibrary

cache_module = behavior.cache_module
queue_module = behavior.queue_module
ha = behavior.ha
configured = behavior.configured


def picture(ha, name, color="red"):
    path = ha.base / "www" / "activity_images" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (12, 10), color).save(path)
    return path


async def finished(queue):
    for _ in range(200):
        if queue._worker is not None and queue._worker.done():
            await queue._worker
            return
        await asyncio.sleep(0.01)
    raise AssertionError("Queue did not finish")


@pytest.mark.asyncio
async def test_inventory_complete_grouped_safe_and_cached(ha, configured, monkeypatch):
    cache, _ = configured
    picture(ha, "master.png")
    picture(ha, "master_thumb.webp")
    picture(ha, "master_qa.webp")
    picture(ha, "orphan_qa.webp")
    picture(ha, "orphan_thumb.webp")
    picture(ha, "standalone.webp")
    picture(ha, "activity-icons-style-board-v1.png")
    corrupt = ha.base / "www/activity_images/corrupt.png"
    corrupt.write_bytes(b"not an image")
    outside = ha.base / "private.png"
    Image.new("RGB", (10, 10)).save(outside)
    (corrupt.parent / "escape.png").symlink_to(outside)
    await cache.async_remember("Morning Run", "/local/activity_images/master.png?v=1")
    await cache.async_remember("Jogging", "/local/activity_images/master.png?v=1")
    library = ActivityImageLibrary(ha, cache)
    result = await library.async_list()
    assert result["total"] == 4
    assert result["stats"] == {
        "images": 4, "linked": 1, "unlinked": 3, "mappings": 2,
        "storage_bytes": sum(p.stat().st_size for p in corrupt.parent.iterdir() if not p.is_symlink()),
        "supported_images": 4000,
    }
    master = next(item for item in result["items"] if item["filename"] == "master.png")
    assert master["titles"] == ["Jogging", "Morning Run"]
    assert master["origin"] == "unknown"
    details = await library.async_get(master["id"])
    assert details["width"] == 12 and details["height"] == 10
    assert details["created_at"] is None
    assert set(details["assets"]) == {"master", "thumbnail", "qa"}
    assert (await library.async_list(filter="generated"))["total"] == 0
    assert (await library.async_list(query="run"))["total"] == 1
    assert (await library.async_list(filter="unlinked", limit=1, offset=1))["total"] == 3
    assert len((await library.async_list(limit=1))["items"]) == 1
    orphan = next(item for item in result["items"] if item["filename"] == "orphan_qa.webp")
    assert orphan["title"] == "orphan_qa"
    assert "thumbnail" in (await library.async_get(orphan["id"]))["assets"]
    import custom_components.activity_images.library as module
    original_verify = module._verify
    calls = []
    def counted(data, **kwargs):
        calls.append(1)
        return original_verify(data, **kwargs)
    monkeypatch.setattr(module, "_verify", counted)
    await library.async_list(query="M")
    await library.async_list(query="Mo")
    await library.async_list(refresh=True)
    assert calls == []
    picture(ha, "master.png", "blue")
    await library.async_list(refresh=True)
    assert len(calls) == 1
    (corrupt.parent / "master.png").unlink()
    (corrupt.parent / "master.png").symlink_to(outside)
    with pytest.raises(OSError):
        await library.async_read(master["id"], "master")
    with pytest.raises(ValueError):
        await library.async_read("../../private", "master")
    with pytest.raises(ValueError):
        await library.async_read(orphan["id"], "../../private.png")


@pytest.mark.asyncio
@pytest.mark.parametrize("filename", ["orphan.webp", "imported.jpg", "imported.gif", "Summer ? #1%.webp"])
async def test_linked_imported_formats_reuse_through_home_tasks_as_png(ha, configured, filename):
    cache, engine = configured
    path = picture(ha, filename)
    original = path.read_bytes()
    library = ActivityImageLibrary(ha, cache)
    image_id = library.image_id(filename)
    url = await library.async_url(image_id)
    assert url == "/local/activity_images/" + quote(filename, safe="")
    await cache.async_link("Reused imported image", url)
    assert await cache.async_find("Reused imported image") == url
    assert (await library.async_get(image_id))["titles"] == ["Reused imported image"]
    wrapper, ai = behavior._load_ai_wrapper()
    entry = SimpleNamespace(
        entry_id="imported_format_test", data=engine.settings, options={},
        runtime_data=SimpleNamespace(generator=engine),
    )
    entity = wrapper.ActivityImagesAITaskEntity(entry)
    entity.hass = ha
    entity._attr_entity_id = "ai_task.activity_images"
    task = ai.GenImageTask(
        name="home_tasks_imported_image",
        instructions="Reused imported image\n\nThe image must have a square 1:1 aspect ratio.",
    )
    result = await entity._async_generate_image(task, SimpleNamespace(conversation_id="imported"))
    assert result.mime_type == "image/png"
    with Image.open(BytesIO(result.image_data)) as image:
        assert image.format == "PNG" and image.size == (12, 10)
    assert path.read_bytes() == original
    assert ha.services.calls == []


@pytest.mark.asyncio
async def test_inventory_defers_pixel_decoding_until_preview_or_link(ha, configured, monkeypatch):
    cache, _ = configured
    picture(ha, "lazy.png")
    library = ActivityImageLibrary(ha, cache)
    calls = []
    original_load = Image.Image.load

    def track_load(image, *args, **kwargs):
        calls.append(1)
        return original_load(image, *args, **kwargs)

    monkeypatch.setattr(Image.Image, "load", track_load)
    result = await library.async_list()
    assert result["total"] == 1
    assert calls == []
    image_id = result["items"][0]["id"]
    await library.async_read(image_id, "master")
    assert calls
    calls.clear()
    await library.async_url(image_id)
    assert calls


@pytest.mark.asyncio
async def test_mapping_atomic_conflicts_and_alias_preservation(ha, configured, monkeypatch):
    cache, _ = configured
    await cache.async_remember("Running", "/local/activity_images/old.png?v=1")
    await cache.async_remember("Alias", "/local/activity_images/old.png?v=1")
    detached = cache.entries()
    detached["running"]["image_url"] = "bad"
    assert cache.exact("Running").endswith("old.png?v=1")
    with pytest.raises(MappingConflict):
        await cache.async_link("Running", "/local/activity_images/new.png")
    with pytest.raises(MappingConflict):
        await cache.async_unlink("Running", "/local/activity_images/new.png")
    saved = cache.entries()
    async def fail(_data):
        raise OSError("disk full")
    monkeypatch.setattr(cache._store, "async_save", fail)
    for operation in (
        cache.async_remember("Running", "/local/activity_images/new.png"),
        cache.async_remember_many([{"title": "Other", "image_url": "https://example.com/test"}]),
        cache.async_unlink("Running", "/local/activity_images/old.png"),
        cache.async_link("Running", "/local/activity_images/new.png", replace=True),
        cache.async_record_image("/local/activity_images/new.png", {"origin": "generated"}),
    ):
        with pytest.raises(OSError, match="disk full"):
            await operation
        assert cache.entries() == saved
    assert cache.image_metadata("/local/activity_images/new.png") == {}


@pytest.mark.asyncio
async def test_library_4000_images_no_eviction_and_bounded_pages(ha, configured):
    cache, _ = configured
    root = ha.base / "www/activity_images"
    root.mkdir(parents=True)
    source = picture(ha, "seed.png").read_bytes()
    tasks = []
    for i in range(4001):
        filename = f"image_{i:04}.png"
        (root / filename).write_bytes(source)
        tasks.append({"title": f"Activity {i:04}", "image_url": f"/local/activity_images/{filename}"})
    (root / "seed.png").unlink()
    await cache.async_remember_many(tasks)
    restored = cache_module.ActivityImageCache(ha)
    await restored.async_setup()
    assert len(restored.entries()) == 4001
    library = ActivityImageLibrary(ha, restored)
    result = await library.async_list(limit=96, offset=3980)
    assert result["total"] == result["stats"]["images"] == 4001
    assert result["stats"]["mappings"] == 4001
    assert len(result["items"]) == 21
    assert result["stats"]["storage_bytes"] == len(source) * 4001


@pytest.mark.asyncio
async def test_manual_candidates_review_accept_cas_and_discard(ha, configured):
    cache, engine = configured
    first = await engine.async_generate("Walking")
    await cache.async_remember("Alias", first["image_url"])
    old_path = ha.base / "www" / first["image_url"].split("?", 1)[0].removeprefix("/local/")
    old_bytes = old_path.read_bytes()
    queue = queue_module.ActivityImageQueue(ha, engine)
    await queue.async_setup()
    library = ActivityImageLibrary(ha, cache)
    image_id = library.image_id(old_path.name)
    with pytest.raises(ValueError, match="exact associated"):
        await library.async_generate(queue, "Different title", image_id)
    job = await library.async_generate(queue, "Walking", image_id)
    assert job["key"] == "walking"
    again = await queue.async_candidate("WALKING!")
    assert job["id"] == again["id"]
    await finished(queue)
    job = queue.status()["jobs"][-1]
    assert job["state"] == "review"
    assert cache.exact("Walking") == first["image_url"]
    assert old_path.read_bytes() == old_bytes
    assert len(ha.services.calls) == 2
    assert (await library.async_list())["total"] == 2
    candidate = await library.async_get(job["image_id"])
    assert candidate["origin"] == "generated" and candidate["created_at"]
    accepted = await queue.async_accept(job["id"])
    assert accepted["state"] == "accepted"
    assert cache.exact("Alias") == first["image_url"]
    assert cache.exact("Walking") != first["image_url"]
    assert (await queue.async_accept(job["id"]))["state"] == "accepted"
    stale = await queue.async_candidate("Walking")
    await finished(queue)
    await cache.async_link("Walking", first["image_url"], replace=True)
    with pytest.raises(MappingConflict):
        await queue.async_accept(stale["id"])
    assert queue._job(stale["id"])["state"] == "review"
    assert (await queue.async_discard(stale["id"]))["state"] == "discarded"
    assert old_path.read_bytes() == old_bytes
    unlinked = picture(ha, "unlinked.png")
    library.invalidate()
    assert (await library.async_generate(queue, "New Activity", library.image_id(unlinked.name)))["state"] == "pending"
    await finished(queue)
    await queue.async_shutdown()


@pytest.mark.asyncio
async def test_manual_interrupted_restart_requires_explicit_retry(ha, configured):
    cache, engine = configured
    started = asyncio.Event()
    async def blocked(_title, **kwargs):
        started.set()
        await asyncio.Event().wait()
    original = engine.async_generate
    engine.async_generate = blocked
    queue = queue_module.ActivityImageQueue(ha, engine)
    await queue.async_setup()
    job = await queue.async_candidate("Paused Activity")
    await started.wait()
    await queue.async_shutdown()
    assert queue.status()["jobs"][-1]["state"] == "interrupted"
    engine.async_generate = original
    resumed = queue_module.ActivityImageQueue(ha, engine)
    await resumed.async_setup()
    assert resumed.status()["pending"] == []
    assert resumed._worker is None
    assert len(ha.services.calls) == 0
    retry = await resumed.async_retry(job_id=job["id"])
    assert retry["id"] != job["id"]
    await finished(resumed)
    assert len(ha.services.calls) == 1
    assert resumed.status()["jobs"][-1]["state"] == "review"
    assert cache.exact("Paused Activity") is None
    await resumed.async_shutdown()


@pytest.mark.asyncio
async def test_pending_manual_cancel_errors_and_history(ha, configured, monkeypatch):
    _, engine = configured
    queue = queue_module.ActivityImageQueue(ha, engine)
    monkeypatch.setattr(queue, "_kick", lambda: None)
    await queue.async_setup()
    job = await queue.async_candidate("Cancelled Activity")
    assert await queue.async_cancel(["Cancelled Activity"]) == 1
    assert queue._job(job["id"])["state"] == "cancelled"
    assert queue.status()["pending"] == []
    review = queue._new_job("Retained Candidate", "manual")
    review["state"] = "review"
    queue._jobs.append(review)
    for i in range(150):
        terminal = queue._new_job(f"Finished {i}", "background")
        terminal["state"] = "completed"
        queue._jobs.append(terminal)
    pending = await queue.async_candidate("Pending Activity")
    queue._trim_history()
    assert len(queue._jobs) == 102
    assert queue._job(review["id"])["state"] == "review"
    assert queue._job(pending["id"])["state"] == "pending"
    queue._failed["failed"] = "error"
    await queue.async_clear_errors()
    assert queue.status()["pending"] == ["Pending Activity"]
    assert len(ha.services.calls) == 0
    await queue.async_shutdown()


@pytest.mark.asyncio
async def test_manual_failure_not_replayed_clear_or_reload(ha, configured):
    _, engine = configured
    queue = queue_module.ActivityImageQueue(ha, engine)
    await queue.async_setup()
    ha.services.fail = True
    # RuntimeError is permanent in the existing provider retry policy.
    job = await queue.async_candidate("Failed Activity")
    await finished(queue)
    assert queue._job(job["id"])["state"] == "failed"
    assert len(ha.services.calls) == 1
    await queue.async_clear_errors()
    await queue.async_shutdown()
    resumed = queue_module.ActivityImageQueue(ha, engine)
    await resumed.async_setup()
    assert resumed.status()["pending"] == []
    assert resumed.status()["jobs"][-1]["state"] == "failed"
    assert len(ha.services.calls) == 1
    ha.services.fail = False
    await resumed.async_retry(job_id=job["id"])
    await finished(resumed)
    assert len(ha.services.calls) == 2
    await resumed.async_shutdown()


@pytest.mark.asyncio
async def test_candidate_enqueue_persistence_failure_never_calls_provider(ha, configured, monkeypatch):
    _, engine = configured
    queue = queue_module.ActivityImageQueue(ha, engine)
    await queue.async_setup()
    async def fail(_data):
        raise OSError("disk full")
    monkeypatch.setattr(queue._store, "async_save", fail)
    with pytest.raises(OSError):
        await queue.async_candidate("Not paid")
    assert queue.status()["pending"] == []
    assert queue.status()["jobs"] == []
    assert queue._worker is None
    assert len(ha.services.calls) == 0


@pytest.mark.asyncio
async def test_review_survives_reload_and_no_implicit_calendar_requeue(ha, configured, monkeypatch):
    cache, engine = configured
    queue = queue_module.ActivityImageQueue(ha, engine)
    await queue.async_setup()
    job = await queue.async_candidate("Review Activity")
    await finished(queue)
    result = queue._job(job["id"])["result_url"]
    await queue.async_shutdown()
    resumed = queue_module.ActivityImageQueue(ha, engine)
    await resumed.async_setup()
    assert resumed.status()["jobs"][-1]["state"] == "review"
    assert await resumed.async_enqueue(["Review Activity"]) == []
    assert (await resumed.async_candidate("Review Activity"))["id"] == job["id"]
    assert len(ha.services.calls) == 1
    async def fail(_data):
        raise OSError("queue storage unavailable")
    original_save = resumed._store.async_save
    monkeypatch.setattr(resumed._store, "async_save", fail)
    with pytest.raises(OSError):
        await resumed.async_accept(job["id"])
    assert cache.exact("Review Activity") is None
    assert resumed._job(job["id"])["state"] == "review"
    monkeypatch.setattr(resumed._store, "async_save", original_save)
    assert (await resumed.async_accept(job["id"]))["state"] == "accepted"
    assert cache.exact("Review Activity") == result
    await cache.async_unlink("Review Activity", result)
    await resumed.async_clear_errors()
    assert await resumed.async_enqueue(["Review Activity"]) == []
    await resumed.async_shutdown()


@pytest.mark.asyncio
async def test_pending_manual_never_resumes_but_legacy_pending_does(ha, configured, monkeypatch):
    _, engine = configured
    queue = queue_module.ActivityImageQueue(ha, engine)
    monkeypatch.setattr(queue, "_kick", lambda: None)
    await queue.async_setup()
    job = await queue.async_candidate("Manual Waiting")
    await queue.async_enqueue(["Legacy Waiting"])
    await queue.async_shutdown()
    resumed = queue_module.ActivityImageQueue(ha, engine)
    await resumed.async_setup()
    await finished(resumed)
    assert resumed._job(job["id"])["state"] == "interrupted"
    assert len(ha.services.calls) == 1
    assert engine.cache.exact("Legacy Waiting")
    assert engine.cache.exact("Manual Waiting") is None
    assert any(item["state"] == "completed" for item in resumed.status()["jobs"])
    await resumed.async_shutdown()


@pytest.mark.asyncio
async def test_failed_candidate_processing_never_changes_old_files_or_aliases(ha, configured, monkeypatch):
    cache, engine = configured
    first = await engine.async_generate("Safe Activity")
    await cache.async_remember("Safe Alias", first["image_url"])
    root = ha.base / "www/activity_images"
    before = {p.name: p.read_bytes() for p in root.iterdir()}
    import custom_components.activity_images.engine as module
    async def fail(*args, **kwargs):
        raise OSError("processing failed")
    monkeypatch.setattr(module, "async_save_generated_image", fail)
    queue = queue_module.ActivityImageQueue(ha, engine)
    await queue.async_setup()
    job = await queue.async_candidate("Safe Activity")
    await finished(queue)
    assert queue._job(job["id"])["state"] == "failed"
    assert cache.exact("Safe Activity") == cache.exact("Safe Alias") == first["image_url"]
    assert {p.name: p.read_bytes() for p in root.iterdir()} == before
    await queue.async_clear_errors()
    assert await queue.async_enqueue(["Safe Activity"]) == []
    assert len(ha.services.calls) == 2
    await queue.async_shutdown()


@pytest.mark.asyncio
async def test_shutdown_before_queue_load_does_not_overwrite_saved_work(ha, configured):
    _, engine = configured
    ha.saved["activity_images_jobs"] = {"pending": ["Important Legacy Job"], "failed": {}}
    queue = queue_module.ActivityImageQueue(ha, engine)
    await queue.async_shutdown()
    assert ha.saved["activity_images_jobs"]["pending"] == ["Important Legacy Job"]


@pytest.mark.asyncio
async def test_failed_mapping_commit_serializes_concurrent_alias_edit(ha, configured, monkeypatch):
    cache, _ = configured
    await cache.async_remember("Selected", "/local/activity_images/old.png")
    await cache.async_remember("Alias", "/local/activity_images/old.png")
    original_save = cache._store.async_save
    started, release = asyncio.Event(), asyncio.Event()
    attempts = 0
    async def fail_first(value):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            started.set()
            await release.wait()
            raise OSError("disk failure")
        await original_save(value)
    monkeypatch.setattr(cache._store, "async_save", fail_first)
    failed = asyncio.create_task(cache.async_link("Selected", "/local/activity_images/new.png", replace=True))
    await started.wait()
    alias = asyncio.create_task(cache.async_link("Alias", "/local/activity_images/alias.png", replace=True))
    await asyncio.sleep(0)
    assert not alias.done()
    release.set()
    with pytest.raises(OSError):
        await failed
    await alias
    assert cache.exact("Selected") == "/local/activity_images/old.png"
    assert cache.exact("Alias") == "/local/activity_images/alias.png"
    restored = cache_module.ActivityImageCache(ha)
    await restored.async_setup()
    assert restored.entries() == cache.entries()


@pytest.mark.asyncio
async def test_cancelled_queue_write_drains_executor_before_shutdown_and_reload(ha, configured, monkeypatch):
    import threading
    _, engine = configured
    queue = queue_module.ActivityImageQueue(ha, engine)
    await queue.async_setup()
    original_save = queue._store.async_save
    started, release = asyncio.Event(), threading.Event()
    async def executor_save(value):
        started.set()
        await ha.async_add_executor_job(release.wait)
        await original_save(value)
    monkeypatch.setattr(queue._store, "async_save", executor_save)
    request = asyncio.create_task(queue.async_candidate("Interrupted Commit"))
    await started.wait()
    request.cancel()
    await asyncio.sleep(0)
    assert not request.done()
    shutdown = asyncio.create_task(queue.async_shutdown())
    await asyncio.sleep(0)
    assert not shutdown.done()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await request
    await shutdown
    assert ha.saved["activity_images_jobs"]["pending"] == ["Interrupted Commit"]
    resumed = queue_module.ActivityImageQueue(ha, engine)
    await resumed.async_setup()
    assert resumed.status()["pending"] == []
    assert resumed.status()["jobs"][-1]["state"] == "interrupted"
    assert len(ha.services.calls) == 0
    await resumed.async_shutdown()


@pytest.mark.asyncio
async def test_cancelled_cache_write_drains_and_keeps_committed_memory(ha, configured, monkeypatch):
    import threading
    cache, _ = configured
    original_save = cache._store.async_save
    started, release = asyncio.Event(), threading.Event()
    async def executor_save(value):
        started.set()
        await ha.async_add_executor_job(release.wait)
        await original_save(value)
    monkeypatch.setattr(cache._store, "async_save", executor_save)
    request = asyncio.create_task(cache.async_remember("Committed Title", "/local/activity_images/committed.png"))
    await started.wait()
    request.cancel()
    await asyncio.sleep(0)
    assert not request.done()
    alias = asyncio.create_task(cache.async_remember("Alias", "/local/activity_images/alias.png"))
    await asyncio.sleep(0)
    assert not alias.done()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await request
    await alias
    assert cache.exact("Committed Title") == "/local/activity_images/committed.png"
    restored = cache_module.ActivityImageCache(ha)
    await restored.async_setup()
    assert restored.entries() == cache.entries()


@pytest.mark.asyncio
async def test_failed_accept_preserves_concurrent_other_alias_edit(ha, configured, monkeypatch):
    cache, engine = configured
    old = await engine.async_generate("Selected Activity")
    await cache.async_remember("Other Alias", old["image_url"])
    queue = queue_module.ActivityImageQueue(ha, engine)
    await queue.async_setup()
    job = await queue.async_candidate("Selected Activity")
    await finished(queue)
    original_save = queue._store.async_save
    started, release = asyncio.Event(), asyncio.Event()
    async def fail(value):
        started.set()
        await release.wait()
        raise OSError("job persistence failed")
    monkeypatch.setattr(queue._store, "async_save", fail)
    accept = asyncio.create_task(queue.async_accept(job["id"]))
    await started.wait()
    await cache.async_link("Other Alias", "/local/activity_images/concurrent.png", replace=True)
    release.set()
    with pytest.raises(OSError):
        await accept
    assert cache.exact("Selected Activity") == old["image_url"]
    assert cache.exact("Other Alias") == "/local/activity_images/concurrent.png"
    assert queue._job(job["id"])["state"] == "review"
    monkeypatch.setattr(queue._store, "async_save", original_save)
    await queue.async_shutdown()


@pytest.mark.asyncio
async def test_forget_url_predicate_evaluated_after_queued_replacement(ha, configured):
    cache, _ = configured
    old, new = "/local/activity_images/old.png", "/local/activity_images/new.png"
    await cache.async_remember("Selected", old)
    await cache._write_lock.acquire()
    replace = asyncio.create_task(cache.async_link("Selected", new, replace=True))
    await asyncio.sleep(0)
    forget = asyncio.create_task(cache.async_forget(url=old))
    await asyncio.sleep(0)
    cache._write_lock.release()
    await replace
    assert await forget == 0
    assert cache.exact("Selected") == new
    await cache._write_lock.acquire()
    replace = asyncio.create_task(cache.async_link("Selected", old, replace=True))
    await asyncio.sleep(0)
    forget_url = asyncio.create_task(cache.async_forget_url(new))
    await asyncio.sleep(0)
    cache._write_lock.release()
    await replace
    await forget_url
    assert cache.exact("Selected") == old


@pytest.mark.asyncio
@pytest.mark.parametrize("fuzzy", [False, True])
async def test_missing_file_cleanup_does_not_remove_replaced_mapping(ha, configured, monkeypatch, fuzzy):
    cache, _ = configured
    old, new = "/local/activity_images/missing.png", "/local/activity_images/new.png"
    await cache.async_remember("Walking Outside", old)
    entered, release = asyncio.Event(), asyncio.Event()
    async def missing(url):
        assert url == old
        entered.set()
        await release.wait()
        return False
    monkeypatch.setattr(cache, "_async_url_exists", missing)
    lookup = asyncio.create_task(cache.async_find("Walking Outside!" if not fuzzy else "Walking Outsides", touch=False, similar=fuzzy))
    await entered.wait()
    await cache.async_link("Walking Outside", new, replace=True)
    release.set()
    assert await lookup is None
    assert cache.exact("Walking Outside") == new


@pytest.mark.asyncio
async def test_inventory_rejects_sparse_byte_limit_and_pixel_budget_before_decode(ha, configured, monkeypatch):
    from custom_components.activity_images.library import (
        MAX_IMAGE_BYTES,
        _read_owned,
        _verify,
    )
    cache, _ = configured
    picture(ha, "valid.png")
    root = ha.base / "www/activity_images"
    sparse = root / "sparse.png"
    with sparse.open("wb") as stream:
        stream.truncate(MAX_IMAGE_BYTES + 1)
    with pytest.raises(ValueError, match="oversized"):
        _read_owned(root, sparse.name)
    oversized_pixels = root / "pixels.png"
    Image.new("1", (5001, 5000)).save(oversized_pixels)
    import PIL.PngImagePlugin
    def forbidden_load(*args, **kwargs):
        raise AssertionError("Pixel-budget rejection must happen before decoding")
    with monkeypatch.context() as patcher:
        patcher.setattr(PIL.PngImagePlugin.PngImageFile, "load", forbidden_load)
        with pytest.raises(ValueError, match="25 million"):
            _verify(oversized_pixels.read_bytes())
    library = ActivityImageLibrary(ha, cache)
    result = await library.async_list(limit=1)
    assert result["total"] == 1
    assert result["items"][0]["filename"] == "valid.png"
    assert result["stats"]["storage_bytes"] > MAX_IMAGE_BYTES


@pytest.mark.asyncio
async def test_manager_clear_persists_legacy_retry_barrier_but_public_clear_is_compatible(ha, configured, monkeypatch):
    _, engine = configured
    queue = queue_module.ActivityImageQueue(ha, engine)
    await queue.async_setup()
    ha.services.fail = True
    await queue.async_enqueue(["Legacy Failure"])
    await finished(queue)
    assert len(ha.services.calls) == 1
    assert queue.status()["failed"]
    await queue.async_clear_errors(preserve_retry_blocks=True)
    assert queue.status()["failed"] == {}
    assert queue.status()["retry_blocked"]
    assert await queue.async_enqueue(["Legacy Failure"]) == []
    await queue.async_shutdown()
    resumed = queue_module.ActivityImageQueue(ha, engine)
    await resumed.async_setup()
    assert resumed.status()["failed"] == {}
    assert await resumed.async_enqueue(["Legacy Failure"]) == []
    assert len(ha.services.calls) == 1
    ha.services.fail = False
    assert (await resumed.async_retry(title="Legacy Failure"))["queued"] == ["Legacy Failure"]
    await finished(resumed)
    assert len(ha.services.calls) == 2
    assert resumed.status()["retry_blocked"] == []
    resumed._failed["public clear"] = "old error"
    await resumed.async_clear_errors(preserve_retry_blocks=True)
    await resumed.async_clear_errors()  # Existing public action deliberately removes the block.
    monkeypatch.setattr(resumed, "_kick", lambda: None)
    assert await resumed.async_enqueue(["Public Clear"]) == ["Public Clear"]
    await resumed.async_shutdown()


@pytest.mark.asyncio
async def test_active_excludes_idle_pending_and_includes_direct_generation(ha, configured, monkeypatch):
    _, engine = configured
    queue = queue_module.ActivityImageQueue(ha, engine)
    await queue.async_setup()
    monkeypatch.setattr(queue, "_kick", lambda: None)
    await queue.async_enqueue(["Waiting Only"])
    assert queue.status()["pending"] == ["Waiting Only"]
    assert queue.status()["active"] is False
    entered, release = asyncio.Event(), asyncio.Event()
    native = ha.services.async_call
    async def blocked(*args, **kwargs):
        entered.set()
        await release.wait()
        return await native(*args, **kwargs)
    monkeypatch.setattr(ha.services, "async_call", blocked)
    generating = asyncio.create_task(engine.async_generate("Direct Public Generation", force=True))
    await entered.wait()
    assert queue.status()["active"] is True
    assert queue.status()["current"] is None
    release.set()
    await generating
    assert queue.status()["active"] is False
    await queue.async_shutdown()

"""Regression tests for failure boundaries using disposable files and fake providers."""
from __future__ import annotations

import importlib
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image, ImageDraw

import test_behavior as behavior
from test_library import picture
from test_library_deletion import manager

ha = behavior.ha
configured = behavior.configured
generated = behavior.generated_module
cache_module = behavior.cache_module
processing = importlib.import_module("custom_components.activity_images.image_processing")
files = importlib.import_module("custom_components.activity_images.deletion_files")


def png(image, fmt="PNG"):
    data = BytesIO()
    image.save(data, format=fmt)
    return data.getvalue()


@pytest.mark.asyncio
async def test_generated_source_rejects_remote_empty_and_oversize(ha, monkeypatch):
    with pytest.raises(ValueError, match="local media-source"):
        await generated._read_source(ha, "https://example.invalid/image.png")
    async def resolve(*args):
        return SimpleNamespace(path=None)
    monkeypatch.setattr(generated, "async_resolve_media", resolve)
    with pytest.raises(ValueError, match="local file"):
        await generated._read_source(ha, "media-source://test/remote")
    empty = ha.base / "empty"
    empty.touch()
    with pytest.raises(ValueError, match="empty"):
        generated._read_local_source(empty)
    too_large = ha.base / "oversized"
    too_large.write_bytes(b"12345")
    monkeypatch.setattr(generated, "MAX_IMAGE_BYTES", 4)
    with pytest.raises(ValueError, match="regular file"):
        generated._read_local_source(too_large)



def test_normalization_complex_border_and_alpha_boundaries():
    with pytest.raises(ValueError, match="dimensions"):
        processing.validate_dimensions(Image.new("RGB", (0, 10)))
    with pytest.raises(ValueError, match="transparent"):
        processing.normalize_icon_canvas(Image.new("RGBA", (12, 12)))
    full = Image.new("RGBA", (30, 512), "red")
    result = processing.normalize_icon_canvas(full)
    assert result.tobytes() == full.tobytes() and result is not full
    # A barely visible isolated edge pixel can disappear during downsampling.
    faint = Image.new("RGBA", (128, 1024))
    ImageDraw.Draw(faint).rectangle((20, 100, 100, 1023), fill="red")
    faint.putpixel((64, 0), (255, 0, 0, 9))
    result = processing.normalize_icon_canvas(faint)
    assert 510 <= result.height <= 512
    assert processing._visible_bbox(result) == (0, 0, result.width, result.height)


@pytest.mark.parametrize("kind", ["config", "storage", "quarantine"])
def test_quarantine_rejects_unsafe_permissions(tmp_path, kind):
    root = tmp_path / "owned"
    root.mkdir()
    target = root / "original.png"
    target.write_bytes(b"original")
    evidence = files.snapshot(root, [target.name])
    quarantine = tmp_path / ".storage/activity_images_deletions"
    quarantine.mkdir(parents=True, mode=0o700)
    unsafe = {"config": tmp_path, "storage": quarantine.parent, "quarantine": quarantine}[kind]
    unsafe.chmod(0o777)
    try:
        with pytest.raises(ValueError, match="Unsafe"):
            files.cleanup(root, quarantine, evidence, "operation", "image")
        assert target.read_bytes() == b"original"
        assert not list(quarantine.iterdir())
    finally:
        unsafe.chmod(0o700)


def test_deletion_path_and_missing_atomic_rename_fail_closed(tmp_path, monkeypatch):
    with pytest.raises(ValueError, match="absolute"):
        files.open_root(Path("relative"))
    with pytest.raises(ValueError, match="private deletion"):
        files._open_quarantine(tmp_path / "elsewhere")
    root = tmp_path / "owned"
    root.mkdir()
    target = root / "old.png"
    target.write_bytes(b"original")
    evidence = files.snapshot(root, [target.name])
    monkeypatch.setattr(files.ctypes, "CDLL", lambda *a, **kw: SimpleNamespace())
    with pytest.raises(OSError, match="rename is unavailable"):
        files.cleanup(root, tmp_path / ".storage/activity_images_deletions", evidence, "operation", "image")
    assert target.read_bytes() == b"original"


@pytest.mark.asyncio
async def test_cache_batch_metadata_and_fuzzy_missing_preserve_associations(ha, configured):
    cache, _ = configured
    picture(ha, "original.png")
    original = "/local/activity_images/original.png"
    await cache.async_setup()  # Idempotent.
    await cache.async_remember("", original)
    await cache.async_remember_many([{"title": "Course German A1", "image_url": original}])
    before = cache.entries()
    await cache.async_remember_many([{"title": "Course German A1", "image_url": original}, {"title": "", "image_url": original}])
    assert cache.entries() == before
    assert await cache.async_find("!!!") is None
    assert await cache.async_metadata(["Course German A1"]) == {"Course German A1": {"width": 12, "height": 10}}
    assert cache.references_url(None) is False
    await cache.async_forget_url(None)
    root = ha.base / "www/activity_images"
    (root / "original.png").unlink()
    assert await cache.async_find("Course German A2", prune=False) is None
    assert cache.entries() == before
    assert await cache.async_find("Course German A2") is None
    assert not cache.entries()
    assert await cache.async_image_metadata({"bad": None, "remote": "https://example.invalid/image", "missing": original,
        "unsafe": "/local/activity_images/../secret.png"}) == {}
    directory = root / "folder.png"
    directory.mkdir()
    assert await cache.async_image_metadata({"directory": "/local/activity_images/folder.png"}) == {}


@pytest.mark.asyncio
async def test_recency_write_failure_does_not_publish_unpersisted_mapping_state(ha, configured, monkeypatch):
    cache, _ = configured
    picture(ha, "old.png")
    await cache.async_remember("Existing", "/local/activity_images/old.png")
    before = cache.entries()
    now = before["existing"]["last_used_at"] + 7200
    monkeypatch.setattr(cache_module.time, "time", lambda: now)
    async def fail():
        raise OSError("Local write failed")
    monkeypatch.setattr(cache, "_async_save", fail)
    with pytest.raises(OSError):
        await cache.async_find("Existing")
    assert cache.entries() == before
    assert not ha.services.calls


@pytest.mark.asyncio
async def test_queue_validation_and_lifecycle_prevent_dispatch(ha, configured, monkeypatch):
    cache, engine = configured
    queue = behavior.queue_module.ActivityImageQueue(ha, engine)
    await queue.async_setup()
    monkeypatch.setattr(queue, "_kick", lambda: None)
    assert await queue.async_enqueue([], calendar_sources=[]) == []
    with pytest.raises(ValueError, match="verified"):
        await queue.async_enqueue(["Unverified"], calendar_sources=[])
    with pytest.raises(ValueError, match="No verified"):
        await queue.async_enqueue(["Different"], calendar_sources=[{"calendar_entity_id": "calendar.test", "raw_summary": "Another"}])
    assert not queue._jobs
    picture(ha, "old.png")
    await cache.async_remember("Existing", "/local/activity_images/old.png")
    assert await queue.async_enqueue([None, "Existing", "Waiting"]) == ["Waiting"]
    with pytest.raises(ValueError, match="Job not found"):
        await queue.async_accept("unknown")
    job = queue._jobs[0]
    with pytest.raises(ValueError, match="review candidate"):
        await queue.async_accept(job["id"])
    with pytest.raises(ValueError, match="review candidate"):
        await queue.async_discard(job["id"])
    with pytest.raises(ValueError, match="manual jobs"):
        await queue.async_retry(job_id=job["id"])
    with pytest.raises(ValueError, match="No failed legacy"):
        await queue.async_retry(title="Unknown")
    monkeypatch.setattr(behavior.queue_module, "MAX_PENDING_JOBS", 1)
    with pytest.raises(ValueError, match="queue is full"):
        await queue.async_generate_direct("New")
    await queue.async_shutdown()
    with pytest.raises(ValueError, match="unloading"):
        await queue.async_enqueue(["Late"])
    assert not ha.services.calls


@pytest.mark.asyncio
async def test_deletion_confirmation_retry_and_shutdown_guards(ha, configured, monkeypatch):
    picture(ha, "old.png")
    deletions = manager(ha, configured)
    monkeypatch.setattr(deletions, "_kick", lambda: None)
    plan = await deletions.async_plan([deletions.library.image_id("old.png")])
    with pytest.raises(ValueError, match="confirmation"):
        await deletions.async_delete(plan["token"], False, False)
    operation = await deletions.async_delete(plan["token"], False, True)
    assert (await deletions.async_delete(plan["token"], False, True))["id"] == operation["id"]
    with pytest.raises(ValueError, match="not retryable"):
        await deletions.async_retry_plan(operation["id"])
    with pytest.raises(ValueError, match="confirmation"):
        await deletions.async_retry(operation["id"], "unknown", False)
    with pytest.raises(ValueError, match="confirmation"):
        await deletions.async_abandon(operation["id"], False)
    with pytest.raises(ValueError, match="not found"):
        await deletions.async_retry_plan("unknown")
    await deletions.async_shutdown()
    with pytest.raises(ValueError, match="unloading"):
        await deletions.async_plan([deletions.library.image_id("old.png")])
    assert (ha.base / "www/activity_images/old.png").exists()
    assert not ha.services.calls


@pytest.mark.asyncio
async def test_calendar_ignore_limits_and_live_demand_fail_closed(ha, configured, monkeypatch):
    from custom_components.activity_images.engine import GenerationSuppressed
    events = {"calendar.school": {"events": [{"summary": "Known activity"}]}}
    adapter, queue, _ = behavior._calendar_test_setup(
        ha, configured, monkeypatch, events, enabled=True
    )
    await adapter.async_refresh()
    assert await adapter.async_read_live_demand() == ["Known activity"]
    for title, ignored in (("", True), ("Title", 1)):
        with pytest.raises(ValueError, match="Invalid exact title"):
            await adapter.async_ignore(title, ignored, adapter.ignore_revision)
    adapter.ignored_titles = frozenset(f"Ignored {i}" for i in range(512))
    with pytest.raises(ValueError, match="At most 512"):
        await adapter.async_ignore("Another", True, adapter.ignore_revision)
    ha._states["calendar.school"].state = "unavailable"
    with pytest.raises(GenerationSuppressed, match="unavailable"):
        await adapter.async_read_live_demand()
    ha._states["calendar.school"].state = "off"
    ha.services.async_remove("calendar", "get_events")
    with pytest.raises(GenerationSuppressed, match="service is unavailable"):
        await adapter.async_read_live_demand()
    await adapter.async_shutdown()
    with pytest.raises(ValueError, match="unloading"):
        await adapter.async_ignore("Another", True, adapter.ignore_revision)
    await adapter.async_update_settings({})
    assert not ha.services.calls


@pytest.mark.asyncio
async def test_calendar_live_read_rejects_malformed_and_excessive_demand(ha, configured, monkeypatch):
    from custom_components.activity_images.engine import GenerationSuppressed
    events = {"calendar.school": {"events": []}}
    adapter, _, _ = behavior._calendar_test_setup(ha, configured, monkeypatch, events, enabled=False)
    for payload in (None, {"events": [None]*513}, {"events": [{"summary": f"Title {i}"} for i in range(65)]}):
        async def respond(_call):
            return {"calendar.school": payload}
        ha.services.async_register("calendar", "get_events", respond)
        with pytest.raises(GenerationSuppressed):
            await adapter.async_read_live_demand()
    async def changed(_call):
        ha._states["calendar.school"].state = "unavailable"
        return events
    ha.services.async_register("calendar", "get_events", changed)
    with pytest.raises(GenerationSuppressed, match="selection changed"):
        await adapter.async_read_live_demand()
    await adapter.async_shutdown()
    assert not ha.services.calls


@pytest.mark.asyncio
async def test_queue_managed_job_requires_deletion_owner(ha, configured, monkeypatch):
    _, engine = configured
    queue = behavior.queue_module.ActivityImageQueue(ha, engine)
    monkeypatch.setattr(queue, "_kick", lambda: None)
    await queue.async_setup()
    deletion = {"operation_id": "operation", "image_id": "image", "key": "replacement", "job_id": "job"}
    first = await queue.async_candidate("Replacement", deletion=deletion, job_id="job")
    assert (await queue.async_candidate("Replacement", deletion=deletion, job_id="job"))["id"] == first["id"]
    with pytest.raises(ValueError, match="outstanding"):
        await queue.async_candidate("Replacement")
    with pytest.raises(ValueError, match="managed by their deletion"):
        await queue.async_accept(first["id"])
    with pytest.raises(ValueError, match="managed by their deletion"):
        await queue.async_discard(first["id"])
    with pytest.raises(ValueError, match="deletion operation"):
        await queue.async_retry(job_id=first["id"])
    await queue._run()  # No live deletion authorizer exists.
    assert queue._job(first["id"])["state"] == "cancelled"
    assert not ha.services.calls
    await queue.async_shutdown()


@pytest.mark.asyncio
async def test_retry_does_not_override_outstanding_work_or_capacity(ha, configured, monkeypatch):
    _, engine = configured
    queue = behavior.queue_module.ActivityImageQueue(ha, engine)
    await queue.async_setup()
    monkeypatch.setattr(queue, "_kick", lambda: None)
    await queue.async_enqueue(["Waiting"])
    queue._retry_blocked.add("waiting")
    with pytest.raises(ValueError, match="outstanding"):
        await queue.async_retry(title="Waiting")
    queue._retry_blocked.add("another")
    monkeypatch.setattr(behavior.queue_module, "MAX_PENDING_JOBS", 1)
    with pytest.raises(ValueError, match="queue is full"):
        await queue.async_retry(title="Another")
    assert queue._retry_blocked == {"waiting", "another"}
    assert not ha.services.calls
    await queue.async_shutdown()


@pytest.mark.asyncio
async def test_deletion_journal_failure_retains_original_and_observable_error(ha, configured, monkeypatch):
    cache, _ = configured
    original = picture(ha, "old.png")
    url = "/local/activity_images/old.png"
    await cache.async_remember("Old title", url)
    deletions = manager(ha, configured)
    monkeypatch.setattr(deletions, "_kick", lambda: None)
    plan = await deletions.async_plan([deletions.library.image_id("old.png")])
    operation = await deletions.async_delete(plan["token"], False, True)
    async def fail(*args, **kwargs):
        raise OSError("Disk full")
    monkeypatch.setattr(cache, "async_journal", fail)
    await deletions._run()
    public = deletions.operations()[0]
    assert public["id"] == operation["id"] and public["state"] == "failed"
    assert public["items"][0]["error"] == "Persistence failed; explicit retry required"
    assert original.exists() and cache.exact("Old title") == url
    assert cache.reserved(url=url) and not ha.services.calls
    await deletions.async_shutdown()


@pytest.mark.asyncio
async def test_deletion_expired_plan_and_invalid_authority_do_not_generate(ha, configured, monkeypatch):
    cache, _ = configured
    original = picture(ha, "old.png")
    url = "/local/activity_images/old.png"
    await cache.async_remember("Old title", url)
    deletions = manager(ha, configured, ["Old title"])
    monkeypatch.setattr(deletions, "_kick", lambda: None)
    image_id = deletions.library.image_id(original.name)
    plan = await deletions.async_plan([image_id])
    deletions._plans[plan["token"]]["expires_at"] = 0
    with pytest.raises(cache_module.MappingConflict, match="expired"):
        await deletions.async_delete(plan["token"], True, True)
    plan = await deletions.async_plan([image_id])
    operation = await deletions.async_delete(plan["token"], True, True)
    from custom_components.activity_images.engine import GenerationSuppressed
    with pytest.raises(GenerationSuppressed, match="authorization changed"):
        await deletions.async_authorize_replacement("Old title", {
            "operation_id": operation["id"], "image_id": image_id, "key": "old title", "job_id": "invalid"
        })
    assert original.exists() and cache.exact("Old title") == url and not ha.services.calls
    await deletions.async_shutdown()


@pytest.mark.asyncio
async def test_deletion_refresh_error_blocks_planning(ha, configured):
    original = picture(ha, "old.png")
    deletions = manager(ha, configured)
    deletions.calendar.calendars = ("calendar.test",)
    deletions.queue.engine.settings["calendar_entity_ids"] = ["calendar.test"]
    async def fail():
        raise RuntimeError("Calendar offline")
    deletions.calendar.async_refresh_for_deletion = fail
    plan = await deletions.async_plan([deletions.library.image_id(original.name)])
    assert "could not be verified" in plan["items"][0]["blocked_reason"]
    with pytest.raises(cache_module.MappingConflict, match="blocked"):
        await deletions.async_delete(plan["token"], False, True)
    assert original.exists() and not ha.services.calls
    await deletions.async_shutdown()


@pytest.mark.asyncio
async def test_calendar_cache_recheck_and_listener_failure(ha, configured, monkeypatch):
    from custom_components.activity_images.engine import GenerationSuppressed
    adapter, _, _ = behavior._calendar_test_setup(
        ha, configured, monkeypatch, {"calendar.school": {"events": [{"summary": "Known activity"}]}}, enabled=True
    )
    await adapter.async_refresh()
    sources = adapter.automatic_sources("Known activity")
    assert sources
    picture(ha, "known.png")
    await configured[0].async_remember("Known activity", "/local/activity_images/known.png")
    with pytest.raises(GenerationSuppressed, match="existing image"):
        await adapter.async_authorize_generation("Known activity", sources)
    notifications = []
    def fail():
        raise RuntimeError("Synthetic listener failure")
    adapter.add_listener(fail)
    adapter.add_listener(lambda: notifications.append(True))
    await adapter.async_ignore("Known activity", True, adapter.ignore_revision)
    assert notifications == [True] and not adapter.automatic_sources()
    await adapter.async_shutdown()
    assert not ha.services.calls


@pytest.mark.asyncio
async def test_deletion_rejects_original_changed_after_review(ha, configured, monkeypatch):
    cache, _ = configured
    original = picture(ha, "old.png")
    url = "/local/activity_images/old.png"
    await cache.async_remember("Old title", url)
    deletions = manager(ha, configured, ["Old title"])
    monkeypatch.setattr(deletions, "_kick", lambda: None)
    plan = await deletions.async_plan([deletions.library.image_id("old.png")])
    operation = await deletions.async_delete(plan["token"], True, True)
    stored = deletions._operation(operation["id"])
    item = stored["items"][0]
    original.write_bytes(b"new unrelated file")
    with pytest.raises(cache_module.MappingConflict, match="Original image changed"):
        await deletions._validate_original(stored, item)
    assert original.read_bytes() == b"new unrelated file"
    assert cache.exact("Old title") == url and not ha.services.calls
    await deletions.async_shutdown()


@pytest.mark.asyncio
async def test_deletion_recovery_refuses_unjournaled_opaque_master(ha, configured):
    from custom_components.activity_images.engine import generation_stem
    deletions = manager(ha, configured)
    title = "Unjournaled"
    stem = generation_stem(title, "test-job")
    master = picture(ha, stem + ".png")
    for suffix in ("_thumb.webp", "_qa.webp"):
        picture(ha, stem + suffix)
    Image.new("RGB", (20, 20), "red").save(master)
    item = {"needed_titles": [title], "jobs": {"unjournaled": "test-job"}, "candidates": {}}
    assert await deletions._recoverable(item) == {}
    assert master.exists() and not ha.services.calls
    await deletions.async_shutdown()


@pytest.mark.asyncio
async def test_deletion_rejects_missing_image_and_pending_consumer(ha, configured, monkeypatch):
    cache, _ = configured
    original = picture(ha, "old.png")
    await cache.async_remember("Existing", "/local/activity_images/old.png")
    deletions = manager(ha, configured)
    with pytest.raises(cache_module.MappingConflict, match="Image unavailable"):
        await deletions.async_plan(["a"*32])
    monkeypatch.setattr(deletions.queue, "_kick", lambda: None)
    await deletions.queue.async_candidate("Existing")
    plan = await deletions.async_plan([deletions.library.image_id(original.name)])
    assert "Pending generation" in plan["items"][0]["blocked_reason"]
    assert original.exists() and not ha.services.calls
    await deletions.queue.async_shutdown()
    await deletions.async_shutdown()


@pytest.mark.asyncio
async def test_deletion_requires_verified_calendar_state_even_after_refresh(ha, configured):
    deletions = manager(ha, configured)
    cal = deletions.calendar
    cal.calendars = ("calendar.test",)
    deletions.queue.engine.settings["calendar_entity_ids"] = ["calendar.test"]
    cal.last_scan = None
    assert "successful refresh" in deletions._demand_error()
    cal.last_scan = "now"
    def fail():
        raise RuntimeError("State read failed")
    cal.status = fail
    cal._demand_truncated = False
    assert "could not be verified" in deletions._demand_error()
    cal.status = lambda: {"complete": False}
    cal._demand_truncated = True
    assert "exceeded" in deletions._demand_error()
    async def refresh():
        cal.last_scan = "new"
    cal.async_refresh_for_deletion = refresh
    cal.coverage_stale = fail
    await deletions._refresh_demand()
    assert "could not be verified" in deletions._demand_error()
    assert not ha.services.calls
    await deletions.async_shutdown()


@pytest.mark.asyncio
async def test_deletion_retry_refuses_changed_candidate_evidence(ha, configured, monkeypatch):
    from custom_components.activity_images.engine import generation_stem
    cache, _ = configured
    original = picture(ha, "old.png")
    url = "/local/activity_images/old.png"
    await cache.async_remember("Old title", url)
    deletions = manager(ha, configured, ["Old title"])
    monkeypatch.setattr(deletions, "_kick", lambda: None)
    image_id = deletions.library.image_id(original.name)
    plan = await deletions.async_plan([image_id])
    operation = await deletions.async_delete(plan["token"], True, True)
    op_id = operation["id"]
    await deletions._save_item(op_id, image_id, state="failed", jobs={"old title": "test-job"}, job_ids=["test-job"])
    retry = await deletions.async_retry_plan(op_id)
    stem = generation_stem("Old title", "test-job")
    master = picture(ha, stem + ".png")
    art = Image.new("RGBA", (20, 20))
    ImageDraw.Draw(art).rectangle((3, 3, 15, 15), fill="red")
    art.save(master)
    for suffix in ("_thumb.webp", "_qa.webp"):
        picture(ha, stem + suffix)
    with pytest.raises(cache_module.MappingConflict, match="candidate files changed"):
        await deletions.async_retry(op_id, retry["token"], True)
    retry = await deletions.async_retry_plan(op_id)
    assert retry["replacement_requests"] == 0
    await deletions.async_retry(op_id, retry["token"], True)
    master.write_bytes(b"Changed after authorization")
    await deletions._run()
    assert deletions.operations()[0]["state"] == "failed"
    assert "Persisted replacement changed" in deletions.operations()[0]["items"][0]["error"]
    assert original.exists() and cache.exact("Old title") == url and not ha.services.calls
    await deletions.async_shutdown()


@pytest.mark.asyncio
async def test_deletion_retry_rejects_changed_original_associations(ha, configured, monkeypatch):
    cache, _ = configured
    original = picture(ha, "old.png")
    url = "/local/activity_images/old.png"
    await cache.async_remember("Old title", url)
    deletions = manager(ha, configured, ["Old title"])
    monkeypatch.setattr(deletions, "_kick", lambda: None)
    image_id = deletions.library.image_id(original.name)
    plan = await deletions.async_plan([image_id])
    operation = await deletions.async_delete(plan["token"], True, True)
    op_id = operation["id"]
    await deletions._save_item(op_id, image_id, state="failed")
    # Model a restored external Store with different mappings; a normal write
    # through the cache would be rejected by the deletion reservation.
    cache._entries["old title"] = {**cache._entries["old title"], "image_url": "/local/activity_images/new.png"}
    retry = await deletions.async_retry_plan(op_id)
    assert "Original associations changed" in retry["items"][0]["blocked_reason"]
    with pytest.raises(cache_module.MappingConflict, match="Retry is blocked"):
        await deletions.async_retry(op_id, retry["token"], True)
    with pytest.raises(cache_module.MappingConflict, match="Retry plan is stale"):
        await deletions.async_retry(op_id, "unknown", True)
    assert original.exists() and not ha.services.calls
    await deletions.async_shutdown()


@pytest.mark.asyncio
async def test_legacy_background_review_cannot_be_recovered_as_valid_replacement(ha, configured, monkeypatch):
    """An upgrade must not authorize old uncertain artwork as a replacement."""
    from custom_components.activity_images.engine import generation_stem

    cache, _ = configured
    original = picture(ha, "old.png")
    url = "/local/activity_images/old.png"
    await cache.async_remember("Old title", url)
    deletions = manager(ha, configured, ["Old title"])
    monkeypatch.setattr(deletions, "_kick", lambda: None)
    image_id = deletions.library.image_id(original.name)
    plan = await deletions.async_plan([image_id])
    operation = await deletions.async_delete(plan["token"], True, True)
    op_id = operation["id"]
    stem = generation_stem("Old title", "legacy-job")
    for suffix in (".png", "_thumb.webp", "_qa.webp"):
        picture(ha, stem + suffix)
    candidate = "/local/activity_images/" + stem + ".png"
    await deletions._save_item(op_id, image_id, state="running",
                              jobs={"old title": "legacy-job"}, job_ids=["legacy-job"])
    await cache.async_record_candidate(candidate, {"origin": "generated", "background_status": "review_needed_unsafe_mask"},
                                       {"operation_id": op_id, "image_id": image_id, "key": "old title"})
    await deletions._save_item(op_id, image_id, state="failed")
    item = deletions._item(op_id, image_id)
    assert await deletions._invalid_candidates(item) == {"old title"}
    retry = await deletions.async_retry_plan(op_id)
    assert retry["replacement_requests"] == 1
    # The same legacy image must not be recovered from its files after losing
    # the candidate journal; the recorded background status still forbids it.
    assert await deletions._recoverable({**item, "candidates": {}}) == {}
    assert original.exists() and cache.exact("Old title") == url
    assert (ha.base / "www/activity_images" / (stem + ".png")).exists()
    assert not ha.services.calls
    await deletions.async_shutdown()

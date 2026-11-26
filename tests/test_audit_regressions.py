"""Regression tests for the November 2026 audit; no real provider calls."""
from io import BytesIO

import pytest
from PIL import Image

import test_behavior as behavior

ha = behavior.ha
configured = behavior.configured
generated = behavior.generated_module


def test_faint_alpha_does_not_skip_background_removal():
    image = Image.new("RGBA", (32, 32), (200, 200, 200, 255))
    image.putpixel((0, 0), (200, 200, 200, 249))
    data = BytesIO()
    image.save(data, format="PNG")
    assert not generated._already_transparent(data.getvalue())
    assert generated.normalize_generated_image(image).background_status != "existing_alpha"


def test_extreme_aspect_rejected_before_resize(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Unexpected large resize")
    monkeypatch.setattr(Image.Image, "resize", forbidden)
    with pytest.raises(ValueError, match="dimensions"):
        generated.normalize_icon_canvas(Image.new("RGBA", (256, 1), "red"))


@pytest.mark.asyncio
async def test_calendar_two_requests_same_tick(ha, configured, monkeypatch):
    adapter, _, _ = behavior._calendar_test_setup(
        ha, configured, monkeypatch, {"calendar.school": {"events": []}}
    )
    try:
        adapter._schedule_scan()
        adapter._schedule_scan()
        await adapter._scan_task
        assert adapter.last_error is None
    finally:
        await adapter.async_shutdown()


@pytest.mark.asyncio
async def test_preserve_fuzzy_matching_across_course_levels(ha, configured):
    cache, _ = configured
    path = ha.base / "www/activity_images/course.png"
    path.parent.mkdir(parents=True)
    Image.new("RGBA", (5, 5)).save(path)
    await cache.async_remember("Kurs niemieckiego poziom A1", "/local/activity_images/course.png")
    assert await cache.async_find("Kurs niemieckiego poziom A2") == "/local/activity_images/course.png"


@pytest.mark.asyncio
async def test_public_generation_failure_blocks_unattended_repeat(ha):
    integration = behavior._load_integration_entrypoint()
    await integration.async_setup(ha, {})
    entry = behavior.MockConfigEntry()
    await integration.async_setup_entry(ha, entry)
    try:
        ha.services.fail = True
        with pytest.raises(RuntimeError):
            await ha.services.async_call("activity_images", "generate_image", {"title": "Audit failure"})
        assert len(ha.services.calls) == 1
        ha.services.fail = False
        with pytest.raises(ValueError, match="explicit retry"):
            await ha.services.async_call("activity_images", "generate_image", {"title": "Audit failure"})
        assert len(ha.services.calls) == 1
        result = await ha.services.async_call("activity_images", "generate_image", {"title": "Audit failure", "force": True})
        assert not result["reused"]
        assert len(ha.services.calls) == 2
    finally:
        await integration.async_unload_entry(ha, entry)


@pytest.mark.asyncio
async def test_before_dispatch_write_failure_does_not_leave_stranded_title(ha, configured, monkeypatch):
    _, engine = configured
    queue = behavior.queue_module.ActivityImageQueue(ha, engine)
    await queue.async_setup()
    monkeypatch.setattr(queue, "_kick", lambda: None)
    await queue.async_enqueue(["Audit storage failure"])
    async def fail():
        raise OSError("Synthetic storage failure")
    monkeypatch.setattr(queue, "_save", fail)
    with pytest.raises(OSError):
        await queue._run()
    assert not queue._pending
    assert "audit storage failure" in queue._retry_blocked
    assert not ha.services.calls


@pytest.mark.asyncio
async def test_provider_url_is_never_fetched(ha, configured, monkeypatch):
    _, engine = configured
    ha.services.response = {"url": "http://127.0.0.1/private"}
    def forbidden(*args, **kwargs):
        pytest.fail("Provider-controlled HTTP URL must not be fetched")
    monkeypatch.setattr(generated, "async_get_clientsession", forbidden)
    with pytest.raises(ValueError, match="local media-source image ID"):
        await engine.async_generate("Untrusted URL")


def test_local_media_read_limit(ha, monkeypatch):
    path = ha.base / "oversized.bin"
    path.write_bytes(b"0123456789")
    monkeypatch.setattr(generated, "MAX_IMAGE_BYTES", 8)
    with pytest.raises(ValueError, match="regular file"):
        generated._read_local_source(path)


def test_local_media_symlink_is_rejected(ha):
    source = ha.base / "source.png"
    source.write_bytes(b"not an image")
    link = ha.base / "link.png"
    link.symlink_to(source)
    with pytest.raises(OSError):
        generated._read_local_source(link)


@pytest.mark.asyncio
async def test_resolve_only_retry_cannot_authorize_generation(ha, configured, monkeypatch):
    import importlib
    adapter, queue, _ = behavior._calendar_test_setup(
        ha, configured, monkeypatch, {"calendar.school": {"events": [{"summary": "Retry-only event"}]}}, enabled=True
    )
    module = importlib.import_module(type(adapter).__module__)
    callbacks = []
    monkeypatch.setattr(module, "async_call_later", lambda _hass, _delay, callback: (callbacks.append(callback) or (lambda: None)))
    try:
        ha._states["calendar.school"].state = "unavailable"
        await adapter.async_refresh_coverage()
        assert callbacks and not queue._pending
        ha._states["calendar.school"].state = "off"
        callbacks[0](None)
        await adapter._scan_task
        assert not queue._pending
        assert not ha.services.calls
    finally:
        await adapter.async_shutdown()


@pytest.mark.asyncio
async def test_changing_provider_for_authorized_calendar_needs_confirmation():
    from test_settings_api import Entries, api, const
    from types import SimpleNamespace
    entries = Entries()
    entries.entry.options = {const.CONF_CALENDARS: ["calendar.school"],
                             const.CONF_AUTO_CALENDARS: ["calendar.school"]}
    hass = SimpleNamespace(config_entries=entries, registry={})
    before = api.read_settings(hass)
    changes = {const.CONF_AI_ENTITY: "ai_task.other"}
    with pytest.raises(ValueError, match="paid confirmation"):
        await api.save_settings(hass, changes, before["revision"])
    assert not entries.updates
    assert (await api.save_settings(hass, changes, before["revision"], confirm_auto=True))["changed"]


@pytest.mark.asyncio
async def test_pending_queue_is_bounded(ha, configured, monkeypatch):
    _, engine = configured
    queue = behavior.queue_module.ActivityImageQueue(ha, engine)
    monkeypatch.setattr(behavior.queue_module, "MAX_PENDING_JOBS", 2)
    monkeypatch.setattr(queue, "_kick", lambda: None)
    await queue.async_setup()
    assert len(await queue.async_enqueue(["Alpha", "Bravo", "Charlie"])) == 2
    with pytest.raises(ValueError, match="queue is full"):
        await queue.async_candidate("Delta")


def test_processing_error_does_not_publish_partial_set(ha, monkeypatch):
    data = BytesIO()
    Image.new("RGBA", (10, 10), "red").save(data, format="PNG")
    target = ha.base / "www/activity_images/partial.png"
    original = generated._atomic_save
    def fail_second(image, destination, format, **kwargs):
        if format == "WEBP":
            raise OSError("Synthetic encode failure")
        original(image, destination, format, **kwargs)
    monkeypatch.setattr(generated, "_atomic_save", fail_second)
    with pytest.raises(OSError):
        generated._process(data.getvalue(), target)
    assert list(target.parent.iterdir()) == []


@pytest.mark.asyncio
async def test_deletion_plans_are_bounded_and_expire(ha, configured, monkeypatch):
    import importlib
    from test_library_deletion import manager
    deletions = manager(ha, configured)
    module = importlib.import_module(type(deletions).__module__)
    now = 100.0
    monkeypatch.setattr(module.time, "monotonic", lambda: now)
    for index in range(70):
        deletions._remember_plan(str(index), {"retry": "test"})
    assert len(deletions._plans) == 64
    assert deletions._get_plan("0") is None
    assert deletions._get_plan("69") is not None
    now += 601
    assert deletions._get_plan("69") is None


@pytest.mark.asyncio
async def test_direct_failure_barrier_survives_reload(ha):
    integration = behavior._load_integration_entrypoint()
    await integration.async_setup(ha, {})
    entry = behavior.MockConfigEntry()
    await integration.async_setup_entry(ha, entry)
    ha.services.fail = True
    with pytest.raises(RuntimeError):
        await ha.services.async_call("activity_images", "generate_image", {"title": "Persisted failure"})
    await integration.async_unload_entry(ha, entry)
    await integration.async_setup_entry(ha, entry)
    try:
        ha.services.fail = False
        with pytest.raises(ValueError, match="explicit retry"):
            await ha.services.async_call("activity_images", "generate_image", {"title": "Persisted failure"})
        assert len(ha.services.calls) == 1
    finally:
        await integration.async_unload_entry(ha, entry)


@pytest.mark.asyncio
async def test_direct_unload_stops_inflight_without_replay(ha):
    import asyncio
    integration = behavior._load_integration_entrypoint()
    await integration.async_setup(ha, {})
    entry = behavior.MockConfigEntry()
    await integration.async_setup_entry(ha, entry)
    started = asyncio.Event()
    async def blocked(call):
        started.set()
        await asyncio.Event().wait()
    ha.services.async_register("ai_task", "generate_image", blocked)
    request = asyncio.create_task(entry.runtime_data.queue.async_generate_direct("In-flight direct"))
    await asyncio.wait_for(started.wait(), 2)
    await asyncio.wait_for(integration.async_unload_entry(ha, entry), 2)
    with pytest.raises(asyncio.CancelledError):
        await request
    assert "in flight direct" in ha.saved["activity_images_jobs"]["retry_blocked"]


def test_lucida_fully_transparent_result_is_rejected():
    data = BytesIO()
    Image.new("RGBA", (16, 16)).save(data, format="PNG")
    with pytest.raises(ValueError, match="visible transparent subject"):
        generated._validate_lucida(data.getvalue())


@pytest.mark.asyncio
async def test_cache_rejects_symlink_and_preserves_real_alias(ha, configured):
    cache, _ = configured
    folder = ha.base / "www/activity_images"
    folder.mkdir(parents=True)
    original = folder / "real.png"
    Image.new("RGBA", (5, 5)).save(original)
    (folder / "unsafe.png").symlink_to(original)
    await cache.async_remember("Unsafe alias", "/local/activity_images/unsafe.png")
    assert await cache.async_find("Unsafe alias", similar=False) is None
    assert original.exists()


@pytest.mark.asyncio
async def test_encoded_asset_identity_preserves_exact_mapping(ha, configured):
    cache, _ = configured
    folder = ha.base / "www/activity_images"
    folder.mkdir(parents=True)
    Image.new("RGBA", (5, 5)).save(folder / "real name.png")
    await cache.async_remember("Space name", "/local/activity_images/real name.png")
    await cache.async_link("Space name", "/local/activity_images/real%20name.png")
    assert await cache.async_find("Space name") == "/local/activity_images/real%20name.png"
    assert cache.references_url("/local/activity_images/real name.png")


@pytest.mark.asyncio
async def test_legacy_cache_setup_never_rewrites_master(ha):
    root = ha.base / "www/activity_images"
    root.mkdir(parents=True)
    path = root / "legacy.png"
    Image.new("RGBA", (20, 15), "red").save(path)
    before = path.read_bytes()
    ha.saved["activity_images_image_cache"] = {"canvas_normalization_version": 0,
        "entries": {"legacy": {"title": "Legacy", "image_url": "/local/activity_images/legacy.png"}}}
    cache = behavior.cache_module.ActivityImageCache(ha)
    await cache.async_setup()
    assert path.read_bytes() == before
    assert cache.exact("Legacy") == "/local/activity_images/legacy.png"
    assert list(root.iterdir()) == [path]


def test_publish_collision_preserves_original_and_rolls_back_variants(ha):
    root = ha.base / "www/activity_images"
    root.mkdir(parents=True)
    target = root / "existing.png"
    Image.new("RGBA", (20, 20), "blue").save(target)
    before = target.read_bytes()
    data = BytesIO()
    Image.new("RGBA", (20, 20), "red").save(data, format="PNG")
    with pytest.raises(FileExistsError):
        generated._process(data.getvalue(), target)
    assert target.read_bytes() == before
    assert list(root.iterdir()) == [target]


@pytest.mark.asyncio
async def test_link_service_requires_replace_confirmation(ha):
    integration = behavior._load_integration_entrypoint()
    await integration.async_setup(ha, {})
    entry = behavior.MockConfigEntry()
    await integration.async_setup_entry(ha, entry)
    try:
        root = ha.base / "www/activity_images"
        root.mkdir(parents=True)
        for name in ("one.png", "two.png"):
            Image.new("RGBA", (10, 10), "red").save(root / name)
        first, second = "/local/activity_images/one.png", "/local/activity_images/two.png"
        await ha.services.async_call("activity_images", "link_existing_image", {"title": "Link", "image_url": first})
        with pytest.raises(Exception, match="confirm replacement"):
            await ha.services.async_call("activity_images", "link_existing_image", {"title": "Link", "image_url": second})
        assert entry.runtime_data.cache.exact("Link") == first
        await ha.services.async_call("activity_images", "link_existing_image", {"title": "Link", "image_url": second, "replace": True})
        assert entry.runtime_data.cache.exact("Link") == second
        assert len(list(root.iterdir())) == 2
    finally:
        await integration.async_unload_entry(ha, entry)


@pytest.mark.asyncio
async def test_native_diagnostics_redacts_sensitive_configuration(ha):
    import json
    import importlib
    import test_settings_diagnostics  # noqa: F401
    from types import SimpleNamespace
    diagnostics = importlib.import_module("custom_components.activity_images.diagnostics")
    entry = SimpleNamespace(entry_id="entry", data={}, options={
        behavior.const_module.CONF_AI_ENTITY: "ai_task.private_provider",
        behavior.const_module.CONF_LUCIDA_URL: "http://private.example/secret",
        behavior.const_module.CONF_REFERENCE_MEDIA_ID: "media-source://private/reference.png",
        behavior.const_module.CONF_PROMPT_TEMPLATE: "Private prompt {title}",
        behavior.const_module.CONF_CALENDARS: ["calendar.private"],
    })
    result = await diagnostics.async_get_config_entry_diagnostics(ha, entry)
    assert result["provider"]["status"] == "unavailable"
    assert "private" not in json.dumps(result).lower()
    assert not ha.services.calls


@pytest.mark.asyncio
async def test_recovered_paid_jobs_do_not_dispatch_before_setup_finishes(ha, configured, monkeypatch):
    import asyncio
    _, engine = configured
    queue = behavior.queue_module.ActivityImageQueue(ha, engine)
    monkeypatch.setattr(queue, "_kick", lambda: None)
    await queue.async_setup()
    await queue.async_enqueue(["Queued before restart"])
    restored = behavior.queue_module.ActivityImageQueue(ha, engine)
    await restored.async_setup(start_worker=False)
    await asyncio.sleep(0)
    assert not ha.services.calls
    with pytest.raises(ValueError, match="not ready"):
        await restored.async_generate_direct("Direct before setup")
    restored.start()
    await restored._worker
    assert len(ha.services.calls) == 1
    await restored.async_shutdown()


@pytest.mark.asyncio
async def test_setup_failure_unloads_runtime_without_paid_dispatch(ha, monkeypatch):
    integration = behavior._load_integration_entrypoint()
    await integration.async_setup(ha, {})
    entry = behavior.MockConfigEntry()
    async def fail(_self):
        raise RuntimeError("Synthetic startup failure")
    monkeypatch.setattr(integration.ActivityCalendarAdapter, "async_start", fail)
    with pytest.raises(RuntimeError, match="startup failure"):
        await integration.async_setup_entry(ha, entry)
    assert entry.runtime_data is None
    assert not ha.data.get("activity_images")
    assert ("unload", ("sensor", "ai_task")) in ha.forwarded
    assert not ha.services.calls


@pytest.mark.asyncio
async def test_background_review_is_retained_without_automatic_assignment(ha, configured, monkeypatch):
    """Opaque provider output must become a manual candidate, not a paid replay."""
    import asyncio
    cache, engine = configured
    path = ha.base / "opaque.png"
    Image.new("RGB", (24, 24), "red").save(path)
    ha.medias["media-source://generated/opaque"] = path
    ha.services.response = {"media_source_id": "media-source://generated/opaque"}
    queue = behavior.queue_module.ActivityImageQueue(ha, engine)
    await queue.async_setup()
    assert await queue.async_enqueue(["Review activity"]) == ["Review activity"]
    await asyncio.wait_for(queue._worker, 3)
    job = queue._jobs[-1]
    assert job["state"] == "review" and job["kind"] == "manual"
    assert cache.exact("Review activity") is None
    assert cache.image_metadata(job["result_url"])["background_status"].startswith("review_needed")
    assert await queue.async_enqueue(["Review activity"]) == []
    with pytest.raises(ValueError, match="outstanding"):
        await queue.async_generate_direct("Review activity")
    assert len(ha.services.calls) == 1
    await queue.async_shutdown()
    restarted = behavior.queue_module.ActivityImageQueue(ha, engine)
    await restarted.async_setup()
    assert restarted._job(job["id"])["state"] == "review"
    await restarted.async_accept(job["id"])
    assert cache.exact("Review activity") == job["result_url"]
    assert len(ha.services.calls) == 1
    await restarted.async_shutdown()


@pytest.mark.asyncio
async def test_public_opaque_result_requires_review_preserves_old_mapping(ha):
    integration = behavior._load_integration_entrypoint()
    await integration.async_setup(ha, {})
    entry = behavior.MockConfigEntry()
    await integration.async_setup_entry(ha, entry)
    try:
        runtime = entry.runtime_data
        folder = ha.base / "www/activity_images"
        folder.mkdir(parents=True)
        Image.new("RGB", (10, 10), "blue").save(folder / "old.png")
        old = "/local/activity_images/old.png"
        await runtime.cache.async_remember("Activity", old)
        opaque = ha.base / "opaque.png"
        Image.new("RGB", (20, 20), "red").save(opaque)
        ha.medias["media-source://generated/opaque"] = opaque
        ha.services.response = {"media_source_id": "media-source://generated/opaque"}
        with pytest.raises(behavior.sys.modules["homeassistant.exceptions"].HomeAssistantError, match="manual background review"):
            await ha.services.async_call("activity_images", "generate_image", {"title": "Activity", "force": True})
        assert runtime.cache.exact("Activity") == old
        assert runtime.queue._jobs[-1]["state"] == "review"
        result = await ha.services.async_call("activity_images", "generate_image", {"title": "Activity"})
        assert result["image_url"] == old and len(ha.services.calls) == 1
    finally:
        await integration.async_unload_entry(ha, entry)


@pytest.mark.asyncio
async def test_deletion_revalidates_demand_before_rate_limit_retry(ha, configured, monkeypatch):
    from test_library_deletion import manager, select, settled
    from test_library import picture
    import importlib
    cache, engine = configured
    original = picture(ha, "original.png")
    url = "/local/activity_images/original.png"
    await cache.async_remember("Activity", url)
    deletions = manager(ha, configured, ["Activity"])
    calls = []
    class RateLimited(Exception):
        status = 429
    async def provider(*args, **kwargs):
        calls.append(1)
        deletions.calendar.calendar_titles["calendar.test"] = []
        raise RateLimited()
    monkeypatch.setattr(ha.services, "async_call", provider)
    retry = importlib.import_module("custom_components.activity_images.retry")
    original_sleep = retry.asyncio.sleep
    async def short_sleep(seconds):
        await original_sleep(0 if seconds >= 2 else seconds)
    monkeypatch.setattr(retry.asyncio, "sleep", short_sleep)
    _, op = await select(deletions, ["original.png"], regenerate=True)
    await settled(deletions)
    assert calls == [1]
    assert original.exists() and cache.exact("Activity") == url
    assert deletions._operation(op["id"])["state"] == "failed"


@pytest.mark.asyncio
async def test_replacement_review_cannot_delete_original(ha, configured):
    from test_library_deletion import manager, select, settled
    from test_library import picture
    cache, _ = configured
    original = picture(ha, "original.png")
    url = "/local/activity_images/original.png"
    await cache.async_remember("Activity", url)
    path = ha.base / "opaque.png"
    Image.new("RGB", (20, 20), "red").save(path)
    ha.medias["media-source://generated/opaque"] = path
    ha.services.response = {"media_source_id": "media-source://generated/opaque"}
    deletions = manager(ha, configured, ["Activity"])
    _, op = await select(deletions, ["original.png"], regenerate=True)
    await settled(deletions)
    current = deletions._operation(op["id"])
    assert current["state"] == "failed"
    candidate = current["items"][0]["candidates"]["activity"]
    assert cache.image_metadata(candidate)["background_status"].startswith("review_needed")
    assert original.exists() and cache.exact("Activity") == url
    retry = await deletions.async_retry_plan(op["id"])
    assert retry["replacement_requests"] == 1
    assert len(ha.services.calls) == 1
    await deletions.async_abandon(op["id"], True)
    assert not cache.reserved("Activity")
    await cache.async_link("Activity", candidate, replace=True)
    assert cache.exact("Activity") == candidate
    assert original.exists() and len(ha.services.calls) == 1


@pytest.mark.asyncio
async def test_live_deletion_demand_is_read_only_and_counts_ignored_titles(ha, configured, monkeypatch):
    events = {"calendar.school": {"events": [{"summary": " Ignore this "},
              {"summary": "Cancelled", "status": "cancelled"}, {"summary": "Activity"}]}}
    adapter, queue, _ = behavior._calendar_test_setup(ha, configured, monkeypatch, events, enabled=True)
    try:
        adapter.ignored_titles = frozenset({" Ignore this "})
        before = dict(adapter.calendar_titles)
        assert await adapter.async_read_live_demand() == ["Activity", "Ignore this"]
        assert adapter.calendar_titles == before and not queue._pending and not ha.services.calls
        events["calendar.school"]["events"].append({"summary": "X" * 256})
        with pytest.raises(ValueError, match="Invalid calendar event summary"):
            await adapter.async_read_live_demand()
    finally:
        await adapter.async_shutdown()


@pytest.mark.asyncio
async def test_terminal_history_compaction_preserves_tokens_barriers_and_active_evidence(ha, configured, monkeypatch):
    cache, _ = configured
    monkeypatch.setattr(behavior.cache_module, "DETAILED_DELETION_HISTORY", 1)
    def operation(index, state="done"):
        return {"id": str(index), "token": f"token-{index}", "state": state,
                "replacement_requests": 0, "retry_tokens": [f"retry-{index}"], "demand": ["Sensitive title"],
                "items": [{"id": f"image-{index}", "filename": f"{index}.png", "state": state, "error": None,
                           "needed_titles": [], "job_ids": [], "identity": {"huge": "x" * 5000},
                           "urls": [f"/local/activity_images/{index}.png"], "reserved_titles": [f"active {index}"]}]}
    cache._tombstones.add("/local/activity_images/0.png")
    await cache.async_journal(operation(0))
    await cache.async_journal(operation(1))
    await cache.async_journal(operation(2, "failed"))
    archived = cache._deletions["0"]
    assert archived["archived"] and archived["token"] == "token-0"
    assert archived["retry_tokens"] == ["retry-0"]
    assert "identity" not in archived["items"][0] and "demand" not in archived
    assert "identity" in cache._deletions["1"]["items"][0]
    assert cache.reserved("Active 2") and cache.is_deleted("/local/activity_images/0.png")
    fresh = behavior.cache_module.ActivityImageCache(ha)
    await fresh.async_setup()
    assert fresh._deletions == cache._deletions and fresh._tombstones == cache._tombstones
    from test_library_deletion import manager
    deletions = manager(ha, (fresh, configured[1]))
    assert all(operation["id"] != "0" for operation in deletions.operations())
    assert (await deletions.async_delete("token-0", False, True))["id"] == "0"
    assert (await deletions.async_retry("0", "retry-0", True))["archived"]
    assert not ha.services.calls


@pytest.mark.asyncio
async def test_metadata_and_mapping_commit_once_and_rollback_together(ha, configured, monkeypatch):
    cache, engine = configured
    original = cache._async_save
    writes = []
    async def count():
        writes.append(1)
        await original()
    monkeypatch.setattr(cache, "_async_save", count)
    result = await engine.async_generate("One store commit")
    assert len(writes) == 1
    assert cache.exact("One store commit") == result["image_url"]
    assert cache.image_metadata(result["image_url"])["origin"] == "generated"
    before = cache.entries()
    metadata = cache._metadata.copy()
    async def fail():
        raise OSError("Synthetic disk error")
    monkeypatch.setattr(cache, "_async_save", fail)
    with pytest.raises(OSError):
        await cache.async_record_image("/local/activity_images/new.png", {"origin": "generated"}, title="One store commit")
    assert cache.entries() == before and cache._metadata == metadata


@pytest.mark.asyncio
async def test_reading_shared_dimensions_only_opens_one_file(ha, configured, monkeypatch):
    cache, _ = configured
    path = ha.base / "www/activity_images/one.png"
    path.parent.mkdir(parents=True)
    Image.new("RGBA", (12, 15), "red").save(path)
    count = []
    original = ha.async_add_executor_job
    async def counted(fn, *args):
        count.append(fn)
        return await original(fn, *args)
    monkeypatch.setattr(ha, "async_add_executor_job", counted)
    images = {f"Alias {i}": "/local/activity_images/one.png?v=1" for i in range(64)}
    result = await cache.async_image_metadata(images)
    assert len(count) == 1 and len(result) == 64
    assert result["Alias 0"] == {"width": 12, "height": 15}



@pytest.mark.asyncio
async def test_home_tasks_review_result_not_returned_as_image(ha):
    from types import SimpleNamespace
    wrapper, ai = behavior._load_ai_wrapper()
    integration = behavior._load_integration_entrypoint()
    await integration.async_setup(ha, {})
    entry = behavior.MockConfigEntry()
    await integration.async_setup_entry(ha, entry)
    try:
        path = ha.base / "opaque.png"
        Image.new("RGB", (20, 20), "red").save(path)
        ha.medias["media-source://generated/opaque"] = path
        ha.services.response = {"media_source_id": "media-source://generated/opaque"}
        entity = wrapper.ActivityImagesAITaskEntity(entry)
        entity.hass = ha
        entity._attr_entity_id = "ai_task.activity_images"
        task = ai.GenImageTask(name="home_tasks_review", instructions="Review output\n\nThe image must have a square 1:1 aspect ratio.")
        chat = SimpleNamespace(conversation_id="test")
        with pytest.raises(wrapper.HomeAssistantError, match="manual background review"):
            await entity._async_generate_image(task, chat)
        runtime = entry.runtime_data
        job = runtime.queue._jobs[-1]
        assert job["state"] == "review" and runtime.cache.exact("Review output") is None
        await runtime.queue.async_accept(job["id"])
        assert (await entity._async_generate_image(task, chat)).image_data
        assert len(ha.services.calls) == 1
    finally:
        await integration.async_unload_entry(ha, entry)


@pytest.mark.asyncio
async def test_matching_decisions_equal_reference_v060(ha, configured):
    """Compare independent original scoring, including exact matches and ties."""
    from difflib import SequenceMatcher
    cache, _ = configured
    normalize = behavior.cache_module.normalize_title
    names = [
        "Kurs niemieckiego poziom A1", "Kurs niemieckiego poziom B2",
        "Sprzątanie pokoju", "Sprzątanie pokoi", "Ćwiczenia równowagi",
        "Green Waste Collection", "Grey Waste Collection", "German language class",
    ] + [f"Activity number {i:02d}" for i in range(40)]
    for i, name in enumerate(names):
        await cache.async_remember(name, f"https://example.invalid/{i}.png")
    entries = cache.entries()
    def reference(title):
        key = normalize(title)
        if key in entries:
            return entries[key]["image_url"]
        tokens = {token for token in key.split() if len(token) >= 4}
        best, score = None, 0.85
        for candidate in sorted(entries):
            others = {token for token in candidate.split() if len(token) >= 4}
            if not tokens or not others or not (tokens & others):
                continue
            if len(tokens & others) / min(len(tokens), len(others)) < 0.50:
                continue
            similarity = SequenceMatcher(None, key, candidate).ratio()
            if similarity >= score:
                best, score = candidate, similarity
        return entries[best]["image_url"] if best else None
    queries = names + [
        "Kurs niemieckiego poziom A2", "SPRZĄTANIE POKOJU!", "Green waste collections",
        "Ćwiczenie równowagi", "Unrelated", "abc", "Activity numbere 13",
    ] + [f"Activity numbere {i:02d}" for i in range(40)]
    for query in queries:
        assert await cache.async_find(query, touch=False, prune=False) == reference(query), query

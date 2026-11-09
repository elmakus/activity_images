"""Synthetic permanent-deletion safety and recovery regressions (no paid calls)."""
from __future__ import annotations

import asyncio
import json
from copy import deepcopy
from types import SimpleNamespace

import pytest
import test_behavior as behavior
from test_library import picture

from custom_components.activity_images import deletion_files
from custom_components.activity_images.image_cache import (
    MappingConflict,
    normalize_title,
)
from custom_components.activity_images.library import ActivityImageLibrary
from custom_components.activity_images.library_deletion import ActivityImageDeletions

ha = behavior.ha
configured = behavior.configured


def manager(ha, configured, titles=()):
    cache, engine = configured
    queue = behavior.queue_module.ActivityImageQueue(ha, engine)
    calendar = SimpleNamespace(_lock=asyncio.Lock(), calendar_titles={"calendar.test": list(titles)},
                               calendars=(), last_scan="now", last_error=None)
    async def reconcile():
        calendar.images = {title: await cache.async_find(title, touch=False) for title in titles}
    calendar.async_reconcile = reconcile
    return ActivityImageDeletions(ha, ActivityImageLibrary(ha, cache), queue, calendar)


async def settled(deletions):
    for _ in range(1000):
        task = deletions._task
        if task and task.done():
            await task
            return
        await asyncio.sleep(0.005)
    raise AssertionError("Deletion operation did not settle")


async def select(deletions, names, regenerate=False):
    plan = await deletions.async_plan([deletions.library.image_id(name) for name in names])
    return plan, await deletions.async_delete(plan["token"], regenerate, True)


@pytest.mark.asyncio
async def test_plan_all_variants_exact_aliases_fuzzy_consumers_no_mutation(ha, configured):
    cache, _ = configured
    for name in ("old.png", "old_thumb.webp", "old_qa.webp"):
        picture(ha, name)
    await cache.async_remember("Clean the room", "/local/activity_images/old.png?v=1")
    await cache.async_remember("Historical", "/local/activity_images/old_thumb.webp")
    await cache.async_remember("QA alias", "/local/activity_images/old_qa.webp?v=2")
    deletions = manager(ha, configured, ["Clean the room!", "clean the room", "Clean a room"])
    before = deepcopy(ha.saved)
    plan = await deletions.async_plan([deletions.library.image_id("old.png")])
    assert ha.saved == before and ha.services.calls == []
    item = plan["items"][0]
    assert item["files"] == ["old.png", "old_qa.webp", "old_thumb.webp"]
    assert item["titles"] == ["Clean the room", "Historical", "QA alias"]
    assert len({normalize_title(title) for title in item["needed_titles"]}) == len(item["needed_titles"])
    assert "Clean the room!" in item["needed_titles"] or "clean the room" in item["needed_titles"]
    assert plan["replacement_requests"] == len(item["needed_titles"])
    assert plan["max_provider_attempts"] == 3 * plan["replacement_requests"]
    assert "unknown" in plan["external_warning"] and not item["safe_without_regeneration"]
    with pytest.raises(ValueError, match="authorization"):
        await deletions.async_delete(plan["token"], False, True)


@pytest.mark.asyncio
async def test_plain_delete_tombstones_redacts_and_never_touches_home_tasks(ha, configured):
    cache, _ = configured
    for name in ("old.png", "old_thumb.webp", "old_qa.webp"):
        picture(ha, name)
    foreign = ha.base / "www/home_tasks/old.png"
    foreign.parent.mkdir(parents=True)
    foreign.write_bytes(b"Home Tasks copy")
    await cache.async_remember("Historical", "/local/activity_images/old_thumb.webp")
    deletions = manager(ha, configured)
    job = deletions.queue._new_job("Previous", "manual")
    job.update(state="accepted", result_url="/local/activity_images/old.png?v=1", original_url="/local/activity_images/old_qa.webp")
    deletions.queue._jobs.append(job)
    await deletions.queue._save()
    plan, operation = await select(deletions, ["old.png"])
    assert plan["items"][0]["safe_without_regeneration"]
    await settled(deletions)
    assert deletions.operations()[0]["state"] == "done"
    assert not list((ha.base / "www/activity_images").glob("old*"))
    assert foreign.read_bytes() == b"Home Tasks copy" and not cache.entries()
    assert cache.is_deleted("/local/activity_images/old_thumb.webp?v=10")
    saved_job = ha.saved[deletions.queue._store.key]["jobs"][0]
    assert saved_job["result_url"] is None and saved_job["result_deleted"] and saved_job["original_url"] is None
    assert (await deletions.async_delete(plan["token"], False, True))["id"] == operation["id"]
    picture(ha, "old.png", "blue")  # A stale public link must not resurrect a tombstoned name.
    for mutation in (cache.async_link("New", "/local/activity_images/old.png"),
                     cache.async_remember("New", "/local/activity_images/%6Fld.png?v=2")):
        with pytest.raises(MappingConflict):
            await mutation
    await cache.async_remember_many([{"title": "New", "image_url": "/local/activity_images/old.png"}])
    assert not cache.entries()
    assert (await deletions.library.async_list(refresh=True))["total"] == 0
    assert ha.services.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["file", "mapping", "demand", "variant"])
async def test_stale_plan_fails_without_mutation_or_provider(ha, configured, change):
    cache, _ = configured
    original = picture(ha, "old.png")
    deletions = manager(ha, configured)
    plan = await deletions.async_plan([deletions.library.image_id("old.png")])
    if change == "file":
        picture(ha, "old.png", "blue")
    elif change == "mapping":
        await cache.async_remember("New association", "/local/activity_images/old.png")
    elif change == "demand":
        deletions.calendar.calendar_titles["calendar.test"] = ["New event"]
    else:
        picture(ha, "old_thumb.webp")
    before = cache.entries()
    with pytest.raises(MappingConflict):
        await deletions.async_delete(plan["token"], True, True)
    assert original.exists() and cache.entries() == before and not cache._deletions and ha.services.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("ids", [[], ["../../private"], ["g" * 32], ["a" * 32] * 2, ["a" * 32] * 65])
async def test_validate_opaque_selection(ha, configured, ids):
    with pytest.raises(ValueError):
        await manager(ha, configured).async_plan(ids)


@pytest.mark.asyncio
async def test_style_reference_derivative_and_review_are_blocked(ha, configured):
    cache, engine = configured
    for name in ("activity-icons-style-board-v1.png", "reference.png", "candidate.png", "orphan_qa.webp", "orphan_thumb.webp"):
        picture(ha, name)
    engine.settings["reference_media_content_id"] = "media-source://media_source/local/reference.png"
    deletions = manager(ha, configured)
    job = deletions.queue._new_job("Review", "manual")
    job.update(state="review", result_url="/local/activity_images/candidate.png")
    deletions.queue._jobs.append(job)
    for name in ("activity-icons-style-board-v1.png", "reference.png", "candidate.png"):
        plan = await deletions.async_plan([deletions.library.image_id(name)])
        assert plan["items"][0]["blocked_reason"]
        with pytest.raises(MappingConflict):
            await deletions.async_delete(plan["token"], True, True)
    await cache.async_remember("Derivative alias", "/local/activity_images/orphan_thumb.webp")
    plan, _ = await select(deletions, ["orphan_qa.webp"])
    assert plan["items"][0]["files"] == ["orphan_qa.webp", "orphan_thumb.webp"]
    assert plan["items"][0]["titles"] == ["Derivative alias"]
    await settled(deletions)
    assert cache.exact("Derivative alias") is None


@pytest.mark.asyncio
async def test_corrupt_derivative_is_included_symlink_variant_blocks(ha, configured):
    picture(ha, "old.png")
    folder = ha.base / "www/activity_images"
    (folder / "old_thumb.webp").write_bytes(b"corrupt")
    deletions = manager(ha, configured)
    plan = await deletions.async_plan([deletions.library.image_id("old.png")])
    assert plan["items"][0]["files"] == ["old.png", "old_thumb.webp"]
    (folder / "old_qa.webp").symlink_to(folder / "old.png")
    plan = await deletions.async_plan([deletions.library.image_id("old.png")])
    assert plan["items"][0]["blocked_reason"]


@pytest.mark.asyncio
async def test_all_replacements_durable_before_old_is_committed(ha, configured, monkeypatch):
    cache, engine = configured
    old = picture(ha, "old.png")
    url = "/local/activity_images/old.png?v=1"
    for title in ("One activity", "Two activity", "Historical alias"):
        await cache.async_remember(title, url)
    deletions = manager(ha, configured, ["One activity", "Two activity"])
    original = engine.async_generate
    calls = []
    async def generate(title, **kwargs):
        calls.append(title)
        assert old.exists() and cache.exact("One activity") == url and cache.exact("Two activity") == url
        if title == "Two activity":
            raise RuntimeError("synthetic provider failure")
        return await original(title, **kwargs)
    monkeypatch.setattr(engine, "async_generate", generate)
    plan, operation = await select(deletions, ["old.png"], True)
    await settled(deletions)
    assert deletions.operations()[0]["state"] == "failed"
    assert old.exists() and all(cache.exact(title) == url for title in ("One activity", "Two activity", "Historical alias"))
    item = cache._deletions[operation["id"]]["items"][0]
    first = item["candidates"][normalize_title("One activity")]
    assert len(calls) == 2
    retry = await deletions.async_retry_plan(operation["id"])
    assert retry["replacement_requests"] == 1 and retry["max_provider_attempts"] == 3
    assert retry["items"][0]["needed_titles"] == ["Two activity"]
    monkeypatch.setattr(engine, "async_generate", original)
    resumed = await deletions.async_retry(operation["id"], retry["token"], True)
    assert (await deletions.async_retry(operation["id"], retry["token"], True))["id"] == resumed["id"]
    await settled(deletions)
    assert deletions.operations()[0]["state"] == "done" and not old.exists()
    assert cache.exact("One activity") == first and cache.exact("Two activity") != url
    assert cache.exact("Historical alias") is None and len(ha.services.calls) == 2
    assert len({job["id"] for job in deletions.queue._jobs}) == 3


@pytest.mark.asyncio
async def test_journal_failure_before_generation_retains_everything(ha, configured, monkeypatch):
    cache, _ = configured
    old = picture(ha, "old.png")
    await cache.async_remember("Activity", "/local/activity_images/old.png")
    deletions = manager(ha, configured, ["Activity"])
    plan = await deletions.async_plan([deletions.library.image_id("old.png")])
    async def failure(*args):
        raise OSError("disk failure")
    monkeypatch.setattr(cache._store, "async_save", failure)
    with pytest.raises(OSError):
        await deletions.async_delete(plan["token"], True, True)
    assert old.exists() and cache.exact("Activity") == "/local/activity_images/old.png"
    assert not cache._deletions and not ha.services.calls


@pytest.mark.asyncio
async def test_commit_failure_reuses_persisted_candidate_without_paid_retry(ha, configured, monkeypatch):
    cache, _ = configured
    old = picture(ha, "old.png")
    await cache.async_remember("Activity", "/local/activity_images/old.png")
    deletions = manager(ha, configured, ["Activity"])
    save = cache._store.async_save
    async def fail_commit(data):
        if data["tombstones"]:
            raise OSError("commit failed")
        await save(data)
    monkeypatch.setattr(cache._store, "async_save", fail_commit)
    _, operation = await select(deletions, ["old.png"], True)
    await settled(deletions)
    assert deletions.operations()[0]["state"] == "failed"
    assert old.exists() and cache.exact("Activity") == "/local/activity_images/old.png" and not cache._tombstones
    assert len(ha.services.calls) == 1
    monkeypatch.setattr(cache._store, "async_save", save)
    retry = await deletions.async_retry_plan(operation["id"])
    assert retry["replacement_requests"] == 0
    await deletions.async_retry(operation["id"], retry["token"], True)
    await settled(deletions)
    assert deletions.operations()[0]["state"] == "done" and len(ha.services.calls) == 1


@pytest.mark.asyncio
async def test_cleanup_failure_observable_zero_paid_retry_and_restart(ha, configured, monkeypatch):
    cache, engine = configured
    old = picture(ha, "old.png")
    await cache.async_remember("Historical", "/local/activity_images/old.png")
    deletions = manager(ha, configured)
    native = deletion_files.os.unlink
    def fail_unlink(*args, **kwargs):
        raise OSError("synthetic unlink failure")
    monkeypatch.setattr(deletion_files.os, "unlink", fail_unlink)
    _, operation = await select(deletions, ["old.png"])
    await settled(deletions)
    assert deletions.operations()[0]["state"] == "cleanup_failed"
    assert cache.exact("Historical") is None and not old.exists()
    assert list((ha.base / ".storage/activity_images_deletions").iterdir())
    retry = await deletions.async_retry_plan(operation["id"])
    assert retry["replacement_requests"] == 0
    monkeypatch.setattr(deletion_files.os, "unlink", native)
    await deletions.async_shutdown()
    fresh_cache = behavior.cache_module.ActivityImageCache(ha)
    await fresh_cache.async_setup()
    engine.cache = fresh_cache
    fresh = manager(ha, (fresh_cache, engine))
    await fresh.queue.async_setup()
    await fresh.async_setup()
    await settled(fresh)
    assert fresh.operations()[0]["state"] == "cleanup_failed"  # Explicit retry, not failed-cleanup GC.
    retry = await fresh.async_retry_plan(operation["id"])
    await fresh.async_retry(operation["id"], retry["token"], True)
    await settled(fresh)
    assert fresh.operations()[0]["state"] == "done" and not list((ha.base / ".storage/activity_images_deletions").iterdir())
    assert ha.services.calls == []


@pytest.mark.asyncio
async def test_restart_interrupted_replacements_never_replay_and_reservations_block(ha, configured, monkeypatch):
    cache, engine = configured
    old = picture(ha, "old.png")
    url = "/local/activity_images/old.png"
    await cache.async_remember("Activity", url)
    deletions = manager(ha, configured, ["Activity"])
    monkeypatch.setattr(deletions, "_kick", lambda: None)
    plan, operation = await select(deletions, ["old.png"], True)
    for action in (cache.async_link("New alias", url), cache.async_remember("New alias", url),
                   cache.async_unlink("Activity", url), cache.async_forget(title="Activity"),
                   engine.async_generate("Activity", force=True), deletions.queue.async_candidate("Activity")):
        with pytest.raises(ValueError):
            await action
    assert await deletions.queue.async_enqueue(["Activity"]) == []
    await cache.async_remember_many([{"title": "New alias", "image_url": url}])
    assert cache.exact("New alias") is None
    await deletions.async_shutdown()
    fresh_cache = behavior.cache_module.ActivityImageCache(ha)
    await fresh_cache.async_setup()
    engine.cache = fresh_cache
    fresh = manager(ha, (fresh_cache, engine), ["Activity"])
    await fresh.queue.async_setup()
    await fresh.async_setup()
    await settled(fresh)
    assert fresh.operations()[0]["state"] == "interrupted" and old.exists() and ha.services.calls == []
    assert (await fresh.async_delete(plan["token"], True, True))["id"] == operation["id"]
    await settled(fresh)
    assert ha.services.calls == []
    retry = await fresh.async_retry_plan(operation["id"])
    assert retry["replacement_requests"] == 1
    await fresh.async_retry(operation["id"], retry["token"], True)
    await settled(fresh)
    assert fresh.operations()[0]["state"] == "done" and len(ha.services.calls) == 1


@pytest.mark.asyncio
async def test_bulk_item_failure_does_not_prevent_other_image_completion(ha, configured, monkeypatch):
    cache, _ = configured
    one, two = picture(ha, "one.png"), picture(ha, "two.png")
    await cache.async_remember("One", "/local/activity_images/one.png")
    await cache.async_remember("Two", "/local/activity_images/two.png")
    deletions = manager(ha, configured)
    native = deletion_files.cleanup
    import custom_components.activity_images.library_deletion as module
    def selective(root, quarantine_root, expected, operation_id, image_id):
        if "one.png" in expected["files"]:
            raise OSError("one cleanup failed")
        return native(root, quarantine_root, expected, operation_id, image_id)
    monkeypatch.setattr(module, "cleanup", selective)
    await select(deletions, ["one.png", "two.png"])
    await settled(deletions)
    assert one.exists() and not two.exists()
    assert sorted(item["state"] for item in deletions.operations()[0]["items"]) == ["cleanup_failed", "done"]


@pytest.mark.parametrize("name", ["../old.png", "/outside/old.png", "x\\old.png", ".private", "x\0.png"])
def test_filename_boundary(name):
    with pytest.raises(ValueError):
        deletion_files.validate_filename(name)


def test_symlink_root_parent_and_stale_identity_never_delete(tmp_path):
    root = tmp_path / "owned"
    root.mkdir()
    target = root / "old.png"
    target.write_bytes(b"original")
    expected = deletion_files.snapshot(root, ["old.png"])
    link = tmp_path / "linked"
    link.symlink_to(root, target_is_directory=True)
    for path in (link, link / "nested"):
        with pytest.raises(OSError):
            deletion_files.snapshot(path, ["old.png"])
    target.write_bytes(b"unrelated")
    with pytest.raises(ValueError, match="identity"):
        deletion_files.cleanup(root, tmp_path / ".storage/activity_images_deletions", expected, "operation", "image")
    assert target.read_bytes() == b"unrelated"
    assert json.loads(json.dumps(expected)) == expected


def test_compare_to_rename_race_never_unlinks_unrelated(tmp_path, monkeypatch):
    root = tmp_path / "owned"
    root.mkdir()
    target = root / "old.png"
    target.write_bytes(b"original")
    expected = deletion_files.snapshot(root, ["old.png"])
    native = deletion_files._rename_no_replace
    def raced(source, name, directory, quarantine):
        if source == target.name:
            target.unlink()
            target.write_bytes(b"unrelated")
        native(source, name, directory, quarantine)
    monkeypatch.setattr(deletion_files, "_rename_no_replace", raced)
    with pytest.raises(ValueError, match="raced"):
        deletion_files.cleanup(root, tmp_path / ".storage/activity_images_deletions", expected, "operation", "image")
    assert target.read_bytes() == b"unrelated"
    assert not list((tmp_path / ".storage/activity_images_deletions").iterdir())


def test_cleanup_missing_variants_and_repeated_calls_are_idempotent(tmp_path):
    root = tmp_path / "owned"
    root.mkdir()
    target = root / "old.png"
    target.write_bytes(b"original")
    expected = deletion_files.snapshot(root, ["old.png"])
    deletion_files.cleanup(root, tmp_path / ".storage/activity_images_deletions", expected, "operation", "image")
    deletion_files.cleanup(root, tmp_path / ".storage/activity_images_deletions", expected, "operation", "image")
    assert not target.exists()


@pytest.mark.asyncio
async def test_candidate_metadata_failure_reuses_complete_files_with_zero_requests(ha, configured, monkeypatch):
    cache, _ = configured
    old = picture(ha, "old.png")
    await cache.async_remember("Activity", "/local/activity_images/old.png")
    deletions = manager(ha, configured, ["Activity"])
    native = cache.async_record_candidate
    async def failed(*args):
        raise OSError("metadata persistence failed after file save")
    monkeypatch.setattr(cache, "async_record_candidate", failed)
    _, operation = await select(deletions, ["old.png"], True)
    await settled(deletions)
    assert deletions.operations()[0]["state"] == "failed" and old.exists()
    assert len(ha.services.calls) == 1
    monkeypatch.setattr(cache, "async_record_candidate", native)
    retry = await deletions.async_retry_plan(operation["id"])
    assert retry["replacement_requests"] == 0
    await deletions.async_retry(operation["id"], retry["token"], True)
    await settled(deletions)
    assert deletions.operations()[0]["state"] == "done" and len(ha.services.calls) == 1
    assert len(deletions.queue._jobs) == 1
    assert deletions.queue._jobs[0]["state"] == "accepted"


@pytest.mark.asyncio
async def test_missing_persisted_candidate_retry_counts_one_new_request(ha, configured, monkeypatch):
    cache, _ = configured
    old = picture(ha, "old.png")
    await cache.async_remember("Activity", "/local/activity_images/old.png")
    deletions = manager(ha, configured, ["Activity"])
    native = cache.async_commit_deletion
    async def failed(*args):
        raise OSError("commit failed")
    monkeypatch.setattr(cache, "async_commit_deletion", failed)
    _, operation = await select(deletions, ["old.png"], True)
    await settled(deletions)
    item = cache._deletions[operation["id"]]["items"][0]
    filename = item["candidates"]["activity"].split("?", 1)[0].rsplit("/", 1)[-1]
    (old.parent / filename).unlink()
    monkeypatch.setattr(cache, "async_commit_deletion", native)
    retry = await deletions.async_retry_plan(operation["id"])
    assert retry["replacement_requests"] == 1
    await deletions.async_retry(operation["id"], retry["token"], True)
    await settled(deletions)
    assert deletions.operations()[0]["state"] == "done" and len(ha.services.calls) == 2


@pytest.mark.asyncio
async def test_missing_variants_mapping_is_reviewed_removed_and_tombstoned(ha, configured):
    cache, _ = configured
    picture(ha, "old.png")
    await cache.async_remember("Missing historical QA", "/local/activity_images/old_qa.webp")
    deletions = manager(ha, configured)
    plan, _ = await select(deletions, ["old.png"])
    assert plan["items"][0]["files"] == ["old.png"]
    assert plan["items"][0]["titles"] == ["Missing historical QA"]
    await settled(deletions)
    assert cache.exact("Missing historical QA") is None
    assert cache.is_deleted("/local/activity_images/old_qa.webp")


@pytest.mark.asyncio
async def test_queue_history_failure_never_removes_physical_file(ha, configured, monkeypatch):
    cache, _ = configured
    old = picture(ha, "old.png")
    deletions = manager(ha, configured)
    job = deletions.queue._new_job("Old result", "manual")
    job.update(state="accepted", result_url="/local/activity_images/old.png")
    deletions.queue._jobs.append(job)
    await deletions.queue._save()
    native = deletions.queue._store.async_save
    async def failed(*args):
        raise OSError("history persistence failed")
    monkeypatch.setattr(deletions.queue._store, "async_save", failed)
    _, operation = await select(deletions, ["old.png"])
    await settled(deletions)
    assert old.exists() and cache.is_deleted("/local/activity_images/old.png")
    assert deletions.operations()[0]["state"] == "cleanup_failed"
    status = deletions.queue.status()["jobs"][0]
    assert status["result_deleted"] and status["image_url"] is None
    monkeypatch.setattr(deletions.queue._store, "async_save", native)
    retry = await deletions.async_retry_plan(operation["id"])
    await deletions.async_retry(operation["id"], retry["token"], True)
    await settled(deletions)
    assert not old.exists()


@pytest.mark.asyncio
async def test_unload_cancels_no_provider_replay_and_drains_paid_persistence(ha, configured, monkeypatch):
    cache, engine = configured
    old = picture(ha, "old.png")
    await cache.async_remember("Activity", "/local/activity_images/old.png")
    deletions = manager(ha, configured, ["Activity"])
    started, release = asyncio.Event(), asyncio.Event()
    import custom_components.activity_images.engine as module
    native = module.async_save_generated_image
    async def blocked(*args, **kwargs):
        started.set()
        await release.wait()
        return await native(*args, **kwargs)
    monkeypatch.setattr(module, "async_save_generated_image", blocked)
    _, operation = await select(deletions, ["old.png"], True)
    await started.wait()
    await deletions.async_shutdown()
    stopping = asyncio.create_task(deletions.queue.async_shutdown())
    await asyncio.sleep(0.02)
    assert not stopping.done()
    release.set()
    await stopping
    assert old.exists() and cache.exact("Activity") == "/local/activity_images/old.png"
    assert len(ha.services.calls) == 1
    fresh_cache = behavior.cache_module.ActivityImageCache(ha)
    await fresh_cache.async_setup()
    engine.cache = fresh_cache
    fresh = manager(ha, (fresh_cache, engine), ["Activity"])
    await fresh.queue.async_setup()
    await fresh.async_setup()
    await settled(fresh)
    assert fresh.operations()[0]["state"] == "interrupted"
    retry = await fresh.async_retry_plan(operation["id"])
    assert retry["replacement_requests"] == 0
    await fresh.async_retry(operation["id"], retry["token"], True)
    await settled(fresh)
    assert fresh.operations()[0]["state"] == "done" and len(ha.services.calls) == 1


@pytest.mark.asyncio
async def test_cancelled_cleanup_is_drained_before_shutdown_returns(ha, configured, monkeypatch):
    import threading

    import custom_components.activity_images.library_deletion as module
    picture(ha, "old.png")
    deletions = manager(ha, configured)
    started, release = threading.Event(), threading.Event()
    native = module.cleanup
    def blocked(*args):
        started.set()
        assert release.wait(5)
        return native(*args)
    monkeypatch.setattr(module, "cleanup", blocked)
    await select(deletions, ["old.png"])
    assert await asyncio.to_thread(started.wait, 5)
    stopping = asyncio.create_task(deletions.async_shutdown())
    await asyncio.sleep(0.02)
    assert not stopping.done()
    release.set()
    await stopping
    assert deletions.operations()[0]["state"] == "done"
    with pytest.raises(ValueError, match="unloading"):
        await deletions.async_plan([deletions.library.image_id("old.png")])


@pytest.mark.asyncio
async def test_retry_plan_stale_demand_blocks_without_new_paid_request(ha, configured, monkeypatch):
    cache, engine = configured
    picture(ha, "old.png")
    await cache.async_remember("Activity", "/local/activity_images/old.png")
    deletions = manager(ha, configured, ["Activity"])
    native = engine.async_generate
    async def failed(*args, **kwargs):
        raise RuntimeError("synthetic failure")
    monkeypatch.setattr(engine, "async_generate", failed)
    _, operation = await select(deletions, ["old.png"], True)
    await settled(deletions)
    retry = await deletions.async_retry_plan(operation["id"])
    deletions.calendar.calendar_titles["calendar.test"] = ["Changed activity"]
    monkeypatch.setattr(engine, "async_generate", native)
    with pytest.raises(MappingConflict):
        await deletions.async_retry(operation["id"], retry["token"], True)
    new_plan = await deletions.async_retry_plan(operation["id"])
    assert new_plan["items"][0]["blocked_reason"]
    with pytest.raises(MappingConflict):
        await deletions.async_retry(operation["id"], new_plan["token"], True)
    assert not ha.services.calls


def test_quarantine_symlink_and_root_replacement_are_rejected(tmp_path):
    root = tmp_path / "owned"
    root.mkdir()
    (root / "old.png").write_bytes(b"original")
    expected = deletion_files.snapshot(root, ["old.png"])
    outside = tmp_path / "outside"
    outside.mkdir()
    private = tmp_path / ".storage/activity_images_deletions"
    private.parent.mkdir()
    private.symlink_to(outside, target_is_directory=True)
    with pytest.raises(OSError):
        deletion_files.cleanup(root, tmp_path / ".storage/activity_images_deletions", expected, "operation", "image")
    assert (root / "old.png").read_bytes() == b"original" and not list(outside.iterdir())
    private.unlink()
    root.rename(tmp_path / "original-directory")
    root.mkdir()
    (root / "old.png").write_bytes(b"unrelated")
    with pytest.raises(ValueError, match="directory changed"):
        deletion_files.cleanup(root, tmp_path / ".storage/activity_images_deletions", expected, "operation", "image")
    assert (root / "old.png").read_bytes() == b"unrelated"


def test_quarantine_atomic_no_replace_preserves_raced_destination(tmp_path, monkeypatch):
    root = tmp_path / "owned"
    root.mkdir()
    target = root / "old.png"
    target.write_bytes(b"original")
    expected = deletion_files.snapshot(root, ["old.png"])
    native = deletion_files._rename_no_replace
    def raced(source, name, directory, quarantine):
        (tmp_path / ".storage/activity_images_deletions" / name).write_bytes(b"unrelated quarantine file")
        native(source, name, directory, quarantine)
    monkeypatch.setattr(deletion_files, "_rename_no_replace", raced)
    with pytest.raises(FileExistsError):
        deletion_files.cleanup(root, tmp_path / ".storage/activity_images_deletions", expected, "operation", "image")
    assert target.read_bytes() == b"original"
    assert next((tmp_path / ".storage/activity_images_deletions").iterdir()).read_bytes() == b"unrelated quarantine file"


def live_demand(deletions, titles):
    calendar = deletions.calendar
    calendar.calendars = ("calendar.test",)
    scans = []
    async def refresh():
        assert not calendar._lock.locked()
        async with calendar._lock:
            calendar.calendar_titles = {"calendar.test": list(titles)}
            calendar.last_scan = str(len(scans))
            calendar.last_error = None
            scans.append(list(titles))
    calendar.async_refresh_for_deletion = refresh
    return scans


@pytest.mark.asyncio
async def test_plan_refreshes_missed_calendar_demand_and_blocks_unavailable(ha, configured):
    cache, _ = configured
    picture(ha, "old.png")
    await cache.async_remember("Missed activity", "/local/activity_images/old.png")
    deletions = manager(ha, configured)
    scans = live_demand(deletions, ["Missed activity!"])
    plan = await deletions.async_plan([deletions.library.image_id("old.png")])
    assert scans == [["Missed activity!"]]
    assert plan["replacement_requests"] == 1 and not plan["items"][0]["safe_without_regeneration"]
    async def failed():
        deletions.calendar.last_error = "Calendar is unavailable"
    deletions.calendar.async_refresh_for_deletion = failed
    with pytest.raises(MappingConflict):
        await deletions.async_delete(plan["token"], True, True)
    blocked = await deletions.async_plan([deletions.library.image_id("old.png")])
    assert "could not be verified" in blocked["items"][0]["blocked_reason"]
    with pytest.raises(MappingConflict):
        await deletions.async_delete(blocked["token"], True, True)
    assert not ha.services.calls


@pytest.mark.asyncio
async def test_commit_refresh_rejects_new_calendar_consumers_without_more_paid_work(ha, configured, monkeypatch):
    cache, engine = configured
    old = picture(ha, "old.png")
    await cache.async_remember("Known activity", "/local/activity_images/old.png")
    await cache.async_remember("New activity", "/local/activity_images/old.png")
    deletions = manager(ha, configured)
    titles = ["Known activity"]
    scans = live_demand(deletions, titles)
    native = engine.async_generate
    async def generate(*args, **kwargs):
        result = await native(*args, **kwargs)
        titles.append("New activity")
        return result
    monkeypatch.setattr(engine, "async_generate", generate)
    plan, _ = await select(deletions, ["old.png"], True)
    assert plan["replacement_requests"] == 1
    await settled(deletions)
    assert len(scans) >= 4 and deletions.operations()[0]["state"] == "failed"
    assert old.exists() and cache.exact("Known activity") == "/local/activity_images/old.png"
    assert cache.exact("New activity") == "/local/activity_images/old.png" and len(ha.services.calls) == 1


@pytest.mark.parametrize("kind", ["symlink", "fifo"])
def test_raced_symlink_or_special_file_is_restored_without_following(tmp_path, monkeypatch, kind):
    import os
    root = tmp_path / "owned"
    root.mkdir()
    target = root / "old.png"
    target.write_bytes(b"original")
    outside = tmp_path / "outside"
    outside.write_bytes(b"do not touch")
    expected = deletion_files.snapshot(root, ["old.png"])
    native = deletion_files._rename_no_replace
    def raced(source, name, directory, quarantine):
        if source == target.name:
            target.unlink()
            if kind == "symlink":
                target.symlink_to(outside)
            else:
                os.mkfifo(target)
        native(source, name, directory, quarantine)
    monkeypatch.setattr(deletion_files, "_rename_no_replace", raced)
    with pytest.raises((ValueError, OSError)):
        deletion_files.cleanup(root, tmp_path / ".storage/activity_images_deletions", expected, "operation", "image")
    assert target.is_symlink() if kind == "symlink" else target.is_fifo()
    assert outside.read_bytes() == b"do not touch"
    assert not list((tmp_path / ".storage/activity_images_deletions").iterdir())


def test_private_quarantine_cross_device_failure_retains_original(tmp_path, monkeypatch):
    import errno
    root = tmp_path / "owned"
    root.mkdir()
    target = root / "old.png"
    target.write_bytes(b"original")
    expected = deletion_files.snapshot(root, ["old.png"])
    def cross_device(*args):
        raise OSError(errno.EXDEV, "Cross-device link")
    monkeypatch.setattr(deletion_files, "_rename_no_replace", cross_device)
    with pytest.raises(OSError) as error:
        deletion_files.cleanup(root, tmp_path / ".storage/activity_images_deletions", expected, "operation", "image")
    assert error.value.errno == errno.EXDEV and target.read_bytes() == b"original"


@pytest.mark.asyncio
async def test_live_deletion_refresh_is_resolve_only_even_when_auto_enabled(ha, configured, monkeypatch):
    cache, _ = configured
    picture(ha, "old.png")
    await cache.async_remember("Existing activity", "/local/activity_images/old.png")
    events = {"calendar.school": {"events": [{"summary": "Existing activity"}, {"summary": "Missing activity"}]}}
    adapter, queue, _ = behavior._calendar_test_setup(ha, configured, monkeypatch, events, enabled=True)
    deletions = ActivityImageDeletions(ha, ActivityImageLibrary(ha, cache), queue, adapter)
    before = cache.entries()
    plan = await deletions.async_plan([deletions.library.image_id("old.png")])
    assert plan["replacement_requests"] == 1 and cache.entries() == before
    assert queue.status()["pending"] == [] and not ha.services.calls
    started, release = asyncio.Event(), asyncio.Event()
    async def blocked(call):
        started.set()
        await release.wait()
        return events
    ha.services.async_register("calendar", "get_events", blocked)
    refresh = asyncio.create_task(adapter.async_refresh_for_deletion())
    await started.wait()
    refresh.cancel()
    await asyncio.sleep(0.01)
    assert not refresh.done()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await refresh
    assert queue.status()["pending"] == [] and not ha.services.calls
    await adapter.async_shutdown()


@pytest.mark.asyncio
@pytest.mark.parametrize("changed", ["demand", "identity"])
async def test_abandon_releases_stale_reservations_retains_paid_candidate_and_survives_restart(ha, configured, monkeypatch, changed):
    cache, engine = configured
    old = picture(ha, "old.png")
    url = "/local/activity_images/old.png"
    await cache.async_remember("Activity", url)
    deletions = manager(ha, configured, ["Activity"])
    native = engine.async_generate
    async def generate(*args, **kwargs):
        result = await native(*args, **kwargs)
        if changed == "demand":
            deletions.calendar.calendar_titles["calendar.test"].append("New consumer")
        else:
            picture(ha, "old.png", "blue")
        return result
    monkeypatch.setattr(engine, "async_generate", generate)
    _, operation = await select(deletions, ["old.png"], True)
    await settled(deletions)
    assert deletions.operations()[0]["state"] == "failed" and deletions.operations()[0]["can_abandon"]
    candidate = deepcopy(cache._deletions[operation["id"]]["items"][0]["candidates"])
    paid = len(ha.services.calls)
    assert paid == 1 and deletions.queue._jobs[0]["state"] == "review"
    blocked = await deletions.async_plan([deletions.library.image_id(old.name)])
    assert blocked["items"][0]["blocked_reason"]
    with pytest.raises(ValueError):
        await deletions.async_abandon(operation["id"], False)
    result = await deletions.async_abandon(operation["id"], True)
    assert result["state"] == "abandoned" and not result["can_abandon"]
    assert cache._deletions[operation["id"]]["items"][0]["candidates"] == candidate
    assert deletions.queue._jobs[0]["state"] == "abandoned"
    assert deletions.queue._jobs[0]["result_url"] == candidate["activity"]
    assert old.exists() and cache.exact("Activity") == url and len(ha.services.calls) == paid
    assert not cache.reserved("Activity", url) and not deletions.queue._pending
    fresh_plan = await deletions.async_plan([deletions.library.image_id(old.name)])
    assert not fresh_plan["items"][0]["blocked_reason"]
    assert (await deletions.async_abandon(operation["id"], True))["state"] == "abandoned"
    with pytest.raises(ValueError):
        await deletions.async_retry_plan(operation["id"])
    with pytest.raises(ValueError):
        await deletions.queue.async_retry(job_id=deletions.queue._jobs[0]["id"])
    await deletions.async_shutdown()
    await deletions.queue.async_shutdown()
    fresh_cache = behavior.cache_module.ActivityImageCache(ha)
    await fresh_cache.async_setup()
    engine.cache = fresh_cache
    fresh = manager(ha, (fresh_cache, engine), ["Activity"])
    await fresh.queue.async_setup()
    await fresh.async_setup()
    await settled(fresh)
    assert fresh.operations()[0]["state"] == "abandoned"
    assert fresh.queue._jobs[0]["state"] == "abandoned" and len(ha.services.calls) == paid
    assert not (await fresh.async_plan([fresh.library.image_id(old.name)]))["items"][0]["blocked_reason"]
    await fresh_cache.async_link("Another alias", url)
    assert fresh_cache.exact("Another alias") == url


@pytest.mark.asyncio
@pytest.mark.parametrize("failed_store", ["cache", "queue"])
async def test_abandon_persistence_failure_rolls_back_and_keeps_reservations(ha, configured, monkeypatch, failed_store):
    cache, engine = configured
    old = picture(ha, "old.png")
    await cache.async_remember("Activity", "/local/activity_images/old.png")
    deletions = manager(ha, configured, ["Activity"])
    commit = cache.async_commit_deletion
    async def failed(*args):
        raise OSError("synthetic commit failure")
    monkeypatch.setattr(cache, "async_commit_deletion", failed)
    _, operation = await select(deletions, ["old.png"], True)
    await settled(deletions)
    monkeypatch.setattr(cache, "async_commit_deletion", commit)
    before_op = deepcopy(cache._deletions[operation["id"]])
    before_jobs = deepcopy(deletions.queue._jobs)
    before_saved = deepcopy(ha.saved)
    store = cache._store if failed_store == "cache" else deletions.queue._store
    monkeypatch.setattr(store, "async_save", failed)
    with pytest.raises(OSError):
        await deletions.async_abandon(operation["id"], True)
    assert cache._deletions[operation["id"]] == before_op
    assert deletions.queue._jobs == before_jobs
    assert ha.saved == before_saved
    assert cache.reserved("Activity", "/local/activity_images/old.png")
    assert old.exists() and len(ha.services.calls) == 1


@pytest.mark.asyncio
async def test_abandon_mixed_batch_leaves_committed_cleanup_and_done_untouched(ha, configured, monkeypatch):
    cache, _ = configured
    for name in ("failed.png", "cleanup.png", "done.png"):
        picture(ha, name)
        await cache.async_remember(name, "/local/activity_images/" + name)
    deletions = manager(ha, configured)
    native_replace = deletions._replace
    async def selective_replace(operation_id, image_id):
        if deletions._item(operation_id, image_id)["filename"] == "failed.png":
            raise OSError("precommit failure")
        return await native_replace(operation_id, image_id)
    monkeypatch.setattr(deletions, "_replace", selective_replace)
    import custom_components.activity_images.library_deletion as module
    native_cleanup = module.cleanup
    def selective_cleanup(root, quarantine_root, expected, operation_id, image_id):
        if "cleanup.png" in expected["files"]:
            raise OSError("cleanup failure")
        return native_cleanup(root, quarantine_root, expected, operation_id, image_id)
    monkeypatch.setattr(module, "cleanup", selective_cleanup)
    _, operation = await select(deletions, ["failed.png", "cleanup.png", "done.png"])
    await settled(deletions)
    before = {item["filename"]: deepcopy(item) for item in cache._deletions[operation["id"]]["items"]}
    result = await deletions.async_abandon(operation["id"], True)
    assert result["state"] == "cleanup_failed" and not result["can_abandon"]
    after = {item["filename"]: item for item in cache._deletions[operation["id"]]["items"]}
    assert after["cleanup.png"] == before["cleanup.png"] and after["done.png"] == before["done.png"]
    assert after["failed.png"]["state"] == "abandoned"
    assert cache.exact("failed.png") == "/local/activity_images/failed.png"
    assert cache.exact("cleanup.png") is None and cache.exact("done.png") is None
    assert (await deletions.async_retry_plan(operation["id"]))["replacement_requests"] == 0
    assert not ha.services.calls


@pytest.mark.asyncio
async def test_committed_cleanup_cannot_be_abandoned(ha, configured, monkeypatch):
    picture(ha, "old.png")
    deletions = manager(ha, configured)
    import custom_components.activity_images.library_deletion as module
    def failed(*args):
        raise OSError("cleanup failure")
    monkeypatch.setattr(module, "cleanup", failed)
    _, operation = await select(deletions, ["old.png"])
    await settled(deletions)
    assert not deletions.operations()[0]["can_abandon"]
    with pytest.raises(MappingConflict, match="committed cleanup"):
        await deletions.async_abandon(operation["id"], True)


@pytest.mark.asyncio
@pytest.mark.parametrize("overflow", ["titles", "events"])
async def test_deletion_live_demand_truncation_blocks_without_changing_normal_caps(ha, configured, monkeypatch, overflow):
    cache, _ = configured
    old = picture(ha, "old.png")
    await cache.async_remember("Protected consumer", "/local/activity_images/old.png")
    summaries = ([f"Other {index}" for index in range(64)] if overflow == "titles" else ["Other"] * 512) + ["Protected consumer"]
    events = {"calendar.school": {"events": [{"summary": title} for title in summaries]}}
    adapter, queue, _ = behavior._calendar_test_setup(ha, configured, monkeypatch, events, enabled=True)
    deletions = ActivityImageDeletions(ha, ActivityImageLibrary(ha, cache), queue, adapter)
    before_cache = cache.entries()
    original = old.read_bytes()
    plan = await deletions.async_plan([deletions.library.image_id(old.name)])
    assert "64 distinct titles or 512 events" in plan["items"][0]["blocked_reason"]
    assert not plan["items"][0]["safe_without_regeneration"]
    with pytest.raises(MappingConflict):
        await deletions.async_delete(plan["token"], True, True)
    assert cache.entries() == before_cache and old.read_bytes() == original
    assert not cache._deletions and not ha.services.calls and not queue.status()["pending"]
    assert adapter.last_error is None and len(adapter.calendar_titles["calendar.school"]) == (64 if overflow == "titles" else 1)
    # Normal refresh keeps the preexisting capped display and explicit auto-generation behavior.
    await adapter.async_refresh()
    assert len(queue.status()["pending"]) == (64 if overflow == "titles" else 1)
    await adapter.async_shutdown()


@pytest.mark.asyncio
async def test_hardlinked_master_and_derivative_rejected_before_mapping_commit(ha, configured):
    import os
    cache, _ = configured
    old = picture(ha, "old.png")
    derivative = old.with_name("old_thumb.webp")
    os.link(old, derivative)
    await cache.async_remember("Master alias", "/local/activity_images/old.png")
    await cache.async_remember("Derivative alias", "/local/activity_images/old_thumb.webp")
    deletions = manager(ha, configured)
    before = cache.entries()
    with pytest.raises(ValueError, match="Hardlinked"):
        deletion_files.snapshot(old.parent, [old.name, derivative.name])
    plan = await deletions.async_plan([deletions.library.image_id(old.name)])
    assert "Hardlinked" in plan["items"][0]["blocked_reason"]
    with pytest.raises(MappingConflict):
        await deletions.async_delete(plan["token"], False, True)
    assert old.exists() and derivative.exists() and old.stat().st_nlink == 2
    assert cache.entries() == before and not cache._tombstones and not cache._deletions and not ha.services.calls


@pytest.mark.asyncio
async def test_standalone_queue_preview_is_bounded_without_changing_master(ha, configured):
    from io import BytesIO

    from PIL import Image
    old = picture(ha, "standalone.png")
    Image.new("RGB", (1200, 600), "red").save(old)
    original = old.read_bytes()
    library = ActivityImageLibrary(ha, configured[0])
    data, mime = await library.async_read(library.image_id(old.name), "preview")
    with Image.open(BytesIO(data)) as preview:
        assert preview.size == (256, 128)
    assert mime == "image/png" and old.read_bytes() == original
    master, _ = await library.async_read(library.image_id(old.name), "master")
    assert master == original


@pytest.mark.asyncio
async def test_interrupted_abandon_has_no_lingering_paid_requests(ha, configured, monkeypatch):
    cache, engine = configured
    old = picture(ha, "old.png")
    await cache.async_remember("Activity", "/local/activity_images/old.png")
    deletions = manager(ha, configured, ["Activity"])
    monkeypatch.setattr(deletions, "_kick", lambda: None)
    _, operation = await select(deletions, ["old.png"], True)
    await deletions.async_shutdown()
    fresh_cache = behavior.cache_module.ActivityImageCache(ha)
    await fresh_cache.async_setup()
    engine.cache = fresh_cache
    fresh = manager(ha, (fresh_cache, engine), ["Activity"])
    await fresh.queue.async_setup()
    await fresh.async_setup()
    await settled(fresh)
    assert fresh.operations()[0]["state"] == "interrupted"
    abandoned = await fresh.async_abandon(operation["id"], True)
    assert abandoned["state"] == "abandoned" and not abandoned["can_abandon"]
    fresh._kick()
    fresh.queue._kick()
    await settled(fresh)
    assert not fresh.queue._pending and not ha.services.calls and old.exists()
    assert fresh_cache.exact("Activity") == "/local/activity_images/old.png"
    assert not (await fresh.async_plan([fresh.library.image_id(old.name)]))["items"][0]["blocked_reason"]


@pytest.mark.asyncio
async def test_abandon_rejects_outstanding_managed_work(ha, configured, monkeypatch):
    cache, _ = configured
    picture(ha, "old.png")
    deletions = manager(ha, configured)
    native = deletions._replace
    async def failure(*args):
        raise OSError("precommit failure")
    monkeypatch.setattr(deletions, "_replace", failure)
    _, operation = await select(deletions, ["old.png"])
    await settled(deletions)
    monkeypatch.setattr(deletions, "_replace", native)
    item = cache._deletions[operation["id"]]["items"][0]
    job = deletions.queue._new_job("Synthetic managed title", "manual")
    job["deletion"] = {"operation_id": operation["id"], "image_id": item["id"], "key": "synthetic managed title"}
    deletions.queue._jobs.append(job)
    deletions.queue._pending.append(job["title"])
    await deletions.queue._save()
    with pytest.raises(MappingConflict, match="still settling"):
        await deletions.async_abandon(operation["id"], True)
    assert cache._deletions[operation["id"]]["items"][0]["state"] == "failed"
    await deletions.queue.async_cancel([job["title"]])
    assert (await deletions.async_abandon(operation["id"], True))["state"] == "abandoned"
    deletions.queue._kick()
    assert not deletions.queue._pending and not ha.services.calls

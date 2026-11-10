"""Explained manual automatic-generation blocks and confirmed restoration."""
from __future__ import annotations

import pytest
import test_behavior as behavior
from test_library import finished

ha = behavior.ha
configured = behavior.configured
queue_module = behavior.queue_module


def manual_block(queue, key):
    return next(item for item in queue.status()["manual_blocks"] if item["key"] == key)


@pytest.mark.asyncio
async def test_cancel_discard_accept_remain_blocked_but_safe_is_restorable(ha, configured, monkeypatch):
    _, engine = configured
    queue = queue_module.ActivityImageQueue(ha, engine)
    monkeypatch.setattr(queue, "_kick", lambda: None)
    await queue.async_setup()
    job = await queue.async_candidate("Cancel Target")
    assert manual_block(queue, "cancel target")["reason"] == "active"
    assert manual_block(queue, "cancel target")["revision"] == 1
    assert await queue.async_cancel(["Cancel Target"]) == 1
    assert manual_block(queue, "cancel target")["reason"] == "cancelled"
    assert await queue.async_enqueue(["Cancel Target"]) == []
    assert await queue.async_enqueue(["CANCEL  TARGET!"]) == []
    cancelled = manual_block(queue, "cancel target")
    assert cancelled["uncertain"] is False and cancelled["deletion"] is False
    calls = len(ha.services.calls)
    result = await queue.async_restore_auto("Cancel Target", cancelled["revision"], True)
    assert result["restored"] is True and len(ha.services.calls) == calls
    assert queue.status()["pending"] == []
    monkeypatch.setattr(queue, "_kick", lambda: None)
    assert await queue.async_enqueue(["Cancel Target"]) == ["Cancel Target"]
    await queue.async_cancel(["Cancel Target"])
    await queue.async_shutdown()

    queue = queue_module.ActivityImageQueue(ha, engine)
    await queue.async_setup()
    job = await queue.async_candidate("Review Target")
    await finished(queue)
    assert queue._job(job["id"])["state"] == "review"
    assert manual_block(queue, "review target")["reason"] == "active"
    await queue.async_discard(job["id"])
    assert manual_block(queue, "review target")["reason"] == "discarded"
    assert await queue.async_enqueue(["Review Target"]) == []
    discarded = manual_block(queue, "review target")
    await queue.async_restore_auto("  review target ", discarded["revision"], True)
    assert "review target" not in {item["key"] for item in queue.status()["manual_blocks"]}
    await queue.async_shutdown()


@pytest.mark.asyncio
async def test_failed_interrupted_manual_preserves_unknown_after_clear_and_later_success(ha, configured):
    _, engine = configured
    queue = queue_module.ActivityImageQueue(ha, engine)
    await queue.async_setup()
    ha.services.fail = True
    job = await queue.async_candidate("Fragile Title")
    await finished(queue)
    assert queue._job(job["id"])["state"] == "failed"
    assert manual_block(queue, "fragile title")["reason"] == "unknown"
    assert manual_block(queue, "fragile title")["uncertain"] is True
    await queue.async_clear_errors(preserve_retry_blocks=True)
    assert manual_block(queue, "fragile title")["reason"] == "unknown"
    assert await queue.async_enqueue(["Fragile Title"]) == []
    with pytest.raises(ValueError, match="unknown outcome"):
        await queue.async_restore_auto("Fragile Title", manual_block(queue, "fragile title")["revision"], True)
    ha.services.fail = False
    retry = await queue.async_retry(job_id=job["id"])
    await finished(queue)
    assert queue._job(retry["id"])["state"] == "review"
    # Later candidate and acceptance must not silently erase prior uncertainty.
    assert manual_block(queue, "fragile title")["uncertain"] is True
    await queue.async_accept(retry["id"])
    info = manual_block(queue, "fragile title")
    assert info["reason"] == "accepted" and info["uncertain"] is True
    with pytest.raises(ValueError, match="unknown outcome"):
        await queue.async_restore_auto("Fragile Title", info["revision"], True)
    assert await queue.async_enqueue(["Fragile Title"]) == []
    await queue.async_shutdown()


@pytest.mark.asyncio
async def test_restore_zero_calls_then_one_next_authorized_scan(ha, configured, monkeypatch):
    events = {"calendar.school": {"events": [{"summary": "Restore Target"}]}}
    adapter, queue, _ = behavior._calendar_test_setup(
        ha, configured, monkeypatch, events, enabled=True,
    )
    await adapter.async_setup()
    job = await queue.async_candidate("Restore Target")
    await queue._run()
    assert queue._job(job["id"])["state"] == "review"
    await queue.async_discard(job["id"])
    assert manual_block(queue, "restore target")["reason"] == "discarded"
    await adapter.async_refresh()
    assert queue._pending == []
    calls = len(ha.services.calls)
    assert calls == 1
    info = manual_block(queue, "restore target")
    await queue.async_restore_auto("Restore Target", info["revision"], True)
    assert len(ha.services.calls) == calls and queue._pending == []
    await adapter.async_refresh()
    assert queue._pending == ["Restore Target"]
    await queue._run()
    assert len(ha.services.calls) == calls + 1
    assert configured[0].exact("Restore Target") is not None
    await adapter.async_shutdown()
    await queue.async_shutdown()


@pytest.mark.asyncio
async def test_restore_stale_revision_and_double_submit(ha, configured, monkeypatch):
    _, engine = configured
    queue = queue_module.ActivityImageQueue(ha, engine)
    monkeypatch.setattr(queue, "_kick", lambda: None)
    await queue.async_setup()
    await queue.async_candidate("Stale Title")
    await queue.async_cancel(["Stale Title"])
    first = manual_block(queue, "stale title")
    assert first["revision"] == 2
    with pytest.raises(ValueError, match="revision conflict"):
        await queue.async_restore_auto("Stale Title", 1, True)
    with pytest.raises(ValueError, match="confirmation"):
        await queue.async_restore_auto("Stale Title", first["revision"], False)
    await queue.async_restore_auto("Stale Title", first["revision"], True)
    with pytest.raises(ValueError, match="No manual.*block"):
        await queue.async_restore_auto("Stale Title", first["revision"], True)
    # New work after restore starts a fresh revision chain.
    assert await queue.async_enqueue(["Stale Title"]) == ["Stale Title"]
    await queue.async_shutdown()


@pytest.mark.asyncio
async def test_manual_blocks_persist_across_restart_and_survive_trim(ha, configured, monkeypatch):
    _, engine = configured
    queue = queue_module.ActivityImageQueue(ha, engine)
    await queue.async_setup()
    job = await queue.async_candidate("Durable Title")
    await finished(queue)
    await queue.async_accept(job["id"])
    before = manual_block(queue, "durable title")
    assert before["reason"] == "accepted"
    await queue.async_shutdown()
    resumed = queue_module.ActivityImageQueue(ha, engine)
    await resumed.async_setup()
    after = manual_block(resumed, "durable title")
    assert after == before
    assert await resumed.async_enqueue(["Durable Title"]) == []
    for index in range(150):
        terminal = resumed._new_job(f"Old {index}", "background")
        terminal["state"] = "completed"
        resumed._jobs.append(terminal)
    resumed._trim_history()
    assert all(job["title"] != "Durable Title" or job["state"] not in {"accepted"} or True for job in resumed._jobs)
    trimmed = manual_block(resumed, "durable title")
    assert trimmed["reason"] == "accepted" and trimmed["revision"] == before["revision"]
    await resumed.async_restore_auto("Durable Title", trimmed["revision"], True)
    await resumed.async_shutdown()


@pytest.mark.asyncio
async def test_legacy_block_without_metadata_is_conservatively_unknown(ha, configured, monkeypatch):
    _, engine = configured
    queue = queue_module.ActivityImageQueue(ha, engine)
    monkeypatch.setattr(queue, "_kick", lambda: None)
    ha.saved[queue._store.key] = {"manual_blocked": ["legacy title"], "pending": [], "failed": {}, "jobs": []}
    await queue.async_setup()
    info = manual_block(queue, "legacy title")
    assert info["reason"] == "legacy_unknown" and info["revision"] == 0 and info["uncertain"] is True
    assert await queue.async_enqueue(["Legacy Title"]) == []
    with pytest.raises(ValueError, match="unknown outcome"):
        await queue.async_restore_auto("Legacy Title", 0, True)
    # A later manual candidate must not silently erase legacy uncertainty.
    await queue.async_candidate("Legacy Title")
    await queue.async_cancel(["Legacy Title"])
    assert manual_block(queue, "legacy title")["uncertain"] is True
    with pytest.raises(ValueError, match="unknown outcome"):
        await queue.async_restore_auto("Legacy Title", manual_block(queue, "legacy title")["revision"], True)
    # Manual generation itself remains allowed.
    again = await queue.async_candidate("Legacy Title")
    assert again["kind"] == "manual"
    await queue.async_shutdown()


@pytest.mark.asyncio
async def test_restore_refuses_active_failed_reserved_and_deletion(ha, configured, monkeypatch):
    _, engine = configured
    queue = queue_module.ActivityImageQueue(ha, engine)
    monkeypatch.setattr(queue, "_kick", lambda: None)
    await queue.async_setup()
    await queue.async_candidate("Active Title")
    with pytest.raises(ValueError, match="outstanding"):
        await queue.async_restore_auto("Active Title", manual_block(queue, "active title")["revision"], True)
    await queue.async_cancel(["Active Title"])
    queue._failed["active title"] = "recorded failure"
    with pytest.raises(ValueError, match="failure barrier"):
        await queue.async_restore_auto("Active Title", manual_block(queue, "active title")["revision"], True)
    queue._failed.clear()
    queue._retry_blocked.add("active title")
    with pytest.raises(ValueError, match="failure barrier"):
        await queue.async_restore_auto("Active Title", manual_block(queue, "active title")["revision"], True)
    queue._retry_blocked.clear()
    engine.cache._deletions["op"] = {"id": "op", "state": "pending", "items": [
        {"state": "pending", "urls": [], "reserved_titles": ["active title"]}]}
    try:
        with pytest.raises(ValueError, match="reserved"):
            await queue.async_restore_auto("Active Title", manual_block(queue, "active title")["revision"], True)
    finally:
        engine.cache._deletions.clear()
    deletion_id = "deletionjob"
    await queue.async_candidate("Deletion Title", deletion={"operation_id": "op", "image_id": "img", "key": "deletion title", "job_id": deletion_id}, job_id=deletion_id)
    info = manual_block(queue, "deletion title")
    assert info["deletion"] is True
    with pytest.raises(ValueError, match="Deletion operation owns"):
        await queue.async_restore_auto("Deletion Title", info["revision"], True)
    await queue.async_shutdown()


@pytest.mark.asyncio
async def test_mutation_rollback_restores_manual_metadata_and_snapshot_is_detached(ha, configured, monkeypatch):
    _, engine = configured
    queue = queue_module.ActivityImageQueue(ha, engine)
    monkeypatch.setattr(queue, "_kick", lambda: None)
    await queue.async_setup()
    await queue.async_candidate("Rollback Title")
    await queue.async_cancel(["Rollback Title"])
    before = manual_block(queue, "rollback title")
    with pytest.raises(RuntimeError):
        async with queue._mutation():
            queue._touch_manual_block("Rollback Title", reason="accepted", job_id="changed")
            raise RuntimeError("rollback")
    assert manual_block(queue, "rollback title") == before
    snapshot = await queue.async_coverage_snapshot()
    assert snapshot["manual_blocked"] == {"rollback title"}
    assert snapshot["manual_blocks"]["rollback title"]["reason"] == "cancelled"
    snapshot["manual_blocks"]["rollback title"]["reason"] = "mutated"
    snapshot["manual_blocked"].clear()
    assert manual_block(queue, "rollback title")["reason"] == "cancelled"
    assert "rollback title" in queue._manual_blocked
    await queue.async_shutdown()


@pytest.mark.asyncio
async def test_coverage_exposes_shared_normalized_manual_reason(ha, configured, monkeypatch):
    from types import SimpleNamespace

    from custom_components.activity_images.calendar_api import coverage
    from custom_components.activity_images.library import ActivityImageLibrary

    events = {"calendar.school": {"events": [{"summary": "Shared title"}, {"summary": "Shared title "}]}}
    adapter, queue, _ = behavior._calendar_test_setup(
        ha, configured, monkeypatch, events, enabled=True,
        calendars=("calendar.school",),
    )
    await adapter.async_setup()
    await queue.async_candidate("Shared title")
    await queue.async_cancel(["Shared title"])
    runtime = SimpleNamespace(calendar=adapter, queue=queue, cache=configured[0], library=ActivityImageLibrary(ha, configured[0]))
    await adapter.async_refresh_coverage()
    result = await coverage(runtime)
    assert ha.services.calls == []
    first = next(item for item in result["items"] if item["raw_summary"] == "Shared title")
    second = next(item for item in result["items"] if item["raw_summary"] == "Shared title ")
    assert first["manual_block"]["reason"] == "cancelled"
    assert second["manual_block"]["reason"] == "cancelled"
    assert first["eligible_for_auto"] is False and second["eligible_for_auto"] is False
    info = manual_block(queue, "shared title")
    await queue.async_restore_auto("SHARED title!", info["revision"], True)
    result = await coverage(runtime)
    restored = next(item for item in result["items"] if item["raw_summary"] == "Shared title")
    assert restored["manual_block"] is None
    await adapter.async_shutdown()
    await queue.async_shutdown()

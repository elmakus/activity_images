"""Calendar cost boundaries with real queue/cache code and fake HA providers."""
from __future__ import annotations

import asyncio
import importlib
from copy import deepcopy
from types import SimpleNamespace
from typing import Any

import pytest
import test_behavior as behavior

behavior._load_integration_entrypoint()
settings_api = importlib.import_module("custom_components.activity_images.settings")
ConfigEntrySettings = importlib.import_module("custom_components.activity_images.settings_schema").ConfigEntrySettings

ha = behavior.ha
configured = behavior.configured
const = behavior.const_module


def calendar(ha, configured, monkeypatch, summaries, *, automatic=True):
    events = {"calendar.school": {"events": [
        {"summary": summary, "start": "2026-11-15", "end": "2026-11-16"}
        for summary in summaries
    ]}}
    adapter, queue, _ = behavior._calendar_test_setup(
        ha, configured, monkeypatch, events, enabled=automatic,
    )
    return adapter, queue, events


@pytest.mark.asyncio
async def test_ignore_preserves_raw_case_time_whitespace_and_unicode(ha, configured, monkeypatch):
    titles = ["Basen 9:15", "Basen 9:16", "basen 9:15", "Basen 9:15 ",
              " Basen 9:15", "Basen", "Café", "Cafe\u0301", "[A].*", "[a].*"]
    adapter, queue, _ = calendar(ha, configured, monkeypatch, titles + [titles[0]])
    await adapter.async_setup()
    await adapter.async_ignore(titles[0], True, 0)
    await adapter.async_ignore("Café", True, 1)
    await adapter.async_ignore("[A].*", True, 2)
    await adapter.async_refresh()
    assert {event["raw_summary"] for event in adapter.raw_occurrences["calendar.school"]} == set(titles)
    sources = {source["raw_summary"] for source in adapter.automatic_sources()}
    assert sources == set(titles) - {titles[0], "Café", "[A].*"}
    assert len(queue._pending) == len({behavior.cache_module.normalize_title(t) for t in sources})
    assert not ha.services.calls
    saved = deepcopy(ha.saved[adapter._ignore_store.key])
    assert set(saved["titles"]) == {titles[0], "Café", "[A].*"}
    restored, _, _ = calendar(ha, configured, monkeypatch, [])
    await restored.async_setup()
    assert restored.ignored_titles == adapter.ignored_titles
    assert restored.ignore_revision == 3
    await adapter.async_shutdown()
    await restored.async_shutdown()


@pytest.mark.asyncio
async def test_ignore_conflict_storage_failure_and_unignore_are_nonbillable(ha, configured, monkeypatch):
    adapter, queue, _ = calendar(ha, configured, monkeypatch, ["Future title"])
    await adapter.async_refresh_coverage()
    assert queue._pending == [] and not ha.services.calls
    await adapter.async_ignore("Future title", True, 0)
    with pytest.raises(ValueError, match="changed elsewhere"):
        await adapter.async_ignore("Different", True, 0)
    original = adapter._ignore_store.async_save

    async def fail(_data):
        raise OSError("Synthetic disk failure")

    monkeypatch.setattr(adapter._ignore_store, "async_save", fail)
    with pytest.raises(OSError):
        await adapter.async_ignore("Different", True, 1)
    assert adapter.ignore_revision == 1 and adapter.ignored_titles == {"Future title"}
    monkeypatch.setattr(adapter._ignore_store, "async_save", original)
    await adapter.async_ignore("Future title", False, 1)
    assert adapter.ignored_titles == set() and queue._pending == [] and not ha.services.calls
    await adapter.async_shutdown()


@pytest.mark.asyncio
async def test_cancelled_coverage_refresh_drains_joined_automatic_scan(ha, configured, monkeypatch):
    adapter, queue, events = calendar(ha, configured, monkeypatch, ["Do not enqueue"])
    entered, release = asyncio.Event(), asyncio.Event()

    async def get_events(_call):
        entered.set()
        await release.wait()
        return events

    ha.services.async_register("calendar", "get_events", get_events)
    automatic = asyncio.create_task(adapter.async_refresh())
    await entered.wait()
    readonly = asyncio.create_task(adapter.async_refresh_coverage())
    await asyncio.sleep(0)
    readonly.cancel()
    await asyncio.sleep(0)
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await readonly
    await automatic
    assert queue._pending == [] and not ha.services.calls
    assert adapter._generation_suppressed == 0
    await adapter.async_shutdown()


@pytest.mark.asyncio
@pytest.mark.parametrize("edit", ["ignore", "disable"])
async def test_acknowledged_edit_blocks_queued_job_after_generator_wait(ha, configured, monkeypatch, edit):
    adapter, queue, _ = calendar(ha, configured, monkeypatch, ["Waiting activity"])
    engine = configured[1]
    entry = SimpleNamespace(entry_id="entry", data=dict(engine.settings), options={})
    entry.runtime_data = SimpleNamespace(calendar=adapter)
    ha.config_entries.async_entries = lambda _domain: [entry]
    adapter.settings = engine.settings = ConfigEntrySettings(entry)
    await adapter.async_refresh()
    assert queue._pending == ["Waiting activity"]
    entered = asyncio.Event()
    generate = engine.async_generate

    async def waiting(*args, **kwargs):
        entered.set()
        return await generate(*args, **kwargs)

    monkeypatch.setattr(engine, "async_generate", waiting)
    async with engine._lock:
        work = asyncio.create_task(queue._run())
        await entered.wait()
        if edit == "ignore":
            await adapter.async_ignore("Waiting activity", True, adapter.ignore_revision)
        else:
            await settings_api.save_settings(ha, {const.CONF_AUTO_CALENDARS: []}, settings_api.revision(entry))
    await asyncio.wait_for(work, 3)
    assert not ha.services.calls and not queue._pending
    assert queue._jobs[-1]["state"] == "cancelled"
    await adapter.async_shutdown()


@pytest.mark.asyncio
async def test_manual_candidate_remains_authorized_for_an_ignored_title(ha, configured, monkeypatch):
    adapter, queue, _ = calendar(ha, configured, monkeypatch, ["Manual activity"], automatic=False)
    await adapter.async_refresh_coverage()
    await adapter.async_ignore("Manual activity", True, 0)
    job = await queue.async_candidate("Manual activity")
    await queue._run()
    assert len(ha.services.calls) == 1
    assert queue._job(job["id"])["state"] == "review"
    assert adapter.ignored_titles == {"Manual activity"}
    assert configured[0].exact("Manual activity") is None
    await adapter.async_shutdown()


@pytest.mark.asyncio
async def test_legacy_pending_background_work_requires_review(ha, configured, monkeypatch):
    queue = behavior.queue_module.ActivityImageQueue(ha, configured[1])
    ha.saved[queue._store.key] = {"pending": ["Legacy activity"], "failed": {}}
    monkeypatch.setattr(queue, "_kick", lambda: None)
    await queue.async_setup()
    assert queue._pending == [] and not ha.services.calls
    assert queue._jobs[0]["state"] == "interrupted"
    assert "legacy activity" in queue._retry_blocked
    await queue.async_shutdown()


@pytest.mark.asyncio
async def test_discovery_and_all_coverage_queries_refresh_and_ignore_make_zero_paid_calls(ha, configured, monkeypatch):
    from test_library import picture

    from custom_components.activity_images.calendar_api import coverage, discover
    from custom_components.activity_images.library import ActivityImageLibrary

    events = {
        "calendar.school": {"events": [{"summary": title} for title in ["Morning", "Morning", "Morning ", "Other"]]},
        "calendar.work": {"events": [{"summary": "Morning"}]},
    }
    adapter, queue, _ = behavior._calendar_test_setup(
        ha, configured, monkeypatch, events, enabled=True, calendars=("calendar.school", "calendar.work"),
    )
    cache = configured[0]
    picture(ha, "available.png")
    await cache.async_remember("Morning", "/local/activity_images/available.png")
    runtime = SimpleNamespace(calendar=adapter, queue=queue, cache=cache, library=ActivityImageLibrary(ha, cache))
    await adapter.async_refresh_coverage()
    await adapter.async_ignore("Dormant ignored ", True, 0)
    await adapter.async_ignore("Morning ", True, 1)
    result = await coverage(runtime)
    assert result["stats"] == {"titles": 4, "available": 2, "missing": 2, "ignored": 2, "queued": 0, "failed": 0}
    morning = next(item for item in result["items"] if item["raw_summary"] == "Morning")
    assert morning["occurrence_count"] == 3 and len(morning["calendars"]) == 2
    assert morning["resolution"] == "normalized" and morning["image"]["id"]
    ignored = next(item for item in result["items"] if item["raw_summary"] == "Morning ")
    assert ignored["ignored"] and ignored["image_status"] == "available" and not ignored["eligible_for_auto"]
    assert (await coverage(runtime, filter="ignored"))["total"] == 2
    assert (await coverage(runtime, query="morning"))["total"] == 2
    assert (await coverage(runtime, calendar="calendar.work"))["total"] == 1
    assert (await coverage(runtime, sort="occurrences", limit=1))["items"][0]["raw_summary"] == "Morning"
    assert (await coverage(runtime, offset=1, limit=1))["total"] == 4
    entities = discover(adapter)["entities"]
    assert all(entity["available"] for entity in entities)
    assert next(entity for entity in entities if entity["entity_id"] == "calendar.test")["monitor"] is False
    assert queue._pending == [] and not ha.services.calls
    await adapter.async_shutdown()


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", [{}, {"summary": None}, "not an event"])
async def test_malformed_partial_demand_cannot_authorize_generation(ha, configured, monkeypatch, bad):
    adapter, queue, events = calendar(ha, configured, monkeypatch, ["Valid"])
    events["calendar.school"]["events"].append(bad)
    await adapter.async_refresh()
    assert adapter.status()["complete"] is False
    assert adapter.automatic_sources() == [] and queue._pending == [] and not ha.services.calls
    await adapter.async_shutdown()


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", [TimeoutError, ConnectionError])
async def test_unknown_paid_outcome_is_never_replayed_by_scans_refresh_or_restart(ha, configured, monkeypatch, outcome):
    adapter, queue, _ = calendar(ha, configured, monkeypatch, ["Uncertain activity"])
    await adapter.async_refresh()
    attempts = []
    call = ha.services.async_call

    async def uncertain(*args, **kwargs):
        if args[:2] != ("ai_task", "generate_image"):
            return await call(*args, **kwargs)
        attempts.append(1)
        raise outcome("Provider may already have accepted the image")

    monkeypatch.setattr(ha.services, "async_call", uncertain)
    await queue._run()
    assert attempts == [1] and queue._jobs[-1]["state"] == "failed"
    await adapter.async_refresh_coverage()
    await adapter.async_refresh()
    assert attempts == [1] and queue._pending == []
    await queue.async_shutdown()
    restored = behavior.queue_module.ActivityImageQueue(ha, configured[1])
    monkeypatch.setattr(restored, "_kick", lambda: None)
    await restored.async_setup()
    assert restored._pending == [] and "uncertain activity" in restored._failed
    assert await restored.async_enqueue(["Uncertain activity"]) == []
    await restored.async_clear_errors(preserve_retry_blocks=True)
    assert await restored.async_enqueue(["Uncertain activity"]) == []
    await restored.async_shutdown()
    await adapter.async_shutdown()


@pytest.mark.asyncio
async def test_ignore_acknowledgement_during_cache_wait_prevents_new_attempt(ha, configured, monkeypatch):
    adapter, queue, _ = calendar(ha, configured, monkeypatch, ["Wait for cache"])
    await adapter.async_refresh()
    cache = configured[0]
    find = cache.async_find
    entered, release = asyncio.Event(), asyncio.Event()

    async def waiting(title, **kwargs):
        result = await find(title, **kwargs)
        if kwargs.get("prune") is False:
            entered.set()
            await release.wait()
        return result

    monkeypatch.setattr(cache, "async_find", waiting)
    work = asyncio.create_task(queue._run())
    await entered.wait()
    await adapter.async_ignore("Wait for cache", True, adapter.ignore_revision)
    release.set()
    await asyncio.wait_for(work, 3)
    assert not ha.services.calls and queue._jobs[-1]["state"] == "cancelled"
    await adapter.async_shutdown()


@pytest.mark.asyncio
async def test_ignore_after_rate_limit_rejection_blocks_the_next_provider_retry(ha, configured, monkeypatch):
    from custom_components.activity_images import retry

    adapter, queue, _ = calendar(ha, configured, monkeypatch, ["Rate limited"])
    await adapter.async_refresh()
    attempts = []

    class Rejected(Exception):
        status = 429

    async def rejected(*_args, **_kwargs):
        attempts.append(1)
        await adapter.async_ignore("Rate limited", True, adapter.ignore_revision)
        raise Rejected("Explicit rejection")

    async def no_delay(_seconds):
        pass

    monkeypatch.setattr(ha.services, "async_call", rejected)
    monkeypatch.setattr(retry.asyncio, "sleep", no_delay)
    await queue._run()
    assert attempts == [1] and queue._jobs[-1]["state"] == "cancelled"
    await adapter.async_shutdown()


@pytest.mark.asyncio
async def test_already_dispatched_provider_result_is_kept_after_ignore(ha, configured, monkeypatch):
    adapter, queue, _ = calendar(ha, configured, monkeypatch, ["Already dispatched"])
    await adapter.async_refresh()
    entered, release = asyncio.Event(), asyncio.Event()
    call = ha.services.async_call

    async def provider(*args, **kwargs):
        entered.set()
        await release.wait()
        return await call(*args, **kwargs)

    monkeypatch.setattr(ha.services, "async_call", provider)
    work = asyncio.create_task(queue._run())
    await entered.wait()
    await adapter.async_ignore("Already dispatched", True, adapter.ignore_revision)
    release.set()
    await asyncio.wait_for(work, 3)
    assert len(ha.services.calls) == 1 and queue._jobs[-1]["state"] == "completed"
    assert configured[0].exact("Already dispatched") and "Already dispatched" in adapter.ignored_titles
    await adapter.async_shutdown()


@pytest.mark.asyncio
@pytest.mark.parametrize("legacy_auto", [False, True])
async def test_native_migration_preserves_only_prior_selected_authorization_and_registry_binding(ha, monkeypatch, legacy_auto):
    behavior._load_integration_entrypoint()
    entry = SimpleNamespace(entry_id="migrated", data={const.CONF_AI_ENTITY: "ai_task.mock"},
                            options={const.CONF_CALENDARS: ["calendar.school"], const.CONF_AUTO_GENERATE: legacy_auto,
                                     "unrelated": {"preserved": True}})
    settings_api.migrate_calendar_options(ha, entry)
    assert entry.options[const.CONF_AUTO_CALENDARS] == (["calendar.school"] if legacy_auto else [])
    assert entry.options[const.CONF_AUTO_GENERATE] is False
    assert entry.options[const.CALENDAR_BINDINGS] == {"calendar.school": "registry-calendar.school"}
    assert entry.options["unrelated"] == {"preserved": True}
    before = deepcopy(entry.options)
    ha.registry["calendar.school"].id = "unrelated-replacement"
    settings_api.migrate_calendar_options(ha, entry)
    assert entry.options == before  # Restart cannot bind a reused entity ID again.


@pytest.mark.asyncio
@pytest.mark.parametrize("snapshot", [None, {}, {"_calendar_registry_ids": "corrupt"}])
async def test_lost_or_corrupt_display_snapshot_cannot_rebind_a_reused_calendar(ha, configured, monkeypatch, snapshot):
    adapter, queue, _ = calendar(ha, configured, monkeypatch, ["Replacement title"])
    entry = SimpleNamespace(entry_id="bound", data=dict(configured[1].settings), options={})
    configured[1].settings = ConfigEntrySettings(entry)
    await adapter.async_shutdown()
    ha.saved[adapter._store.key] = snapshot
    ha.registry["calendar.school"].id = "unrelated-replacement"
    restored = type(adapter)(ha, configured[0], queue, configured[1].settings)
    await restored.async_setup()
    await restored.async_start()
    await restored.async_refresh()
    assert restored.last_error and "identity" in restored.last_error
    assert queue._pending == [] and restored.automatic_sources() == [] and not ha.services.calls
    await restored.async_shutdown()


@pytest.mark.asyncio
async def test_sidebar_auto_enable_requires_confirmation_and_legacy_writes_are_rejected(ha, monkeypatch):
    behavior._load_integration_entrypoint()
    entry = SimpleNamespace(entry_id="options", data={const.CONF_AI_ENTITY: "ai_task.mock"}, options={})
    ha.config_entries.async_entries = lambda _domain: [entry]
    changes = {const.CONF_CALENDARS: ["calendar.school"], const.CONF_AUTO_CALENDARS: ["calendar.school"]}
    revision = settings_api.revision(entry)
    with pytest.raises(ValueError, match="paid confirmation"):
        await settings_api.save_settings(ha, changes, revision)
    assert entry.options == {}
    await settings_api.save_settings(ha, changes, revision, confirm_auto=True)
    assert entry.options[const.CONF_AUTO_CALENDARS] == ["calendar.school"]
    revision = settings_api.revision(entry)
    with pytest.raises(ValueError, match="legacy flag"):
        await settings_api.save_settings(ha, {const.CONF_AUTO_GENERATE: True}, revision)
    await settings_api.save_settings(ha, {const.CONF_AUTO_CALENDARS: []}, revision)
    assert not ha.services.calls


@pytest.mark.asyncio
async def test_ignored_titles_remain_consumers_for_permanent_deletion(ha, configured, monkeypatch):
    from test_library import picture

    from custom_components.activity_images.library import ActivityImageLibrary
    from custom_components.activity_images.library_deletion import (
        ActivityImageDeletions,
    )

    adapter, queue, _ = calendar(ha, configured, monkeypatch, ["Keep this image"])
    picture(ha, "consumer.png")
    await configured[0].async_remember("Keep this image", "/local/activity_images/consumer.png")
    await adapter.async_refresh_coverage()
    await adapter.async_ignore("Keep this image", True, 0)
    deletions = ActivityImageDeletions(ha, ActivityImageLibrary(ha, configured[0]), queue, adapter)
    plan = await deletions.async_plan([deletions.library.image_id("consumer.png")])
    assert plan["items"][0]["needed_titles"] == ["Keep this image"]
    assert plan["items"][0]["safe_without_regeneration"] is False
    assert not ha.services.calls
    await adapter.async_shutdown()


@pytest.mark.asyncio
async def test_public_clear_errors_never_rearms_unknown_calendar_outcomes(ha, configured, monkeypatch):
    adapter, queue, _ = calendar(ha, configured, monkeypatch, ["Unknown result"])
    integration = behavior._load_integration_entrypoint()
    await integration.async_setup(ha, {})
    ha.data[const.DOMAIN]["synthetic"] = SimpleNamespace(queue=queue)
    attempts = []
    call = ha.services.async_call

    async def uncertain(*args, **kwargs):
        if args[:2] != ("ai_task", "generate_image"):
            return await call(*args, **kwargs)
        attempts.append(1)
        raise TimeoutError("Unknown provider outcome")

    monkeypatch.setattr(ha.services, "async_call", uncertain)
    await adapter.async_refresh()
    await queue._run()
    result = await ha.services.async_call("activity_images", "clear_errors", {})
    assert result == {"cleared": 1}
    await adapter.async_refresh()
    assert attempts == [1] and queue._pending == [] and "unknown result" in queue._retry_blocked
    await adapter.async_shutdown()


@pytest.mark.asyncio
async def test_corrupt_ignore_storage_keeps_library_and_manual_candidates_available(ha, monkeypatch):
    integration = behavior._load_integration_entrypoint()
    await integration.async_setup(ha, {})
    entry = behavior.MockConfigEntry()
    entry.options = {const.CONF_CALENDARS: ["calendar.school"], const.CONF_AUTO_GENERATE: True}
    ha.saved["activity_images_ignored_titles"] = {"titles": "not a list", "revision": 1}
    monkeypatch.setattr(integration.ActivityImageQueue, "_kick", lambda _self: None)

    async def events(_call):
        return {"calendar.school": {"events": [{"summary": "Manual despite ignore storage failure"}]}}

    ha.services.async_register("calendar", "get_events", events)
    assert await integration.async_setup_entry(ha, entry)
    runtime: Any = entry.runtime_data
    await runtime.calendar.async_refresh()
    assert "automatic generation is blocked" in runtime.calendar.status()["ignore_error"]
    assert runtime.calendar.automatic_sources() == [] and runtime.queue._pending == []
    assert (await runtime.library.async_list())["total"] == 0 and not ha.services.calls
    job = await runtime.queue.async_candidate("Manual despite ignore storage failure")
    await runtime.queue._run()
    assert runtime.queue._job(job["id"])["state"] == "review" and len(ha.services.calls) == 1
    assert await integration.async_unload_entry(ha, entry)


@pytest.mark.asyncio
async def test_legacy_trimmed_snapshot_never_invents_an_original_raw_summary(ha, configured, monkeypatch):
    adapter, queue, _ = calendar(ha, configured, monkeypatch, ["Basen 9:15 "])
    ha.saved[adapter._store.key] = {"calendar.school": ["Basen 9:15"]}
    ha.state = "not_running"
    await adapter.async_setup()
    await adapter.async_start()
    assert adapter.calendar_titles == {"calendar.school": ["Basen 9:15"]}
    assert adapter.raw_occurrences == {} and adapter.automatic_sources() == []
    assert adapter.status()["stale"] and queue._pending == []
    ha.state = "running"
    await adapter.async_refresh_coverage()
    assert adapter.raw_occurrences["calendar.school"][0]["raw_summary"] == "Basen 9:15 "
    assert queue._pending == [] and not ha.services.calls
    await adapter.async_shutdown()

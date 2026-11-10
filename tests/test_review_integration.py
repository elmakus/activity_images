"""Integration of the three review lanes (fake HA, no paid calls).

Locks only the intersections: restored-auto still needs a full scan, Coverage
combines manual reasons with partial stale flags, an own provider is never
sent from any queue path, refresh uses the per-calendar service shape, sticky
unknown survives history/settings changes, and the sensor/Card Pro shape stays.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest
import test_behavior as behavior

behavior._load_integration_entrypoint()

ha = behavior.ha
configured = behavior.configured
const = behavior.const_module


def manual_block(queue, key):
    return next(item for item in queue.status()["manual_blocks"] if item["key"] == key)


def own_wrapper(ha, entity_id, entry_id):
    ha.registry[entity_id] = SimpleNamespace(
        id=f"registry-{entity_id}", entity_id=entity_id, name=None,
        original_name=entity_id, disabled_by=None, platform="activity_images",
        config_entry_id=entry_id, domain="ai_task",
    )


@pytest.mark.asyncio
async def test_restored_auto_still_needs_full_complete_scan(ha, configured, monkeypatch):
    events = {
        "calendar.school": {"events": [{"summary": "Restore Target"}]},
        "calendar.work": {"events": [{"summary": "Work Only"}]},
    }
    adapter, queue, _ = behavior._calendar_test_setup(
        ha, configured, monkeypatch, events, enabled=True,
        calendars=("calendar.school", "calendar.work"),
    )
    await adapter.async_setup()
    await adapter.async_ignore("Work Only", True, 0)
    job = await queue.async_candidate("Restore Target")
    await queue._run()
    assert queue._job(job["id"])["state"] == "review"
    await queue.async_discard(job["id"])
    await adapter.async_refresh()
    assert queue._pending == [] and len(ha.services.calls) == 1
    info = manual_block(queue, "restore target")
    await queue.async_restore_auto("Restore Target", info["revision"], True)
    assert queue._pending == [] and len(ha.services.calls) == 1

    async def partial(call):
        requested = call.data.get("entity_id", [])
        if "calendar.work" in requested:
            raise RuntimeError("work provider down")
        return {"calendar.school": {"events": [{"summary": "Restore Target"}]}}

    ha.services.async_register("calendar", "get_events", partial)
    result = await adapter.async_refresh()
    assert result["complete"] is False
    assert adapter.display_titles_map()["calendar.school"] == ["Restore Target"]
    assert adapter.automatic_sources() == [] and queue._pending == []
    assert len(ha.services.calls) == 1
    # The failed scan scheduled production backoff; consume it before rescanning.
    if adapter._retry_cancel is not None:
        adapter._retry_cancel()
        adapter._retry_cancel = None

    async def healthy(_call):
        return events

    ha.services.async_register("calendar", "get_events", healthy)
    await adapter.async_refresh()
    assert queue._pending == ["Restore Target"]
    await queue._run()
    assert len(ha.services.calls) == 2
    await adapter.async_shutdown()
    await queue.async_shutdown()


@pytest.mark.asyncio
async def test_coverage_combines_manual_reasons_with_partial_stale(ha, configured, monkeypatch):
    from custom_components.activity_images.calendar_api import coverage
    from custom_components.activity_images.library import ActivityImageLibrary

    events = {
        "calendar.school": {"events": [{"summary": "Shared title"}]},
        "calendar.work": {"events": [{"summary": "Shared title"}]},
    }
    adapter, queue, _ = behavior._calendar_test_setup(
        ha, configured, monkeypatch, events, enabled=True,
        calendars=("calendar.school", "calendar.work"),
    )
    await adapter.async_setup()
    await queue.async_candidate("Shared title")
    await queue.async_cancel(["Shared title"])
    info = manual_block(queue, "shared title")
    assert info["reason"] == "cancelled" and info["uncertain"] is False
    await adapter.async_refresh()
    assert queue._pending == []

    async def partial(call):
        requested = call.data.get("entity_id", [])
        if "calendar.work" in requested:
            raise RuntimeError("work provider down")
        return {"calendar.school": {"events": [{"summary": "Shared title"}]}}

    ha.services.async_register("calendar", "get_events", partial)
    await adapter.async_refresh()
    runtime = SimpleNamespace(calendar=adapter, queue=queue, cache=configured[0],
                              library=ActivityImageLibrary(ha, configured[0]))
    body = await coverage(runtime)
    assert body["scan"]["complete"] is False
    item = next(entry for entry in body["items"] if entry["raw_summary"] == "Shared title")
    assert item["manual_block"] == {"reason": "cancelled", "revision": info["revision"], "uncertain": False}
    assert item["eligible_for_auto"] is False
    stale = {source["entity_id"]: source["stale"] for source in item["calendars"]}
    assert stale == {"calendar.school": False, "calendar.work": True}
    assert ha.services.calls == []
    await adapter.async_shutdown()
    await queue.async_shutdown()


@pytest.mark.asyncio
async def test_own_provider_never_reaches_provider_from_queue_paths(ha, configured, monkeypatch):
    own_wrapper(ha, "ai_task.own", "entry1")
    events = {"calendar.school": {"events": [{"summary": "Auto self"}]}}
    adapter, queue, _ = behavior._calendar_test_setup(
        ha, configured, monkeypatch, events, enabled=True,
    )
    configured[1].settings[const.CONF_AI_ENTITY] = "ai_task.own"
    await adapter.async_setup()
    job = await queue.async_candidate("Manual self")
    await queue._run()
    assert queue._job(job["id"])["state"] == "failed"
    assert "itself" in (queue._job(job["id"])["error"] or "")
    await adapter.async_refresh()
    assert queue._pending == ["Auto self"]
    await queue._run()
    assert queue._jobs[-1]["state"] == "failed"
    assert "itself" in (queue._jobs[-1]["error"] or "")
    assert ha.services.calls == []
    await adapter.async_shutdown()
    await queue.async_shutdown()


@pytest.mark.asyncio
async def test_refresh_issues_single_calendar_service_calls(ha, configured, monkeypatch):
    events = {
        "calendar.school": {"events": [{"summary": "School Night"}]},
        "calendar.work": {"events": [{"summary": "Work Day"}]},
    }
    adapter, queue, _ = behavior._calendar_test_setup(
        ha, configured, monkeypatch, events, enabled=True,
        calendars=("calendar.school", "calendar.work"),
    )
    requested = []

    async def recording(call):
        requested.append(call.data.get("entity_id"))
        return events  # Smoke-fixture shape: whole map, adapter selects its entity.

    ha.services.async_register("calendar", "get_events", recording)
    result = await adapter.async_refresh()
    assert result["complete"] is True
    assert requested == [["calendar.school"], ["calendar.work"]]
    assert adapter.calendar_titles == {"calendar.school": ["School Night"], "calendar.work": ["Work Day"]}
    await adapter.async_shutdown()
    await queue.async_shutdown()


@pytest.mark.asyncio
async def test_sticky_unknown_survives_history_trim_and_settings_change(ha, configured, monkeypatch):
    events = {"calendar.school": {"events": [{"summary": "Fragile Title"}]}}
    adapter, queue, _ = behavior._calendar_test_setup(
        ha, configured, monkeypatch, events, enabled=True,
    )
    await adapter.async_setup()
    ha.services.fail = True
    job = await queue.async_candidate("Fragile Title")
    await queue._run()
    ha.services.fail = False
    assert queue._job(job["id"])["state"] == "failed"
    await adapter.async_refresh()
    assert adapter.status()["complete"] is True
    assert queue._pending == []
    assert manual_block(queue, "fragile title")["uncertain"] is True
    for index in range(150):
        terminal = queue._new_job(f"Old {index}", "background")
        terminal["state"] = "completed"
        queue._jobs.append(terminal)
    queue._trim_history()
    trimmed = manual_block(queue, "fragile title")
    assert trimmed["reason"] == "unknown" and trimmed["uncertain"] is True
    changed = dict(adapter.settings)
    changed[const.CONF_LOOKAHEAD_DAYS] = 21
    await adapter.async_update_settings(changed)
    assert adapter.display_stale("calendar.school") is True
    assert adapter.automatic_sources() == []
    kept = manual_block(queue, "fragile title")
    assert kept["reason"] == "unknown" and kept["uncertain"] is True
    assert await queue.async_enqueue(["Fragile Title"]) == []
    with pytest.raises(ValueError, match="unknown outcome"):
        await queue.async_restore_auto("Fragile Title", kept["revision"], True)
    await adapter.async_shutdown()
    await queue.async_shutdown()


@pytest.mark.asyncio
async def test_sensor_and_card_pro_public_shape_preserved(ha, configured, monkeypatch):
    from test_library import picture

    events = {"calendar.school": {"events": [{"summary": "Shaped Title"}]}}
    adapter, queue, _ = behavior._calendar_test_setup(
        ha, configured, monkeypatch, events, enabled=True,
    )
    picture(ha, "shaped.png")
    await configured[0].async_remember("Shaped Title", "/local/activity_images/shaped.png")
    await adapter.async_refresh()
    assert set(adapter.status()) == {"calendars", "tracked_titles", "images", "last_scan",
                                     "error", "ignore_error", "automatic_generation",
                                     "complete", "stale"}
    assert isinstance(adapter.images, dict)
    rules = adapter.rules
    assert rules, "expected image and fallback Card Pro entries"
    for rule in rules:
        if rule.get("label_type") == "image":
            assert set(rule) == {"entity", "allowlist", "label", "label_type"}
            assert rule["allowlist"].startswith("^") and rule["allowlist"].endswith("$")
        else:
            assert set(rule) == {"entity", "blocklist", "label"}
    assert any(rule.get("label_type") == "image" for rule in rules)
    assert any("blocklist" in rule for rule in rules)
    assert ha.services.calls == []
    await adapter.async_shutdown()
    await queue.async_shutdown()

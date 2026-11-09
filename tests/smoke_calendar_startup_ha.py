"""Isolated real-HA lifecycle regression checks; no live calendar or AI provider.

Run with HA Core 2026.11.0 installed: python tests/smoke_calendar_startup_ha.py
Kept in a subprocess because test_behavior replaces HA modules globally.
"""
from __future__ import annotations

import asyncio
import copy
import logging
import sys
import tempfile
import threading
from pathlib import Path
from typing import Any
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import atomicwrites  # noqa: E402
from homeassistant.config_entries import ConfigEntries  # noqa: E402
from homeassistant.const import EVENT_HOMEASSISTANT_STARTED, __version__  # noqa: E402
from homeassistant.core import CoreState, HomeAssistant, SupportsResponse  # noqa: E402
from homeassistant.helpers import device_registry as dr  # noqa: E402
from homeassistant.helpers import entity_registry as er  # noqa: E402
from homeassistant.helpers import storage as ha_storage  # noqa: E402
from homeassistant.helpers.storage import Store  # noqa: E402
from homeassistant.util.file import WriteError, write_utf8_file_atomic  # noqa: E402

from custom_components.activity_images import calendar_adapter as module  # noqa: E402
from custom_components.activity_images.const import CONF_AUTO_CALENDARS  # noqa: E402

ENTITY = "calendar.school"
URL = "/local/activity_images/existing.png"


class Cache:
    def __init__(self):
        self.images = {"Old event": URL}
        self.entered: asyncio.Event | None = None
        self.release: asyncio.Event | None = None

    async def async_find_many(self, titles):
        if self.entered:
            self.entered.set()
            assert self.release is not None
            await self.release.wait()
        return {title: self.images[title] for title in titles if title in self.images}

    async def async_find(self, title, **kwargs):
        return (await self.async_find_many([title])).get(title)


class Queue:
    def __init__(self):
        self.calls = []

    async def async_enqueue(self, titles, *, calendar_sources=None):
        self.calls.append(titles)


class Timers:
    """Keep real cancellable HA timers, advancing only retry callbacks manually."""
    def __init__(self):
        self.entries = []
        self.native = module.async_call_later

    def later(self, hass, delay, action):
        cancel = self.native(hass, delay, action)
        record = {"delay": delay, "action": action, "cancelled": False}
        self.entries.append(record)

        def stop():
            record["cancelled"] = True
            cancel()

        record["cancel"] = stop
        return stop

    async def fire(self, hass):
        record = self.entries[-1]
        assert not record["cancelled"]
        record["cancel"]()
        record["action"](None)
        await hass.async_block_till_done()


class Warnings(logging.Handler):
    def __init__(self):
        super().__init__(logging.WARNING)
        self.messages = []

    def emit(self, record):
        self.messages.append(record.getMessage())


def adapter(hass, cache=None, queue=None, auto=False) -> Any:
    registry = er.async_get(hass)
    entry = registry.async_get(ENTITY) or registry.async_get_or_create(
        "calendar", "test", "school", suggested_object_id="school",
    )
    settings = {
        module.CONF_CALENDARS: [ENTITY], CONF_AUTO_CALENDARS: [ENTITY] if auto else [],
        module.CONF_LOOKAHEAD_DAYS: 30, module.CALENDAR_BINDINGS: {ENTITY: entry.id},
    }
    original = copy.deepcopy(settings)
    fake_cache: Any = cache or Cache()
    fake_queue: Any = queue or Queue()
    result: Any = module.ActivityCalendarAdapter(hass, fake_cache, fake_queue, settings)
    assert settings == original
    return result


def register(hass, service):
    hass.services.async_register(
        "calendar", "get_events", service, supports_response=SupportsResponse.ONLY
    )


def clean(hass, subject, timers):
    assert not subject._unsubscribers
    assert not subject._listeners
    assert subject._retry_cancel is None
    assert all(entry["cancelled"] for entry in timers.entries)
    assert subject._scan_task is None or subject._scan_task.done()
    assert subject._reconcile_task is None or subject._reconcile_task.done()
    assert subject._snapshot_task is None or subject._snapshot_task.done()
    assert EVENT_HOMEASSISTANT_STARTED not in hass.bus.async_listeners()
    assert hass.bus.async_listeners().get("state_changed", 0) == hass.data["smoke_registry_state_listeners"]
    assert not any(
        not handle.cancelled()
        and "_TrackTimeInterval" in repr(handle) and "ActivityCalendarAdapter" in repr(handle)
        for handle in hass.loop._scheduled
    )


async def lifecycle_and_recovery(hass):
    calls = []
    response = {ENTITY: {"events": [{"summary": "Old event"}]}}

    async def service(call):
        calls.append(call.data)
        return response

    register(hass, service)
    hass.states.async_set(ENTITY, "off")
    timers = Timers()
    with patch.object(module, "async_call_later", timers.later):
        subject = adapter(hass)
        notifications = []
        subject.add_listener(lambda: notifications.append(subject.status()))
        await subject.async_start()
        await subject.async_start()  # Idempotent listener registration.
        await subject.async_refresh()
        subject._schedule_scan()
        await hass.async_block_till_done()
        assert calls == [] and subject._scan_task is None
        assert len(subject._unsubscribers) == 2 and subject._state_unsubscribe is not None
        await hass.async_start()  # Actual HA transition and async_at_started dispatch.
        await hass.async_block_till_done()
        assert len(calls) == 1, (calls, subject.status())
        assert subject.images == {"Old event": URL}
        rules = subject.rules
        response = {}  # HA may briefly match none of the requested entities.
        await subject.async_refresh()
        assert subject.images == {"Old event": URL} and subject.rules == rules
        assert subject.last_error and timers.entries[-1]["delay"] == 5
        for _ in range(20):
            subject._schedule_scan()
        await subject.async_refresh()
        assert len(calls) == 2  # State/interval/manual calls join the retry window.
        response = {ENTITY: {"events": [{"summary": "Recovered"}]}}
        subject.cache.images["Recovered"] = URL
        await timers.fire(hass)
        assert subject.calendar_titles == {ENTITY: ["Recovered"]}
        assert subject.last_error is None and subject._retry_count == 0
        assert subject.queue.calls == []  # Automatic generation remains disabled.
        await subject.async_refresh()  # Normal refresh still works after recovery.
        assert len(calls) == 4
        await subject.async_shutdown()
        before = len(calls), len(notifications), subject.status()
        await subject.async_refresh()
        await subject.async_reconcile()
        subject.image_saved("ignored", URL)
        subject._at_started(hass)  # Already-dispatched start callback is harmless.
        subject._retry_scan(None)
        await hass.async_block_till_done()
        assert (len(calls), len(notifications), subject.status()) == before
        clean(hass, subject, timers)


async def bounded_retries_and_readiness(hass):
    calls = []
    failure = "Temporary failure"

    async def service(call):
        calls.append(call)
        raise RuntimeError(failure)

    register(hass, service)
    hass.states.async_remove(ENTITY)
    await hass.async_block_till_done()
    timers = Timers()
    warnings = Warnings()
    module._LOGGER.addHandler(warnings)
    try:
        with patch.object(module, "async_call_later", timers.later):
            subject = adapter(hass)
            await subject.async_start()
            await hass.async_block_till_done()
            assert calls == [] and "not ready" in subject.last_error
            assert warnings.messages == []
            hass.states.async_set(ENTITY, "unavailable")
            await timers.fire(hass)
            assert calls == []
            hass.states.async_set(ENTITY, "off")
            await timers.fire(hass)
            await timers.fire(hass)
            assert [entry["delay"] for entry in timers.entries] == [5, 15, 30]
            assert len(calls) == 2 and len(warnings.messages) == 1
            for _ in range(3):
                await subject.async_refresh()
            assert len(timers.entries) == 3 and len(warnings.messages) == 1
            failure = "Changed failure"
            await subject.async_refresh()
            assert len(warnings.messages) == 2
            assert subject.queue.calls == []
            await subject.async_shutdown()
            clean(hass, subject, timers)

            hass.services.async_remove("calendar", "get_events")
            unavailable = adapter(hass)
            await unavailable.async_start()
            await hass.async_block_till_done()
            assert unavailable.last_error == "ValueError: calendar.get_events is not ready"
            await unavailable.async_shutdown()  # Pending native retry timer is cancelled.
            clean(hass, unavailable, timers)
    finally:
        module._LOGGER.removeHandler(warnings)


async def coalescing_and_shutdown(hass):
    entered, release = asyncio.Event(), asyncio.Event()
    calls = []

    async def service(call):
        calls.append(call)
        entered.set()
        await release.wait()
        return {ENTITY: {"events": [{"summary": "New event"}]}}

    register(hass, service)
    hass.states.async_set(ENTITY, "off")
    subject = adapter(hass)
    await subject.async_start()  # Reload while already running schedules immediately.
    await entered.wait()
    direct = [asyncio.create_task(subject.async_refresh()) for _ in range(10)]
    for index in range(20):
        hass.states.async_set(ENTITY, "off", {"revision": index})
    await asyncio.sleep(0)
    release.set()
    await asyncio.gather(*direct)
    await hass.async_block_till_done()
    assert len(calls) == 2  # One dirty follow-up, not one scan per caller/event.
    await subject.async_shutdown()

    # Unload during a public action's service await, including a late provider result.
    entered.clear()
    release.clear()
    subject = adapter(hass, auto=True)
    direct = asyncio.create_task(subject.async_refresh())
    await entered.wait()
    await subject.async_shutdown()
    await asyncio.gather(direct, return_exceptions=True)
    release.set()  # HA service work can outlive the caller; it must not commit.
    await hass.async_block_till_done()
    assert subject.calendar_titles == {} and subject.images == {}
    assert subject.queue.calls == [] and subject.last_scan is None

    # Direct reconciliation isn't an owned task: guard after its cache await too.
    cache = Cache()
    subject = adapter(hass, cache)
    subject.calendar_titles = {ENTITY: ["Old event"]}
    cache.entered, cache.release = asyncio.Event(), asyncio.Event()
    direct = asyncio.create_task(subject.async_reconcile())
    await cache.entered.wait()
    await subject.async_shutdown()
    cache.release.set()
    await direct
    assert subject.images == {}


async def persistence_and_prestart_unload(hass):
    hass.set_state(CoreState.not_running)
    timers = Timers()
    with patch.object(module, "async_call_later", timers.later):
        subject = adapter(hass)
        await subject._store.async_save({
            ENTITY: [None, " Old event ", "Old event", "", "x" * 256],
            "calendar.removed": ["Must not restore"],
        })
        await subject.async_start()
        assert subject.calendar_titles == {ENTITY: ["Old event"]}
        assert subject.images == {"Old event": URL}
        assert subject.last_scan is None  # A restored snapshot isn't a fresh scan.
        assert subject._snapshot_titles({ENTITY: [str(n) for n in range(100)]}) == {
            ENTITY: [str(n) for n in range(64)]
        }
        await subject.async_shutdown()
        await hass.async_start()
        await hass.async_block_till_done()
        assert subject._scan_task is None
        clean(hass, subject, timers)

        # A real Store reload retains the usable snapshot through startup failure.
        subject = adapter(hass)
        await subject.async_start()
        await hass.async_block_till_done()
        assert subject.images == {"Old event": URL}
        assert subject.last_error

        async def recovered(call):
            return {ENTITY: {"events": [{"summary": "Old event"}, {"summary": "Later"}]}}

        register(hass, recovered)
        await timers.fire(hass)
        assert subject.calendar_titles == {ENTITY: ["Old event", "Later"]}
        assert subject.last_error is None and subject.queue.calls == []
        await subject.async_shutdown()
        clean(hass, subject, timers)


async def shutdown_during_cache_and_start(hass):
    async def service(call):
        return {ENTITY: {"events": [{"summary": "New event"}]}}

    register(hass, service)
    cache = Cache()
    cache.entered, cache.release = asyncio.Event(), asyncio.Event()
    subject = adapter(hass, cache, auto=True)
    direct = asyncio.create_task(subject.async_refresh())
    await cache.entered.wait()
    await subject.async_shutdown()
    cache.release.set()
    await asyncio.gather(direct, return_exceptions=True)
    assert subject.images == {} and subject.calendar_titles == {}
    assert subject.queue.calls == []

    cache = Cache()
    cache.entered, cache.release = asyncio.Event(), asyncio.Event()
    subject = adapter(hass, cache)
    starting = asyncio.create_task(subject.async_start())
    await cache.entered.wait()
    await subject.async_shutdown()
    cache.release.set()
    await starting
    assert subject.images == {} and subject.calendar_titles == {}
    assert subject._unsubscribers == [] and subject._scan_task is None


async def image_saved_during_first_snapshot(hass, *, unload=False):
    entered, release = asyncio.Event(), asyncio.Event()
    calls = []
    cache = Cache()
    subject = adapter(hass, cache)

    async def service(call):
        calls.append(call)
        return {ENTITY: {"events": [{"summary": "New event"}]}}

    register(hass, service)
    save = subject._store.async_save

    async def blocked_save(data):
        entered.set()
        await release.wait()
        await save(data)

    with patch.object(subject._store, "async_save", blocked_save):
        shutdown = None
        refreshing = asyncio.create_task(subject.async_refresh())
        await entered.wait()  # First lookup found no image; titles aren't committed yet.
        assert subject.calendar_titles == {} and subject.images == {}
        cache.images["New event"] = URL  # Generator has persisted its completed result.
        subject.image_saved("New event", URL)
        assert subject._reconcile_task is not None
        await asyncio.sleep(0)  # Reconciliation waits for the scan's lock.
        assert not subject._reconcile_task.done()
        if unload:
            shutdown = asyncio.create_task(subject.async_shutdown())
            await asyncio.gather(refreshing, return_exceptions=True)
            assert subject._closed and not shutdown.done()
        release.set()
        await asyncio.gather(refreshing, return_exceptions=True)
        if shutdown:
            await shutdown
        await hass.async_block_till_done()
        assert len(calls) == 1  # Reconciliation never re-queries the calendar provider.
        if unload:
            assert subject.images == {} and subject.calendar_titles == {}
        else:
            assert subject.images == {"New event": URL}
            assert subject.calendar_titles == {ENTITY: ["New event"]}
        assert subject.queue.calls == []
        await subject.async_shutdown()
        subject.image_saved("New event", URL)
        await hass.async_block_till_done()
        clean(hass, subject, Timers())


async def snapshot_executor_write_drained_before_reload(hass):
    entered, release = asyncio.Event(), threading.Event()
    loop = asyncio.get_running_loop()
    subject = adapter(hass, auto=True)
    notifications = []
    subject.add_listener(lambda: notifications.append(subject.status()))
    replace = atomicwrites.replace_atomic

    def blocked_replace(source, target):
        if str(target) == subject._store.path:
            assert Path(source).is_file()  # Real executor write reached atomic replacement.
            loop.call_soon_threadsafe(entered.set)
            assert release.wait(timeout=10), "Executor barrier was not released"
        replace(source, target)

    async def events_a(call):
        return {ENTITY: {"events": [{"summary": "Snapshot A"}]}}

    register(hass, events_a)
    with patch.object(atomicwrites, "replace_atomic", blocked_replace):
        refreshing = asyncio.create_task(subject.async_refresh())
        shutdown = None
        try:
            await asyncio.wait_for(entered.wait(), timeout=5)
            assert subject._snapshot_task and not subject._snapshot_task.done()
            shutdown = asyncio.create_task(subject.async_shutdown())
            await asyncio.gather(refreshing, return_exceptions=True)
            # Scan cancellation is complete, but the writer is still in its thread.
            assert subject._closed and subject._scan_task.done()
            assert not shutdown.done()
            assert not subject._snapshot_task.done()
        finally:
            release.set()
            if shutdown:
                await shutdown
            else:
                await subject.async_shutdown()
            await asyncio.gather(refreshing, return_exceptions=True)
    assert subject.images == {} and subject.calendar_titles == {}
    assert subject.last_scan is None and subject.queue.calls == [] and notifications == []
    clean(hass, subject, Timers())

    async def events_b(call):
        return {ENTITY: {"events": [{"summary": "Snapshot B"}]}}

    register(hass, events_b)
    reloaded = adapter(hass)
    await reloaded.async_start()
    await hass.async_block_till_done()
    assert reloaded.calendar_titles == {ENTITY: ["Snapshot B"]}
    await reloaded.async_shutdown()
    clean(hass, reloaded, Timers())
    fresh_store = Store(hass, 1, f"{module.DOMAIN}_calendar_snapshot", atomic_writes=True)
    restored = await fresh_store.async_load()
    assert isinstance(restored, dict)
    assert restored[ENTITY] == ["Snapshot B"]
    assert restored["_raw_occurrences"][ENTITY][0]["raw_summary"] == "Snapshot B"
    binding = er.async_get(hass).async_get(ENTITY)
    assert binding is not None and restored[module.CALENDAR_BINDINGS][ENTITY] == binding.id


async def swallowed_snapshot_write_failure_preserves_last_good(hass):
    import json
    subject = adapter(hass, auto=True)
    timers = Timers()
    response = {ENTITY: {"events": [{"summary": "Old event"}]}}
    async def events(call):
        return response
    register(hass, events)
    with patch.object(module, "async_call_later", timers.later):
        await subject.async_refresh()
        previous_titles, previous_images = copy.deepcopy(subject.calendar_titles), dict(subject.images)
        previous_scan = subject.last_scan
        previous_snapshot = copy.deepcopy(subject._snapshot())
        assert previous_scan and previous_titles == {ENTITY: ["Old event"]}
        response = {ENTITY: {"events": [{"summary": "Uncommitted event"}]}}
        native = write_utf8_file_atomic
        def fail_native(filename, *args, **kwargs):
            if filename == subject._store.path:
                raise WriteError("Injected calendar disk failure")
            return native(filename, *args, **kwargs)
        with patch.object(ha_storage, "write_utf8_file_atomic", fail_native):
            status = await subject.async_refresh()
        assert subject.calendar_titles == previous_titles
        assert subject.images == previous_images and subject.last_scan == previous_scan
        assert "persistence verification failed" in status["error"]
        assert subject.queue.calls == []  # Cached work and a failed commit never enqueue.
        envelope = await hass.async_add_executor_job(lambda: json.loads(Path(subject._store.path).read_text()))
        assert envelope["data"] == previous_snapshot
        await subject.async_shutdown()
        clean(hass, subject, timers)


async def run():
    assert __version__ == "2026.11.0", __version__
    with tempfile.TemporaryDirectory(prefix="ha-calendar-smoke-") as directory:
        hass = HomeAssistant(directory)
        dr.async_setup(hass)
        await dr.async_load(hass)
        hass.config_entries = ConfigEntries(hass, {})
        await hass.config_entries.async_initialize()
        await er.async_load(hass)
        registered = er.async_get(hass).async_get_or_create("calendar", "test", "school", suggested_object_id="school")
        assert registered.entity_id == ENTITY
        hass.data["smoke_registry_state_listeners"] = hass.bus.async_listeners().get("state_changed", 0)
        await lifecycle_and_recovery(hass)
        await bounded_retries_and_readiness(hass)
        await coalescing_and_shutdown(hass)
        await shutdown_during_cache_and_start(hass)
        await image_saved_during_first_snapshot(hass)
        await image_saved_during_first_snapshot(hass, unload=True)
        await snapshot_executor_write_drained_before_reload(hass)
        await swallowed_snapshot_write_failure_preserves_last_good(hass)
        hass.services.async_remove("calendar", "get_events")
        await persistence_and_prestart_unload(hass)
        await hass.async_block_till_done()
        assert not any(
            task is not asyncio.current_task() and not task.done()
            and "ActivityCalendarAdapter" in getattr(task.get_coro(), "__qualname__", "")
            for task in asyncio.all_tasks()
        )
        await hass.async_stop()
    print("Calendar startup/lifecycle smoke passed (real HA 2026.11.0; no provider calls)")


if __name__ == "__main__":
    asyncio.run(run())

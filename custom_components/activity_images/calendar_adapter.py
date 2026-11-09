"""Calendar-to-image adapter using Home Assistant's native calendar.get_events action.

No dependency on Home Tasks or calendar-card internals. Selection and generation
are opt-in; the adapter polls upcoming events and watches state changes.
"""
from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Callable, Mapping
from contextlib import suppress
from datetime import timedelta

from homeassistant.core import CoreState, HomeAssistant, callback
from homeassistant.helpers.event import (
    async_call_later,
    async_track_state_change_event,
    async_track_time_interval,
)
from homeassistant.helpers.start import async_at_started
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import (
    CALENDAR_SCAN_MINUTES,
    CONF_AUTO_GENERATE,
    CONF_CALENDARS,
    CONF_LOOKAHEAD_DAYS,
    DEFAULT_LOOKAHEAD_DAYS,
    DOMAIN,
    MAX_TRACKED_TITLES,
)
from .image_cache import ActivityImageCache
from .image_queue import ActivityImageQueue
from .storage import async_save_verified

_LOGGER = logging.getLogger(__name__)
_RETRY_DELAYS = (5, 15, 30)
_FALLBACK_ICON = "mdi:calendar-outline"
_REGEX_META = re.compile(r"[.*+?^${}()|\[\]\\]")


def escape_event_title(title: str) -> str:
    """Escape only JavaScript RegExp special characters (including backslash)."""
    return _REGEX_META.sub(lambda match: "\\" + match.group(0), title)


class ActivityCalendarAdapter:
    """Keep a bounded, reactive map from future event summaries to image URLs."""

    def __init__(
        self,
        hass: HomeAssistant,
        cache: ActivityImageCache,
        queue: ActivityImageQueue,
        settings: Mapping,
    ) -> None:
        self.hass = hass
        self.cache = cache
        self.queue = queue
        self.calendars: tuple[str, ...] = tuple(settings.get(CONF_CALENDARS) or ())
        self.lookahead_days = int(settings.get(CONF_LOOKAHEAD_DAYS, DEFAULT_LOOKAHEAD_DAYS))
        self.auto_generate = bool(settings.get(CONF_AUTO_GENERATE, False))
        self.calendar_titles: dict[str, list[str]] = {}
        self.images: dict[str, str] = {}
        self.last_scan: str | None = None
        self.last_error: str | None = None
        self._listeners: set[Callable[[], None]] = set()
        self._unsubscribers: list[Callable[[], None]] = []
        self._state_unsubscribe: Callable[[], None] | None = None
        self._lock = asyncio.Lock()
        self._scan_task: asyncio.Task | None = None
        self._reconcile_task: asyncio.Task | None = None
        self._snapshot_task: asyncio.Task | None = None
        self._rescan_needed = False
        self._generation_suppressed = 0
        self._demand_truncated = False
        self._reconcile_needed = False
        self._closed = False
        self._start_registered = False
        self._retry_cancel: Callable[[], None] | None = None
        self._retry_count = 0
        self._warned_error: str | None = None
        self._store = Store(hass, 1, f"{DOMAIN}_calendar_snapshot", atomic_writes=True)

    @callback
    def add_listener(self, listener: Callable[[], None]) -> Callable[[], None]:
        self._listeners.add(listener)
        return lambda: self._listeners.discard(listener)

    @callback
    def _notify(self) -> None:
        if not self._closed:
            for listener in tuple(self._listeners):
                try:
                    listener()
                except Exception:
                    _LOGGER.exception("Activity Images sensor listener failed")

    @property
    def rules(self) -> list[dict]:
        """Pre-escaped native Calendar Card Pro entity entries, including fallback."""
        rules: list[dict] = []
        for entity, titles in self.calendar_titles.items():
            patterns: list[str] = []
            for title in titles:
                if url := self.images.get(title):
                    pattern = "^" + escape_event_title(title) + "$"
                    patterns.append(pattern)
                    rules.append({
                        "entity": entity, "allowlist": pattern,
                        "label": url, "label_type": "image",
                    })
            rules.append({
                "entity": entity,
                "blocklist": "|".join(patterns) if patterns else "(?!)",
                "label": _FALLBACK_ICON,
            })
        return rules

    @callback
    def _schedule_scan(self, _event=None) -> None:
        if (self._closed or not self.calendars
                or self.hass.state is not CoreState.running or self._retry_cancel):
            return
        if self._scan_task and not self._scan_task.done():
            self._rescan_needed = True
            return
        self._scan_task = self.hass.async_create_task(self._drain_scans())

    async def _drain_scans(self) -> None:
        while not self._closed:
            self._rescan_needed = False
            await self._async_refresh_once()
            if not self._rescan_needed or self._retry_cancel:
                break

    @callback
    def _retry_scan(self, _now) -> None:
        self._retry_cancel = None
        self._schedule_scan()

    @callback
    def _scan_failed(self, exc: Exception) -> None:
        self.last_error = f"{type(exc).__name__}: {str(exc)[:200]}"
        self._rescan_needed = False
        if self._retry_count < len(_RETRY_DELAYS):
            delay = _RETRY_DELAYS[self._retry_count]
            self._retry_count += 1
            self._retry_cancel = async_call_later(self.hass, delay, self._retry_scan)
        elif self._warned_error != self.last_error:
            _LOGGER.warning("Unable to refresh Activity Images calendars: %s", self.last_error)
            self._warned_error = self.last_error
        self._notify()

    def _snapshot_titles(self, snapshot: object) -> dict[str, list[str]]:
        """Restore only bounded titles for calendars still explicitly selected."""
        if not isinstance(snapshot, dict):
            return {}
        parsed: dict[str, list[str]] = {}
        seen: set[str] = set()
        for entity in self.calendars:
            values = snapshot.get(entity)
            if not isinstance(values, list):
                continue
            titles: list[str] = []
            for value in values[:512]:
                if not isinstance(value, str):
                    continue
                title = value.strip()
                if (not title or len(title) > 255 or title in titles
                        or (title not in seen and len(seen) >= MAX_TRACKED_TITLES)):
                    continue
                titles.append(title)
                seen.add(title)
            parsed[entity] = titles
        return parsed

    @callback
    def _at_started(self, _hass: HomeAssistant) -> None:
        self._schedule_scan()

    async def async_start(self) -> None:
        """Restore the last snapshot, then defer optional scans until HA is running."""
        if not self.calendars or self._closed or self._start_registered:
            return
        self._start_registered = True
        async with self._lock:
            try:
                parsed = self._snapshot_titles(await self._store.async_load())
                images = await self.cache.async_find_many(list(dict.fromkeys(
                    title for titles in parsed.values() for title in titles
                )))
                if self._closed:
                    return
                self.calendar_titles = parsed
                self.images = images
                self._notify()
            except Exception:
                _LOGGER.debug("Unable to restore Activity Images calendar snapshot", exc_info=True)
        if self._closed:
            return
        self._unsubscribers.append(
            async_track_time_interval(
                self.hass, self._schedule_scan,
                timedelta(minutes=CALENDAR_SCAN_MINUTES),
            )
        )
        self._state_unsubscribe = async_track_state_change_event(
            self.hass, self.calendars, self._schedule_scan,
        )
        self._unsubscribers.append(async_at_started(self.hass, self._at_started))

    async def async_update_settings(self, settings) -> None:
        """Apply the latest options between scans, without stopping accepted jobs."""
        async with self._lock:
            if self._closed:
                return
            calendars = tuple(settings.get(CONF_CALENDARS) or ())
            days = int(settings.get(CONF_LOOKAHEAD_DAYS, DEFAULT_LOOKAHEAD_DAYS))
            automatic = bool(settings.get(CONF_AUTO_GENERATE, False))
            selection_changed = calendars != self.calendars
            demand_changed = selection_changed or days != self.lookahead_days
            enable_generation = automatic and not self.auto_generate
            if not demand_changed and automatic == self.auto_generate:
                return
            self.calendars, self.lookahead_days, self.auto_generate = calendars, days, automatic
            if self._retry_cancel:
                self._retry_cancel()
                self._retry_cancel = None
            self._retry_count = 0
            self._warned_error = None
            if demand_changed:
                self.last_scan = None
                self.last_error = None
                self._demand_truncated = False
            if selection_changed:
                self.calendar_titles = self._snapshot_titles(self.calendar_titles)
                wanted = {title for titles in self.calendar_titles.values() for title in titles}
                self.images = {title: url for title, url in self.images.items() if title in wanted}
                if self._state_unsubscribe:
                    self._state_unsubscribe()
                    self._state_unsubscribe = None
                if self._start_registered and calendars:
                    self._state_unsubscribe = async_track_state_change_event(
                        self.hass, calendars, self._schedule_scan,
                    )
                self._snapshot_task = self.hass.async_create_task(
                    async_save_verified(self.hass, self._store, self.calendar_titles)
                )
                try:
                    await asyncio.shield(self._snapshot_task)
                except Exception as exc:
                    self.last_error = f"Unable to save calendar snapshot: {exc}"
                    _LOGGER.warning("Unable to save updated calendar selection", exc_info=True)
            self._notify()
        if self._closed:
            return
        if not self._start_registered:
            await self.async_start()
        elif demand_changed or enable_generation:
            self._schedule_scan()

    async def async_refresh(self) -> dict:
        """Join the owned scan; public actions must obey the same lifecycle guards."""
        if self._closed or self.hass.state is not CoreState.running:
            return self.status()
        if not self._scan_task or self._scan_task.done():
            self._schedule_scan()
        if self._scan_task and not self._scan_task.done():
            await asyncio.shield(self._scan_task)
        return self.status()

    async def async_refresh_for_deletion(self) -> dict:
        """Verify live demand without authorizing automatic missing-image jobs."""
        self._generation_suppressed += 1
        refresh = asyncio.create_task(self.async_refresh())
        cancelled = False
        try:
            # A cancelled admin request must not leave its scan free to enqueue.
            while not refresh.done():
                try:
                    await asyncio.shield(refresh)
                except asyncio.CancelledError:
                    cancelled = True
            result = await refresh
            if cancelled:
                raise asyncio.CancelledError
            if self._demand_truncated:
                raise ValueError("Calendar demand exceeded 64 distinct titles or 512 events; permanent deletion is blocked")
            return result
        finally:
            self._generation_suppressed -= 1

    async def _async_refresh_once(self) -> dict:
        """Read a 30-day by-default window, group by calendar, resolve, then queue."""
        async with self._lock:
            if (self._closed or not self.calendars
                    or self.hass.state is not CoreState.running):
                return self.status()

            now = dt_util.now()
            end = now + timedelta(days=self.lookahead_days)
            try:
                if not self.hass.services.has_service("calendar", "get_events"):
                    raise ValueError("calendar.get_events is not ready")
                for entity in self.calendars:
                    state = self.hass.states.get(entity)
                    if state is None or state.state in ("unknown", "unavailable"):
                        raise ValueError(f"Calendar is not ready: {entity}")
                response = await self.hass.services.async_call(
                    "calendar", "get_events", {
                        "entity_id": list(self.calendars),
                        "start_date_time": now.isoformat(),
                        "end_date_time": end.isoformat(),
                    },
                    blocking=True, return_response=True,
                )
                if self._closed:
                    return self.status()
                if not isinstance(response, dict):
                    raise ValueError("calendar.get_events did not return a calendar mapping")
                parsed: dict[str, list[str]] = {}
                seen: set[str] = set()
                truncated = False
                for entity in self.calendars:
                    if entity not in response:
                        raise ValueError(f"No calendar event data returned for {entity}")
                    payload = response[entity]
                    if not isinstance(payload, dict):
                        raise ValueError(f"Invalid event response for {entity}")
                    events = payload.get("events", [])
                    if not isinstance(events, list):
                        raise ValueError(f"Invalid event response for {entity}")
                    if len(events) > 512:
                        truncated = True
                    titles: list[str] = []
                    for event in events[:512]:
                        if not isinstance(event, dict) or event.get("status") == "cancelled":
                            continue
                        title = event.get("summary")
                        if not isinstance(title, str):
                            continue
                        title = title.strip()
                        if not title or len(title) > 255:
                            continue
                        if title not in seen and len(seen) >= MAX_TRACKED_TITLES:
                            truncated = True
                            continue
                        if title not in titles:
                            titles.append(title)
                            seen.add(title)
                    parsed[entity] = titles

                wanted = list(dict.fromkeys(
                    title for titles in parsed.values() for title in titles
                ))
                if self._generation_suppressed:
                    images = {}
                    for title in wanted:
                        if url := await self.cache.async_find(title, touch=False, prune=False):
                            images[title] = url
                else:
                    images = await self.cache.async_find_many(wanted)
                if self._closed:
                    return self.status()
                self._snapshot_task = self.hass.async_create_task(async_save_verified(self.hass, self._store, parsed))
                await asyncio.shield(self._snapshot_task)
                if self._closed:
                    return self.status()
                self._retry_count = 0
                self._warned_error = None
                self.calendar_titles = parsed
                self._demand_truncated = truncated
                self.images = images
                self.last_scan = now.isoformat()
                self.last_error = None
                self._notify()
                if self.auto_generate and not self._closed and not self._generation_suppressed:
                    missing = [title for title in wanted if title not in images]
                    await self.queue.async_enqueue(missing)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                # Keep last successful map during network/provider/calendar outages.
                if not self._closed:
                    self._scan_failed(exc)
            return self.status()

    @callback
    def image_saved(self, _title: str, _url: str) -> None:
        """Triggered after cache persistence; never rescan calendars for new PNGs."""
        if self._closed:
            return
        if self._reconcile_task and not self._reconcile_task.done():
            self._reconcile_needed = True
            return
        self._reconcile_task = self.hass.async_create_task(self._drain_reconciles())

    async def _drain_reconciles(self) -> None:
        while not self._closed:
            self._reconcile_needed = False
            await self.async_reconcile()
            if not self._reconcile_needed:
                break

    async def async_reconcile(self) -> None:
        async with self._lock:
            if self._closed or not self.calendar_titles:
                return
            wanted = list(dict.fromkeys(
                title for titles in self.calendar_titles.values() for title in titles
            ))
            images = await self.cache.async_find_many(wanted)
            if not self._closed and images != self.images:
                self.images = images
                self._notify()

    def status(self) -> dict:
        return {
            "calendars": list(self.calendars),
            "tracked_titles": sum(len(items) for items in self.calendar_titles.values()),
            "images": len(self.images),
            "last_scan": self.last_scan,
            "error": self.last_error,
            "automatic_generation": self.auto_generate,
        }

    async def async_shutdown(self) -> None:
        """Stop polling and pending scans; never interrupt the generator itself."""
        self._closed = True
        if self._retry_cancel:
            self._retry_cancel()
            self._retry_cancel = None
        for unsub in self._unsubscribers:
            unsub()
        self._unsubscribers.clear()
        if self._state_unsubscribe:
            self._state_unsubscribe()
            self._state_unsubscribe = None
        for task in (self._scan_task, self._reconcile_task):
            if task and not task.done():
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task
        # Executor writes cannot be cancelled; finish this instance's write before reload.
        if self._snapshot_task:
            try:
                await asyncio.shield(self._snapshot_task)
            except Exception:
                _LOGGER.warning("Unable to save Activity Images calendar snapshot", exc_info=True)
        self._listeners.clear()

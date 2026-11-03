"""Calendar-to-image adapter using Home Assistant's native calendar.get_events action.

No dependency on Home Tasks or calendar-card internals. Selection and generation
are opt-in; the adapter polls upcoming events and watches state changes.
"""
from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import timedelta
import logging
import re

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.event import async_track_state_change_event, async_track_time_interval
from homeassistant.util import dt as dt_util

from .const import (
    CALENDAR_SCAN_MINUTES, CONF_AUTO_GENERATE, CONF_CALENDARS,
    CONF_LOOKAHEAD_DAYS, DEFAULT_LOOKAHEAD_DAYS, MAX_TRACKED_TITLES,
)
from .image_cache import ActivityImageCache
from .image_queue import ActivityImageQueue

_LOGGER = logging.getLogger(__name__)
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
        settings: dict,
    ) -> None:
        self.hass = hass
        self.cache = cache
        self.queue = queue
        self.calendars = tuple(settings.get(CONF_CALENDARS) or ())
        self.lookahead_days = int(settings.get(CONF_LOOKAHEAD_DAYS, DEFAULT_LOOKAHEAD_DAYS))
        self.auto_generate = bool(settings.get(CONF_AUTO_GENERATE, False))
        self.calendar_titles: dict[str, list[str]] = {}
        self.images: dict[str, str] = {}
        self.last_scan: str | None = None
        self.last_error: str | None = None
        self._listeners: set[Callable[[], None]] = set()
        self._unsubscribers: list[Callable[[], None]] = []
        self._lock = asyncio.Lock()
        self._scan_task: asyncio.Task | None = None
        self._reconcile_task: asyncio.Task | None = None
        self._rescan_needed = False
        self._reconcile_needed = False
        self._closed = False

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
        if self._closed or not self.calendars:
            return
        if self._scan_task and not self._scan_task.done():
            self._rescan_needed = True
            return
        self._scan_task = self.hass.async_create_task(self._drain_scans())

    async def _drain_scans(self) -> None:
        while not self._closed:
            self._rescan_needed = False
            await self.async_refresh()
            if not self._rescan_needed:
                break

    async def async_start(self) -> None:
        """Watch selected calendars only. Never generate before explicit opt-in."""
        if not self.calendars or self._closed:
            return
        self._unsubscribers.append(
            async_track_time_interval(
                self.hass, self._schedule_scan,
                timedelta(minutes=CALENDAR_SCAN_MINUTES),
            )
        )
        self._unsubscribers.append(
            async_track_state_change_event(
                self.hass, self.calendars, self._schedule_scan,
            )
        )
        await self.async_refresh()

    async def async_refresh(self) -> dict:
        """Read a 30-day by-default window, group by calendar, resolve, then queue."""
        async with self._lock:
            if self._closed:
                return self.status()
            if not self.calendars:
                return self.status()

            now = dt_util.now()
            end = now + timedelta(days=self.lookahead_days)
            try:
                response = await self.hass.services.async_call(
                    "calendar", "get_events", {
                        "entity_id": list(self.calendars),
                        "start_date_time": now.isoformat(),
                        "end_date_time": end.isoformat(),
                    },
                    blocking=True, return_response=True,
                )
                if not isinstance(response, dict):
                    raise ValueError("calendar.get_events did not return a calendar mapping")
                parsed: dict[str, list[str]] = {}
                seen: set[str] = set()
                for entity in self.calendars:
                    if entity not in response:
                        raise ValueError(f"No calendar event data returned for {entity}")
                    payload = response[entity]
                    if not isinstance(payload, dict) or not isinstance(payload.get("events", []), list):
                        raise ValueError(f"Invalid event response for {entity}")
                    titles: list[str] = []
                    for event in payload.get("events", [])[:512]:
                        if not isinstance(event, dict) or event.get("status") == "cancelled":
                            continue
                        title = event.get("summary")
                        if not isinstance(title, str):
                            continue
                        title = title.strip()
                        if not title or len(title) > 255:
                            continue
                        if title not in seen and len(seen) >= MAX_TRACKED_TITLES:
                            continue
                        if title not in titles:
                            titles.append(title)
                            seen.add(title)
                    parsed[entity] = titles

                wanted = list(dict.fromkeys(
                    title for titles in parsed.values() for title in titles
                ))
                images = await self.cache.async_find_many(wanted)
                self.calendar_titles = parsed
                self.images = images
                self.last_scan = now.isoformat()
                self.last_error = None
                self._notify()
                if self.auto_generate:
                    missing = [title for title in wanted if title not in images]
                    await self.queue.async_enqueue(missing)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                # Keep last successful map during network/provider/calendar outages.
                _LOGGER.warning("Unable to refresh Activity Images calendars: %s", exc)
                self.last_error = f"{type(exc).__name__}: {str(exc)[:200]}"
                self._notify()
            return self.status()

    @callback
    def image_saved(self, _title: str, _url: str) -> None:
        """Triggered after cache persistence; never rescan calendars for new PNGs."""
        if self._closed or not self.calendar_titles:
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
            if images != self.images:
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
        for unsub in self._unsubscribers:
            unsub()
        self._unsubscribers.clear()
        for task in (self._scan_task, self._reconcile_task):
            if task and not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        self._listeners.clear()

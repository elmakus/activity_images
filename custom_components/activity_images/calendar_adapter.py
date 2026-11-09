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
from datetime import datetime, timedelta

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
    CALENDAR_BINDINGS,
    CALENDAR_SCAN_MINUTES,
    CONF_CALENDARS,
    CONF_LOOKAHEAD_DAYS,
    DEFAULT_LOOKAHEAD_DAYS,
    DOMAIN,
    MAX_TRACKED_TITLES,
)
from .engine import GenerationSuppressed
from .image_cache import ActivityImageCache, normalize_title
from .image_queue import ActivityImageQueue
from .settings import calendar_identity
from .settings_schema import calendar_auto_ids
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
        self.settings = settings
        self.calendars: tuple[str, ...] = tuple(settings.get(CONF_CALENDARS) or ())
        self.lookahead_days = int(settings.get(CONF_LOOKAHEAD_DAYS, DEFAULT_LOOKAHEAD_DAYS))
        self.auto_calendars = calendar_auto_ids(settings)
        self.auto_generate = bool(self.auto_calendars)
        self.calendar_titles: dict[str, list[str]] = {}
        self.raw_occurrences: dict[str, list[dict]] = {}
        self.calendar_identities: dict[str, str | None] = {}
        self.ignored_titles: frozenset[str] = frozenset()
        self.ignore_revision = 0
        self._ignores_loaded = False
        self._ignore_error: str | None = None
        self._verified_config: tuple | None = None
        self._scan_in_progress = False
        self.queue.calendar_authorizer = self.async_authorize_generation
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
        self._ignore_store = Store(hass, 1, f"{DOMAIN}_ignored_titles", atomic_writes=True)

    async def async_setup(self) -> None:
        async with self._lock:
            await self._try_load_ignores_locked()

    async def _try_load_ignores_locked(self) -> None:
        try:
            await self._load_ignores_locked()
        except (ValueError, OSError) as exc:
            message = f"Ignored-title storage unavailable; automatic generation is blocked: {exc}"
            if message != self._ignore_error:
                _LOGGER.error("%s", message)
            self._ignore_error = message

    async def _load_ignores_locked(self) -> None:
        if self._ignores_loaded:
            return
        data = await self._ignore_store.async_load()
        if data is not None:
            if (not isinstance(data, dict) or type(data.get("revision")) is not int
                    or data["revision"] < 0 or not isinstance(data.get("titles"), list)
                    or len(data["titles"]) > 512
                    or any(not isinstance(t, str) or not 1 <= len(t) <= 1024 for t in data["titles"])):
                raise ValueError("Invalid ignored-title storage; automatic generation is blocked")
            self.ignored_titles = frozenset(data["titles"])
            self.ignore_revision = data["revision"]
        self._ignores_loaded = True
        self._ignore_error = None

    async def async_ignore(self, raw_summary: str, ignored: bool, revision: int) -> dict:
        """Persist exact HA summaries before acknowledging an authorization change."""
        if not isinstance(raw_summary, str) or not 1 <= len(raw_summary) <= 1024 or type(ignored) is not bool:
            raise ValueError("Invalid exact title")
        async with self._lock:
            await self._load_ignores_locked()
            if self._closed:
                raise ValueError("Activity Images is unloading")
            if type(revision) is not int or revision != self.ignore_revision:
                raise ValueError("Ignored titles changed elsewhere (revision conflict); refresh coverage before saving")
            titles = set(self.ignored_titles)
            titles.add(raw_summary) if ignored else titles.discard(raw_summary)
            if len(titles) > 512:
                raise ValueError("At most 512 exact ignored titles are supported")
            if titles != self.ignored_titles:
                next_revision = self.ignore_revision + 1
                write = asyncio.create_task(async_save_verified(self.hass, self._ignore_store, {
                    "titles": sorted(titles), "revision": next_revision,
                }))
                cancelled = False
                while not write.done():
                    try:
                        await asyncio.shield(write)
                    except asyncio.CancelledError:
                        cancelled = True
                await write
                # No await between publishing the new policy and its acknowledgement.
                self.ignored_titles = frozenset(titles)
                self.ignore_revision = next_revision
                self._notify()
                if cancelled:
                    raise asyncio.CancelledError
            return {"revision": self.ignore_revision, "ignored": raw_summary in self.ignored_titles}

    def _current_config(self) -> tuple:
        return (tuple(self.settings.get(CONF_CALENDARS) or ()),
                int(self.settings.get(CONF_LOOKAHEAD_DAYS, DEFAULT_LOOKAHEAD_DAYS)))

    def _policy_bindings(self) -> dict:
        bindings = self.settings.get(CALENDAR_BINDINGS, {})
        return bindings if isinstance(bindings, dict) else {}

    def coverage_stale(self) -> bool:
        if self._closed or self.last_error or self._verified_config != self._current_config() or not self.last_scan:
            return True
        scanned = datetime.fromisoformat(self.last_scan)
        if dt_util.now() - scanned > timedelta(minutes=CALENDAR_SCAN_MINUTES + 1):
            return True
        bindings = self._policy_bindings()
        return any((state := self.hass.states.get(entity)) is None
                   or state.state in ("unknown", "unavailable")
                   or entity not in bindings
                   or self.calendar_identities.get(entity) != bindings[entity]
                   or bindings[entity] != calendar_identity(self.hass, entity)
                   for entity in self.calendars)

    def automatic_sources(self, title: str | None = None) -> list[dict]:
        """Read current authorization without taking calendar after generator/queue."""
        if (not self._ignores_loaded or self._generation_suppressed or self._scan_in_progress
                or self._demand_truncated or self.coverage_stale()):
            return []
        selected = set(self.settings.get(CONF_CALENDARS) or ())
        automatic = set(calendar_auto_ids(self.settings)) & selected
        demands = dict.fromkeys((entity, event["raw_summary"])
                                for entity, events in self.raw_occurrences.items() if entity in automatic
                                and self.calendar_identities.get(entity) is not None
                                for event in events if event["raw_summary"] not in self.ignored_titles
                                and (title is None or normalize_title(event["raw_summary"]) == normalize_title(title)))
        return [{"calendar_entity_id": entity, "raw_summary": raw} for entity, raw in demands]

    async def async_authorize_generation(self, title: str, sources: list[dict]) -> None:
        """Recheck after cache I/O, immediately before each new paid invocation."""
        if not any(source in self.automatic_sources(title) for source in sources):
            raise GenerationSuppressed("Calendar automatic generation is no longer authorized or needs a fresh complete scan")
        cached = await self.cache.async_find(title, touch=False, prune=False)
        if cached or self.cache.reserved(title):
            raise GenerationSuppressed("An existing image or deletion reservation prevents automatic generation")
        if not any(source in self.automatic_sources(title) for source in sources):
            raise GenerationSuppressed("Calendar authorization changed while waiting")

    def _snapshot(self) -> dict:
        # Additive envelope: older versions still see the original per-calendar map.
        return {**self.calendar_titles, "_raw_occurrences": self.raw_occurrences,
                "_calendar_registry_ids": self.calendar_identities, "_coverage_version": 1}

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
    def _schedule_scan(self, _event=None, *, allow_generation=True) -> None:
        if (self._closed or not self.calendars
                or self.hass.state is not CoreState.running or self._retry_cancel):
            return
        if self._scan_task and not self._scan_task.done():
            self._rescan_needed = True
            self._rescan_allow_generation = self._rescan_allow_generation or allow_generation
            return
        self._scan_task = self.hass.async_create_task(self._drain_scans(allow_generation))

    async def _drain_scans(self, allow_generation=True) -> None:
        while not self._closed:
            self._rescan_needed = False
            self._rescan_allow_generation = False
            await self._async_refresh_once(allow_generation=allow_generation)
            if not self._rescan_needed or self._retry_cancel:
                break
            allow_generation = self._rescan_allow_generation

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
                snapshot = await self._store.async_load()
                parsed = self._snapshot_titles(snapshot)
                raw = snapshot.get("_raw_occurrences", {}) if isinstance(snapshot, dict) else {}
                self.raw_occurrences = {
                    entity: [event for event in events[:512] if isinstance(event, dict)
                             and isinstance(event.get("raw_summary"), str)
                             and 1 <= len(event["raw_summary"]) <= 1024]
                    for entity, events in raw.items() if entity in self.calendars and isinstance(events, list)
                } if isinstance(raw, dict) else {}
                # Paid identity belongs to ConfigEntry options, never a replaceable display snapshot.
                self.calendar_identities = dict(self._policy_bindings())
                # Restored data is display-only, even when it includes raw titles.
                self._verified_config = None
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
            auto_calendars = calendar_auto_ids(settings)
            selection_changed = calendars != self.calendars
            demand_changed = selection_changed or days != self.lookahead_days
            enable_generation = bool(set(auto_calendars) - set(self.auto_calendars))
            self.settings = settings
            if not demand_changed and auto_calendars == self.auto_calendars:
                return
            self.calendars, self.lookahead_days = calendars, days
            self.auto_calendars, self.auto_generate = auto_calendars, bool(auto_calendars)
            if self._retry_cancel:
                self._retry_cancel()
                self._retry_cancel = None
            self._retry_count = 0
            self._warned_error = None
            if demand_changed:
                self.last_scan = None
                self.last_error = None
                self._verified_config = None
                self._demand_truncated = False
            if selection_changed:
                self.calendar_titles = self._snapshot_titles(self.calendar_titles)
                self.raw_occurrences = {entity: events for entity, events in self.raw_occurrences.items() if entity in calendars}
                self.calendar_identities = {entity: identity for entity, identity in self.calendar_identities.items() if entity in calendars}
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
                    async_save_verified(self.hass, self._store, self._snapshot())
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
            self._schedule_scan(allow_generation=enable_generation)

    async def async_refresh(self, *, allow_generation=True) -> dict:
        """Join the owned scan; public actions must obey the same lifecycle guards."""
        if self._closed or self.hass.state is not CoreState.running:
            return self.status()
        if not self._scan_task or self._scan_task.done():
            self._schedule_scan(allow_generation=allow_generation)
        if self._scan_task and not self._scan_task.done():
            await asyncio.shield(self._scan_task)
        return self.status()

    async def _async_refresh_resolve_only(self) -> dict:
        """Keep joined scans nonbillable even when the requesting client disconnects."""
        self._generation_suppressed += 1
        refresh = asyncio.create_task(self.async_refresh(allow_generation=False))
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
            return result
        finally:
            self._generation_suppressed -= 1

    async def async_refresh_for_deletion(self) -> dict:
        """Verify live demand without authorizing automatic missing-image jobs."""
        result = await self._async_refresh_resolve_only()
        if self._demand_truncated:
            raise ValueError("Calendar demand exceeded 64 distinct titles or 512 events; permanent deletion is blocked")
        return result

    async def async_refresh_coverage(self) -> dict:
        """Use the same scan, but never authorize paid work from the UI."""
        return await self._async_refresh_resolve_only()

    async def _async_refresh_once(self, *, allow_generation=True) -> dict:
        """Read a 30-day by-default window, group by calendar, resolve, then queue."""
        async with self._lock:
            if (self._closed or not self.calendars
                    or self.hass.state is not CoreState.running):
                return self.status()

            now = dt_util.now()
            end = now + timedelta(days=self.lookahead_days)
            self._scan_in_progress = True
            try:
                await self._try_load_ignores_locked()
                if not self.hass.services.has_service("calendar", "get_events"):
                    raise ValueError("calendar.get_events is not ready")
                bindings = dict(self._policy_bindings())
                for entity in self.calendars:
                    state = self.hass.states.get(entity)
                    if state is None or state.state in ("unknown", "unavailable"):
                        raise ValueError(f"Calendar is not ready: {entity}")
                    identity = calendar_identity(self.hass, entity)
                    if entity not in bindings or bindings[entity] != identity:
                        raise ValueError(f"Calendar identity is unverified or changed: {entity}. Remove, save, and explicitly reselect the calendar")
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
                occurrences: dict[str, list[dict]] = {}
                seen: set[str] = set()
                raw_seen: set[str] = set()
                truncated = False
                for entity in self.calendars:
                    if entity not in response:
                        raise ValueError(f"No calendar event data returned for {entity}")
                    payload = response[entity]
                    if not isinstance(payload, dict):
                        raise ValueError(f"Invalid event response for {entity}")
                    events = payload.get("events")
                    if not isinstance(events, list):
                        raise ValueError(f"Invalid event response for {entity}")
                    if len(events) > 512:
                        truncated = True
                    titles: list[str] = []
                    occurrences[entity] = []
                    for event in events[:512]:
                        if not isinstance(event, dict):
                            truncated = True
                            continue
                        if event.get("status") == "cancelled":
                            continue
                        raw_summary = event.get("summary")
                        if not isinstance(raw_summary, str):
                            truncated = True
                            continue
                        # Ignore identity is the untouched HA string, never this resolution title.
                        title = raw_summary.strip()
                        if not title:
                            continue
                        if (len(title) > 255 or len(raw_summary) > 1024
                                or (raw_summary not in raw_seen and len(raw_seen) >= MAX_TRACKED_TITLES)
                                or (title not in seen and len(seen) >= MAX_TRACKED_TITLES)):
                            truncated = True
                            continue
                        raw_seen.add(raw_summary)
                        occurrences[entity].append({
                            "raw_summary": raw_summary,
                            "start": event.get("start") if isinstance(event.get("start"), str) else None,
                            "end": event.get("end") if isinstance(event.get("end"), str) else None,
                        })
                        if title not in titles:
                            titles.append(title)
                            seen.add(title)
                    parsed[entity] = titles

                wanted = list(dict.fromkeys(
                    title for titles in parsed.values() for title in titles
                ))
                if self._generation_suppressed or not allow_generation:
                    images = {}
                    for title in wanted:
                        if url := await self.cache.async_find(title, touch=False, prune=False):
                            images[title] = url
                else:
                    images = await self.cache.async_find_many(wanted)
                if self._closed:
                    return self.status()
                snapshot = {**parsed, "_raw_occurrences": occurrences,
                            "_calendar_registry_ids": bindings, "_coverage_version": 1}
                self._snapshot_task = self.hass.async_create_task(async_save_verified(self.hass, self._store, snapshot))
                await asyncio.shield(self._snapshot_task)
                if self._closed:
                    return self.status()
                self._retry_count = 0
                self._warned_error = None
                self.calendar_titles = parsed
                self.raw_occurrences = occurrences
                self.calendar_identities = bindings
                self._demand_truncated = truncated
                self._verified_config = (self.calendars, self.lookahead_days)
                self.images = images
                self.last_scan = now.isoformat()
                self.last_error = None
                self._scan_in_progress = False
                self._notify()
                if allow_generation and not self._closed and not self._generation_suppressed and not truncated:
                    sources = self.automatic_sources()
                    missing = list(dict.fromkeys(source["raw_summary"].strip() for source in sources
                                                 if source["raw_summary"].strip() not in images))
                    if missing:
                        await self.queue.async_enqueue(missing, calendar_sources=sources)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                # Keep last successful map during network/provider/calendar outages.
                if not self._closed:
                    self._scan_failed(exc)
            finally:
                self._scan_in_progress = False
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
            "error": self.last_error or self._ignore_error,
            "ignore_error": self._ignore_error,
            "automatic_generation": bool(calendar_auto_ids(self.settings)),
            "complete": not self._demand_truncated and not self.coverage_stale(),
            "stale": self.coverage_stale(),
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

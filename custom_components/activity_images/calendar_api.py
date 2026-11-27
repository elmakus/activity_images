"""Admin-only calendar discovery and read-only Coverage over existing runtime data."""
from __future__ import annotations

from urllib.parse import quote, unquote
from collections.abc import Callable, Iterable, Mapping
from typing import TYPE_CHECKING, Any, TypedDict, cast

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from homeassistant.components.websocket_api import ActiveConnection
    from . import ActivityImagesRuntime
    from .calendar_adapter import ActivityCalendarAdapter, CalendarStatus
    from .library import LibraryItem

import probatio as p
from homeassistant.exceptions import HomeAssistantError

from .const import CONF_CALENDARS, DOMAIN
from .image_cache import ACTIVITY_IMAGE_PREFIX, asset_url, normalize_title
from .settings import calendar_identity
from .settings_schema import calendar_auto_ids

_QUERY = {
    p.Optional("query", default=""): p.All(str, p.Length(max=255)),
    p.Optional("filter", default="all"): p.In(["all", "missing", "available", "ignored", "queued", "failed"]),
    p.Optional("calendar", default=""): p.All(str, p.Length(max=255)),
    p.Optional("offset", default=0): p.All(int, p.Range(min=0)),
    p.Optional("limit", default=25): p.All(int, p.Range(min=1, max=96)),
    p.Optional("sort", default="title"): p.In(["title", "occurrences"]),
}
_SCHEMAS: dict[str, dict[Any, Any]] = {
    "discover": {}, "coverage": _QUERY, "refresh": _QUERY,
    "ignore": {
        p.Required("raw_summary"): p.All(str, p.Length(min=1, max=1024)),
        p.Required("ignored"): bool,
        p.Required("revision"): p.All(int, p.Range(min=0)),
    },
}


class CalendarDiscovery(TypedDict):
    entity_id: str
    name: str
    state: str
    available: bool
    monitor: bool
    auto_generate: bool
    configured: bool
    registry_status: str
    warning: str | None


class DiscoveryResult(TypedDict):
    entities: list[CalendarDiscovery]


class CoverageOccurrence(TypedDict):
    calendar_entity_id: str
    start: str | None
    end: str | None


class CoverageSource(TypedDict):
    entity_id: str
    name: str
    available: bool
    auto_generate: bool
    stale: bool
    count: int


class CoverageManualBlock(TypedDict):
    reason: str
    revision: str | None
    uncertain: bool


class CoverageItem(TypedDict):
    raw_summary: str
    title: str
    ignored: bool
    scheduled: bool
    occurrence_count: int
    calendars: list[CoverageSource]
    occurrences: list[CoverageOccurrence]
    image_status: str
    resolution: str
    image: LibraryItem | None
    job_status: str | None
    job_id: str | None
    job_error: str | None
    manual_block: CoverageManualBlock | None
    eligible_for_auto: bool


class CoverageResult(TypedDict):
    items: list[CoverageItem]
    total: int
    offset: int
    limit: int
    stats: dict[str, int]
    scan: CalendarStatus
    ignore_revision: int
    entities: list[CalendarDiscovery]


def discover(calendar: ActivityCalendarAdapter) -> DiscoveryResult:
    """Keep saved missing IDs visible; discovery never selects or fetches calendars."""
    from homeassistant.helpers import entity_registry as er

    registry = er.async_get(calendar.hass)
    states = {state.entity_id: state for state in calendar.hass.states.async_all("calendar")}
    selected = set(cast(Iterable[str], calendar.settings.get(CONF_CALENDARS) or ()))
    automatic = set(calendar_auto_ids(calendar.settings)) & selected
    bindings = calendar._policy_bindings()
    ids = selected | set(states) | {entity for entity in registry.entities if entity.startswith("calendar.")}
    entities: list[CalendarDiscovery] = []
    for entity_id in sorted(ids):
        state, entry = states.get(entity_id), registry.async_get(entity_id)
        name = (cast(str | None, state.attributes.get("friendly_name")) if state else None) or (
            entry.name or entry.original_name if entry else None
        ) or entity_id
        available = state is not None and state.state not in {"unknown", "unavailable"} and not (entry and entry.disabled_by)
        warning = None
        if entity_id in selected and (entity_id not in bindings or bindings[entity_id] != calendar_identity(calendar.hass, entity_id)):
            registry_status = "identity_changed"
            warning = "Calendar identity is unverified or changed. Remove Monitor, save, and explicitly select the intended calendar again. Automatic generation is blocked."
        elif entry is None:
            registry_status = "unregistered" if state else "missing"
            warning = "Calendar has no stable registry identity. Monitoring is supported, but automatic generation is blocked." if state else "Calendar unavailable — previously configured entity was not found."
        elif not available:
            registry_status = "missing"
            warning = "Calendar unavailable — showing the saved selection."
        else:
            registry_status = "registered"
        entities.append({"entity_id": entity_id, "name": name, "state": state.state if state else "missing",
                         "available": available, "monitor": entity_id in selected,
                         "auto_generate": entity_id in automatic, "configured": entity_id in selected,
                         "registry_status": registry_status, "warning": warning})
    return {"entities": entities}


async def coverage(runtime: ActivityImagesRuntime, *, query: str = "", filter: str = "all", calendar: str = "", offset: int = 0, limit: int = 25, sort: str = "title") -> CoverageResult:
    """Derive bounded exact-raw groups without queueing, touching or pruning mappings."""
    if (not isinstance(query, str) or len(query) > 255
            or filter not in {"all", "missing", "available", "ignored", "queued", "failed"}
            or not isinstance(calendar, str) or len(calendar) > 255
            or type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 96
            or sort not in {"title", "occurrences"}):
        raise ValueError("Invalid Coverage query")
    adapter, cache, queue, library = runtime.calendar, runtime.cache, runtime.queue, runtime.library
    await library.async_inventory()
    async with adapter._lock:
        if adapter._closed:
            raise ValueError("Activity Images is unloading")
        entities = discover(adapter)["entities"]
        by_entity = {entity["entity_id"]: entity for entity in entities}
        selected = set(cast(Iterable[str], adapter.settings.get(CONF_CALENDARS) or ()))
        groups: dict[str, list[CoverageOccurrence]] = {raw: [] for raw in adapter.ignored_titles}
        # Display projection only; authority stays in automatic_sources().
        for entity, events in adapter.display_occurrences_map().items():
            if entity in selected:
                for event in events:
                    groups.setdefault(event["raw_summary"], []).append({
                        "calendar_entity_id": entity, "start": event.get("start"), "end": event.get("end"),
                    })
        snapshot = await queue.async_coverage_snapshot()
        jobs = snapshot["jobs"]
        failures = snapshot["failed"] | snapshot["retry_blocked"]
        blocked = failures | snapshot["manual_blocked"]
        manual_blocks = snapshot["manual_blocks"]
        scan = adapter.status()
        automatic = adapter.automatic_sources()
        inventory_titles = library._titles(cache.entries())
        resolved: dict[str, tuple[LibraryItem | None, str, str]] = {}
        items: list[CoverageItem] = []
        for raw, occurrences in groups.items():
            title, key = raw.strip(), normalize_title(raw)
            if title not in resolved:
                url = await cache.async_find(title, touch=False, prune=False)
                image = None
                if url and url.startswith(ACTIVITY_IMAGE_PREFIX) and not cache.reserved(title, url):
                    image_id = library.image_id(unquote(asset_url(url).rsplit("/", 1)[-1]))
                    asset = library._images.get(image_id)
                    if asset and asset_url(url) == ACTIVITY_IMAGE_PREFIX + quote(asset["filename"], safe=""):
                        image = library._item(image_id, inventory_titles)
                resolution = "normalized" if image and asset_url(cache.exact(title) or "") == asset_url(url or "") else "fuzzy" if image else "none"
                status = "available" if image else "unknown" if scan["stale"] or url or cache.reserved(title) else "missing"
                resolved[title] = (image, resolution, status)
            image, resolution, image_status = resolved[title]
            job = next((job for job in reversed(jobs) if normalize_title(job["title"]) == key), None)
            job_status = {"pending": "queued", "running": "running", "review": "review",
                          "failed": "failed", "interrupted": "failed"}.get(job["state"]) if job else "failed" if key in failures else None
            manual_block: CoverageManualBlock | None = None
            if key in blocked and key in snapshot["manual_blocked"]:
                entry = manual_blocks.get(key)
                if entry is None:
                    manual_block = {"reason": "legacy_unknown", "revision": None, "uncertain": True}
                else:
                    manual_block = {"reason": entry.get("reason"), "revision": entry.get("revision"),
                                    "uncertain": bool(entry.get("uncertain"))}
            sources: list[CoverageSource] = []
            for entity in sorted({event["calendar_entity_id"] for event in occurrences}):
                info = by_entity[entity]
                sources.append({"entity_id": entity, "name": info["name"], "available": info["available"],
                                "auto_generate": info["auto_generate"], "stale": adapter.display_stale(entity),
                                "count": sum(event["calendar_entity_id"] == entity for event in occurrences)})
            items.append({"raw_summary": raw, "title": title, "ignored": raw in adapter.ignored_titles,
                          "scheduled": bool(occurrences), "occurrence_count": len(occurrences),
                          "calendars": sources, "occurrences": occurrences,
                          "image_status": image_status, "resolution": resolution, "image": image,
                          "job_status": job_status, "job_id": job["id"] if job else None,
                          "job_error": job.get("error") if job else None,
                          "manual_block": manual_block,
                          "eligible_for_auto": image_status == "missing" and key not in blocked
                          and any(source["raw_summary"] == raw for source in automatic)})
        revision = adapter.ignore_revision
    stats = {"titles": len(items), **dict.fromkeys(("missing", "available", "ignored", "queued", "failed"), 0)}

    def matches(item: CoverageItem, category: str) -> bool:
        return (category == "all" or category in {"missing", "available"} and item["image_status"] == category
                or category == "ignored" and item["ignored"]
                or category == "queued" and item["job_status"] in {"queued", "running", "review"}
                or category == "failed" and item["job_status"] == "failed")

    for item in items:
        for category in stats.keys() - {"titles"}:
            stats[category] += int(matches(item, category))
    search = query.casefold().strip()
    items = [item for item in items if matches(item, filter)
             and (not calendar or any(source["entity_id"] == calendar for source in item["calendars"]))
             and (not search or search in item["raw_summary"].casefold()
                  or any(search in source["name"].casefold() for source in item["calendars"]))]
    items.sort(key=lambda item: ((-item["occurrence_count"] if sort == "occurrences" else 0), item["raw_summary"].casefold(), item["raw_summary"]))
    return {"items": items[offset:offset + limit], "total": len(items), "offset": offset, "limit": limit,
            "stats": stats, "scan": scan, "ignore_revision": revision, "entities": entities}


def _handler(command: str, fields: Mapping[Any, Any]) -> Callable[..., None]:
    from homeassistant.components import websocket_api

    from .settings import get_runtime as _runtime

    @websocket_api.websocket_command({"type": f"{DOMAIN}/calendar/{command}", **fields})
    @websocket_api.require_admin
    @websocket_api.async_response
    async def handle(hass: HomeAssistant, connection: ActiveConnection, msg: dict[str, Any]) -> None:
        try:
            runtime = _runtime(hass)
            args = {key: value for key, value in msg.items() if key not in {"id", "type"}}
            result: object
            if command == "discover":
                result = discover(runtime.calendar)
            elif command == "ignore":
                result = await runtime.calendar.async_ignore(**args)
            else:
                if command == "refresh":
                    await runtime.calendar.async_refresh_coverage()
                result = await coverage(runtime, **args)
        except (ValueError, OSError, HomeAssistantError) as exc:
            connection.send_error(msg["id"], "calendar_error", str(exc))
        else:
            connection.send_result(msg["id"], result)
    return handle


def async_register_calendar_api(hass: HomeAssistant) -> None:
    from homeassistant.components import websocket_api

    for command, fields in _SCHEMAS.items():
        websocket_api.async_register_command(hass, _handler(command, fields))

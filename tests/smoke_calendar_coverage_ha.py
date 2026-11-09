"""Native HA 2026.11 calendar Coverage smoke: real ConfigEntries, registry,
options flow, authenticated WS admin/schema checks and local fakes only.

Run from the repo root: python tests/smoke_calendar_coverage_ha.py
Serve a loopback browser harness instead: python tests/smoke_calendar_coverage_ha.py --serve --ready-file /tmp/ready.json

Fake calendar service and fake ai_task local image only; no external
provider, no production HA, all state in a TemporaryDirectory.
"""
from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import os
import signal
import sys
import tempfile
from pathlib import Path
from types import MappingProxyType, SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from aiohttp import web  # noqa: E402
from aiohttp.test_utils import TestClient, TestServer  # noqa: E402
from homeassistant import loader  # noqa: E402
from homeassistant.auth.const import GROUP_ID_ADMIN, GROUP_ID_USER  # noqa: E402
from homeassistant.config_entries import ConfigEntries, ConfigEntry  # noqa: E402
from homeassistant.const import __version__  # noqa: E402
from homeassistant.core import CoreState, HomeAssistant, SupportsResponse  # noqa: E402
from homeassistant.helpers import entity_registry as er  # noqa: E402
from homeassistant.helpers.storage import Store  # noqa: E402
from smoke_library_ha import prepare_http  # noqa: E402
from smoke_settings_ha import form_input  # noqa: E402

from custom_components.activity_images import (  # noqa: E402
    async_setup,
    async_setup_entry,
    async_unload_entry,
)
from custom_components.activity_images.config_flow import ActivityImagesConfigFlow  # noqa: E402
from custom_components.activity_images.const import (  # noqa: E402
    CONF_AI_ENTITY,
    CONF_AUTO_CALENDARS,
    CONF_CALENDARS,
    CONF_LOOKAHEAD_DAYS,
    DOMAIN,
)
from custom_components.activity_images.sensor import (  # noqa: E402
    ActivityImagesLibrarySensor,
)
from custom_components.activity_images.settings import read_settings  # noqa: E402

RAW_SPACE = "Basen 9:15 "
RAW_PLAIN = "Basen 9:15"
RAW_CASE = "basen 9:15"
RAW_UNICODE = "Café ☕"
RAW_SWIM = "Swim"
RAW_MANUAL = "Manual activity"

EVENTS = {
    "calendar.school": {"events": [
        {"summary": RAW_SPACE, "start": "2026-11-10T09:00:00", "end": "2026-11-10T10:00:00"},
        {"summary": RAW_PLAIN, "start": "2026-11-11T09:00:00", "end": "2026-11-11T10:00:00"},
        {"summary": RAW_CASE, "start": "2026-11-12T09:00:00", "end": "2026-11-12T10:00:00"},
        {"summary": RAW_UNICODE, "start": "2026-11-13T09:00:00", "end": "2026-11-13T10:00:00"},
        {"summary": RAW_SWIM, "start": "2026-11-14T09:00:00", "end": "2026-11-14T10:00:00"},
    ]},
}


async def build(directory: str) -> dict:
    """Create real HA state, entry, fakes and runtime. No assertions here."""
    hass: Any = HomeAssistant(directory)
    await prepare_http(hass)
    loader.async_setup(hass)
    manager = hass.config_entries = ConfigEntries(hass, {})
    await manager.async_initialize()
    await er.async_load(hass)
    # Register the calendar identity before any state exists so the entity
    # ID is exact and no collision suffix is appended.
    registry = er.async_get(hass)
    school = registry.async_get_or_create("calendar", "test", "school", suggested_object_id="school")
    work = registry.async_get_or_create("calendar", "test", "work", suggested_object_id="work")
    assert school.entity_id == "calendar.school", school.entity_id
    assert work.entity_id == "calendar.work", work.entity_id
    hass.states.async_set("calendar.school", "on", {"friendly_name": "School"})
    hass.states.async_set("calendar.work", "off", {"friendly_name": "Work"})

    media_folder = Path(directory) / "www" / "activity_images"
    media_folder.mkdir(parents=True)
    Image.new("RGB", (64, 48), "green").save(media_folder / "library.png")
    source = Path(directory) / "provider.png"
    Image.new("RGBA", (64, 64), (10, 20, 30, 255)).save(source)

    provider_calls = []

    async def fake_events(call) -> dict[str, Any]:
        return EVENTS

    hass.services.async_register("calendar", "get_events", fake_events,
                                 supports_response=SupportsResponse.ONLY)

    async def fake_provider(call) -> dict[str, Any]:
        provider_calls.append(call.data)
        return {"media_source_id": "media-source://ai_task/image/test.png"}

    hass.services.async_register("ai_task", "generate_image", fake_provider,
                                 supports_response=SupportsResponse.ONLY)

    async def fake_resolve(hass, media, user):
        assert media == "media-source://ai_task/image/test.png"
        return SimpleNamespace(path=source, url="/api/ai_task/image/test.png")

    entry = ConfigEntry(domain=DOMAIN, title="Activity Images", version=1, minor_version=1,
                        source="user", unique_id=DOMAIN, subentries_data=None,
                        discovery_keys=MappingProxyType({}),
                        data={CONF_AI_ENTITY: "ai_task.mock"},
                        options={CONF_CALENDARS: ["calendar.school"], CONF_LOOKAHEAD_DAYS: 30})
    with patch.object(manager, "async_setup", AsyncMock(return_value=True)):
        await manager.async_add(entry)
    await async_setup(hass, {})
    with patch.object(manager, "async_forward_entry_setups", AsyncMock()):
        await async_setup_entry(hass, entry)
    runtime = entry.runtime_data
    hass.state = CoreState.running
    admin = await hass.auth.async_create_user("Coverage Admin", group_ids=[GROUP_ID_ADMIN])
    regular = await hass.auth.async_create_user("Coverage User", group_ids=[GROUP_ID_USER])

    async def token(user):
        refresh = await hass.auth.async_create_refresh_token(user, client_id="http://localhost/")
        return hass.auth.async_create_access_token(refresh)

    return {"hass": hass, "manager": manager, "entry": entry, "runtime": runtime,
            "provider_calls": provider_calls, "resolve": fake_resolve,
            "admin_token": await token(admin), "user_token": await token(regular)}


async def ws_client(client, access_token):
    ws = await client.ws_connect("/api/websocket")
    assert (await ws.receive_json())["type"] == "auth_required"
    await ws.send_json({"type": "auth", "access_token": access_token})
    assert (await ws.receive_json())["type"] == "auth_ok"
    return ws


def counter():
    state = {"value": 0}

    async def command(ws, name, **kwargs):
        state["value"] += 1
        await ws.send_json({"id": state["value"], "type": name, **kwargs})
        message = await asyncio.wait_for(ws.receive_json(), 5)
        assert message["id"] == state["value"], message
        return message

    return command


async def run_assertions(ctx: dict) -> None:
    """Default mode: real-HA assertions, then exit. No production deletion."""
    hass = ctx["hass"]
    manager = ctx["manager"]
    entry = ctx["entry"]
    runtime = ctx["runtime"]
    provider_calls = ctx["provider_calls"]
    calendar = runtime.calendar
    assert __version__ == "2026.11.0", __version__

    status = await calendar.async_refresh()
    # Five exact raw occurrences collapse to four trimmed titles; the
    # trailing-space variant stays an independent raw group for exact ignore.
    assert status["tracked_titles"] == 4, status
    raws = [event["raw_summary"] for event in calendar.raw_occurrences["calendar.school"]]
    assert raws == [RAW_SPACE, RAW_PLAIN, RAW_CASE, RAW_UNICODE, RAW_SWIM], raws
    assert calendar.raw_occurrences["calendar.school"][0] != calendar.raw_occurrences["calendar.school"][1]
    assert provider_calls == []

    async with TestClient(TestServer(hass.http.app)) as client:
        admin_ws = await ws_client(client, ctx["admin_token"])
        user_ws = await ws_client(client, ctx["user_token"])
        command = counter()

        async def calendar_ws(ws, name, **kwargs):
            return await command(ws, f"activity_images/calendar/{name}", **kwargs)

        for name, args in {
            "discover": {}, "coverage": {}, "refresh": {},
            "ignore": {"raw_summary": "x", "ignored": True, "revision": 0},
        }.items():
            denied = await calendar_ws(user_ws, name, **args)
            assert not denied["success"] and denied["error"]["code"] == "unauthorized", (name, denied)
        for data in ({"revision": "0" * 64, "values": {}},):
            denied = await command(user_ws, "activity_images/settings/save", **data)
            assert not denied["success"] and denied["error"]["code"] == "unauthorized", denied

        discovered = await calendar_ws(admin_ws, "discover")
        assert discovered["success"], discovered
        by_id = {item["entity_id"]: item for item in discovered["result"]["entities"]}
        assert by_id["calendar.school"]["available"] is True
        assert by_id["calendar.school"]["monitor"] is True
        assert by_id["calendar.school"]["auto_generate"] is False
        assert by_id["calendar.school"]["name"] == "School"
        assert by_id["calendar.work"]["state"] == "off"
        assert by_id["calendar.work"]["available"] is True
        assert by_id["calendar.work"]["monitor"] is False

        covered = await calendar_ws(admin_ws, "coverage")
        assert covered["success"], covered
        body = covered["result"]
        assert body["total"] == 5, body["total"]
        assert body["stats"]["missing"] == 5, body["stats"]
        assert body["stats"]["ignored"] == 0, body["stats"]
        assert {item["raw_summary"] for item in body["items"]} == {
            RAW_SPACE, RAW_PLAIN, RAW_CASE, RAW_UNICODE, RAW_SWIM}
        assert all(item["occurrence_count"] == 1 for item in body["items"])
        assert body["ignore_revision"] == 0
        assert all(item["image_status"] == "missing" for item in body["items"])

        searched = await calendar_ws(admin_ws, "coverage", query="basen")
        assert searched["success"] and searched["result"]["total"] == 3, searched
        assert searched["result"]["total"] == 3
        missing_only = await calendar_ws(admin_ws, "coverage", filter="missing")
        assert missing_only["success"] and missing_only["result"]["total"] == 5
        for bad in ({"limit": 97}, {"limit": "25"}, {"offset": -1}, {"query": "x" * 256},
                    {"calendar": "x" * 256}, {"filter": "nope"}, {"sort": "nope"}):
            rejected = await calendar_ws(admin_ws, "coverage", **bad)
            assert not rejected["success"] and rejected["error"]["code"] == "invalid_format", (bad, rejected)
        for bad in ({}, {"raw_summary": "x"}, {"raw_summary": "x", "ignored": "yes", "revision": 0},
                    {"raw_summary": "x", "ignored": True, "revision": "0"},
                    {"raw_summary": "", "ignored": True, "revision": 0}):
            rejected = await calendar_ws(admin_ws, "ignore", **bad)
            assert not rejected["success"] and rejected["error"]["code"] == "invalid_format", (bad, rejected)

        refreshed = await calendar_ws(admin_ws, "refresh", filter="missing")
        assert refreshed["success"], refreshed
        assert refreshed["result"]["total"] == 5, refreshed["result"]["total"]
        assert provider_calls == []
        assert runtime.queue.status()["pending"] == []

        added = await calendar_ws(admin_ws, "ignore", raw_summary=RAW_SPACE, ignored=True, revision=0)
        assert added["success"] and added["result"] == {"revision": 1, "ignored": True}, added
        added_unicode = await calendar_ws(admin_ws, "ignore", raw_summary=RAW_UNICODE, ignored=True, revision=1)
        assert added_unicode["success"] and added_unicode["result"]["revision"] == 2
        stored = await Store(hass, 1, "activity_images_ignored_titles").async_load()
        assert stored is not None and stored["revision"] == 2, stored
        assert set(stored["titles"]) == {RAW_SPACE, RAW_UNICODE}, stored
        assert RAW_SPACE in stored["titles"] and stored["titles"] != [title.strip() for title in stored["titles"]]
        stale = await calendar_ws(admin_ws, "ignore", raw_summary=RAW_SWIM, ignored=True, revision=1)
        assert not stale["success"] and stale["error"]["code"] == "calendar_error", stale
        assert "revision conflict" in stale["error"]["message"], stale
        removed = await calendar_ws(admin_ws, "ignore", raw_summary=RAW_SPACE, ignored=False, revision=2)
        assert removed["success"] and removed["result"] == {"revision": 3, "ignored": False}, removed
        flagged = await calendar_ws(admin_ws, "coverage", filter="ignored")
        assert flagged["success"], flagged
        assert {item["raw_summary"] for item in flagged["result"]["items"]} == {RAW_UNICODE}, flagged
        assert flagged["result"]["items"][0]["scheduled"] is True
        assert provider_calls == []

        listed = await command(admin_ws, "activity_images/library/list", limit=25)
        assert listed["success"] and listed["result"]["total"] == 1, listed
        image_id = listed["result"]["items"][0]["id"]
        linked = await command(admin_ws, "activity_images/library/link",
                                       image_id=image_id, title=RAW_SWIM)
        assert linked["success"] and linked["result"]["linked"] is True, linked
        await calendar.async_refresh()
        sensor = ActivityImagesLibrarySensor(entry.entry_id, calendar)
        assert sensor.native_value >= 1, sensor.native_value
        assert sensor.extra_state_attributes["images"].get(RAW_SWIM), sensor.extra_state_attributes["images"]
        swim = await calendar_ws(admin_ws, "coverage", query=RAW_SWIM)
        assert swim["success"], swim
        assert swim["result"]["items"][0]["image_status"] == "available"
        assert swim["result"]["items"][0]["image"] is not None
        assert provider_calls == []

        snapshot = await Store(hass, 1, "activity_images_calendar_snapshot").async_load()
        assert snapshot is not None and "_raw_occurrences" in snapshot, snapshot
        assert RAW_SPACE in {event["raw_summary"]
                             for events in snapshot["_raw_occurrences"].values() for event in events}
        assert RAW_SWIM in snapshot["calendar.school"], "snapshot must keep the additive trimmed titles"

        awaiting_review = await calendar_ws(admin_ws, "ignore", raw_summary=RAW_MANUAL,
                                            ignored=True, revision=3)
        assert awaiting_review["success"] and awaiting_review["result"]["revision"] == 4
        orphan = await calendar_ws(admin_ws, "coverage", filter="ignored")
        assert orphan["success"], orphan
        manual_groups = [item for item in orphan["result"]["items"] if item["raw_summary"] == RAW_MANUAL]
        assert len(manual_groups) == 1 and manual_groups[0]["scheduled"] is False, orphan
        with patch("custom_components.activity_images.generated_image.async_resolve_media",
                   side_effect=ctx["resolve"]):
            job = await runtime.queue.async_candidate(RAW_MANUAL)
            for _ in range(100):
                await hass.async_block_till_done()
                if runtime.queue._job(job["id"])["state"] == "review":
                    break
            assert runtime.queue._job(job["id"])["state"] == "review"
        accepted = await runtime.queue.async_accept(job["id"])
        assert accepted["state"] == "accepted", accepted
        assert runtime.cache.exact(RAW_MANUAL) is not None
        assert RAW_MANUAL in runtime.calendar.ignored_titles
        assert len(provider_calls) == 1, provider_calls

        snapshot_settings = await command(admin_ws, "activity_images/settings/get")
        assert snapshot_settings["success"], snapshot_settings
        revision = snapshot_settings["result"]["revision"]
        monitor_only = await command(
            admin_ws, "activity_images/settings/save", revision=revision,
            values={CONF_CALENDARS: ["calendar.school", "calendar.work", "calendar.deleted"]})
        assert monitor_only["success"] and monitor_only["result"]["changed"], monitor_only
        assert len(provider_calls) == 1
        rediscovered = await calendar_ws(admin_ws, "discover")
        assert rediscovered["success"], rediscovered
        missing = {item["entity_id"]: item for item in rediscovered["result"]["entities"]}["calendar.deleted"]
        assert missing["monitor"] is True and missing["available"] is False
        assert missing["configured"] is True and missing["state"] == "missing"
        assert {item["entity_id"]: item for item in rediscovered["result"]["entities"]}["calendar.work"]["monitor"] is True
        revision = monitor_only["result"]["revision"]
        auto_bare = await command(
            admin_ws, "activity_images/settings/save", revision=revision,
            values={CONF_AUTO_CALENDARS: ["calendar.school"]})
        assert not auto_bare["success"] and auto_bare["error"]["code"] == "settings_error", auto_bare
        assert "explicit paid confirmation" in auto_bare["error"]["message"], auto_bare
        assert len(provider_calls) == 1
        auto_confirmed = await command(
            admin_ws, "activity_images/settings/save", revision=revision, confirm_auto=True,
            values={CONF_AUTO_CALENDARS: ["calendar.school"]})
        assert auto_confirmed["success"] and auto_confirmed["result"]["changed"], auto_confirmed
        assert entry.options[CONF_AUTO_CALENDARS] == ["calendar.school"]
        stale_save = await command(
            admin_ws, "activity_images/settings/save", revision=revision,
            values={CONF_LOOKAHEAD_DAYS: 21})
        assert not stale_save["success"] and stale_save["error"]["code"] == "settings_conflict", stale_save
        auto_off = await command(
            admin_ws, "activity_images/settings/save", revision=auto_confirmed["result"]["revision"],
            values={CONF_AUTO_CALENDARS: []})
        assert auto_off["success"], auto_off
        assert entry.options[CONF_AUTO_CALENDARS] == []
        assert len(provider_calls) == 1

        with patch("homeassistant.config_entries._async_get_flow_handler",
                   AsyncMock(return_value=ActivityImagesConfigFlow)):
            form = await manager.options.async_init(entry.entry_id)
            assert form["type"] == "form", form
            current = read_settings(hass)["values"]
            confirm = await manager.options.async_configure(
                form["flow_id"], form_input({**current, CONF_AUTO_CALENDARS: ["calendar.school"]}))
            assert confirm["type"] == "form" and confirm["step_id"] == "confirm_auto", confirm
            finished = await manager.options.async_configure(form["flow_id"], {"confirm_paid": True})
            assert finished["type"] == "create_entry", finished
            assert entry.options[CONF_AUTO_CALENDARS] == ["calendar.school"]
        await hass.async_block_till_done()
        assert len(provider_calls) == 1, provider_calls
        await admin_ws.close()
        await user_ws.close()

    print(f"SMOKE-OK tracked=5 ignored_exact=2 linked=1 manual_paid=1 "
          f"provider_calls={len(provider_calls)} ha={__version__}")


HARNESS_HTML = """<!doctype html>
<html lang="en">
<head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Calendar Coverage Harness</title>
<style>
:root { color-scheme:dark; --primary-text-color:#e1e1e1; --secondary-text-color:#aaa;
--primary-background-color:#111; --secondary-background-color:#222;
--card-background-color:#1c1c1c; --divider-color:#444; --primary-color:#03a9f4;
--error-color:#ff8080; --ha-card-border-radius:16px; }
body { margin:0; background:var(--primary-background-color); font-family:Arial,sans-serif; }
#status { color:var(--secondary-text-color); padding:8px; }
</style>
</head>
<body>
<div id="status">connecting…</div>
<script type="module">
import "/activity_images/activity-images-panel.js";
const TOKEN = %TOKEN%;
const status = document.getElementById("status");
const ws = new WebSocket("ws://" + location.host + "/api/websocket");
let id = 0;
const pending = new Map();
ws.onmessage = (event) => {
  const msg = JSON.parse(event.data);
  if (msg.type === "auth_required") {
    ws.send(JSON.stringify({type: "auth", access_token: TOKEN}));
    return;
  }
  if (msg.type === "auth_ok") {
    status.textContent = "authenticated";
    mount();
    return;
  }
  if (msg.type === "auth_invalid") {
    status.textContent = "authentication failed";
    return;
  }
  const request = pending.get(msg.id);
  if (request) {
    pending.delete(msg.id);
    if (msg.success) request.resolve(msg.result);
    else request.reject(msg.error);
  }
};
ws.onclose = () => {
  for (const request of pending.values()) request.reject(new Error("Fixture disconnected"));
  pending.clear();
};
function callWS(message) {
  return new Promise((resolve, reject) => {
    const next = ++id;
    pending.set(next, {resolve, reject});
    ws.send(JSON.stringify({id: next, ...message}));
  });
}
async function fetchWithAuth(path, options = {}) {
  const response = await fetch(path, {...options, headers: {Authorization: "Bearer " + TOKEN}});
  return {ok: response.ok, status: response.status, blob: () => response.blob()};
}
function mount() {
  const panel = document.createElement("activity-images-panel");
  panel.hass = {callWS, fetchWithAuth, connection: new EventTarget(), states: %STATES%, config: {}};
  document.body.append(panel);
  status.textContent = "panel mounted";
}
</script>
</body>
</html>
"""


async def serve(ctx: dict, ready_file: str | None) -> None:
    """Loopback-only harness for the main agent's browser test.

    Serves the real panel module plus a test-only unauthenticated harness
    route that exists only in this loopback process, never in the integration
    source. The disposable access token lives in process memory and the
    loopback page only; it is never printed, saved, or written to disk.
    """
    hass = ctx["hass"]
    token = ctx["admin_token"]
    states = {state.entity_id: state.as_dict() for state in hass.states.async_all()}
    page = HARNESS_HTML.replace("%TOKEN%", json.dumps(token)).replace("%STATES%", json.dumps(states))

    async def harness(_request):
        return web.Response(text=page, content_type="text/html", headers={"Cache-Control": "no-store"})

    async def metrics(_request):
        return web.json_response({"provider_calls": len(ctx["provider_calls"]),
                                  "ignore_revision": ctx["runtime"].calendar.ignore_revision,
                                  "pending": len(ctx["runtime"].queue.status()["pending"])})

    hass.http.app.router.add_get("/harness", harness)
    hass.http.app.router.add_get("/fixture-metrics", metrics)
    server = TestServer(hass.http.app, host="127.0.0.1", port=0)
    await server.start_server()
    url = f"http://127.0.0.1:{server.port}/harness"
    if ready_file:
        Path(ready_file).write_text(json.dumps({"url": url, "pid": os.getpid()}))
    print(f"SERVE-READY url={url} pid={os.getpid()}")
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for name in ("SIGTERM", "SIGINT"):
        with contextlib.suppress(ValueError, OSError, RuntimeError):
            loop.add_signal_handler(getattr(signal, name), stop.set)
    try:
        await stop.wait()
    finally:
        await server.close()
    print("SERVE-STOPPED")


async def main() -> None:
    parser = argparse.ArgumentParser(description="Native calendar Coverage smoke")
    parser.add_argument("--serve", action="store_true",
                        help="serve the loopback browser harness instead of asserting")
    parser.add_argument("--ready-file", default=None,
                        help="write harness metadata JSON (URL + PID, no token) here")
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="activity-coverage-smoke-") as directory:
        ctx = await build(directory)
        status = await ctx["runtime"].calendar.async_refresh()
        # Five exact raw occurrences, four trimmed titles: the trailing-space
        # variant stays an independent raw group for exact-ignore purposes.
        assert status["tracked_titles"] == 4, status
        try:
            if args.serve:
                with patch("custom_components.activity_images.generated_image.async_resolve_media",
                           side_effect=ctx["resolve"]):
                    await serve(ctx, args.ready_file)
            else:
                await run_assertions(ctx)
        finally:
            with patch.object(ctx["manager"], "async_unload_platforms", AsyncMock(return_value=True)):
                await async_unload_entry(ctx["hass"], ctx["entry"])
            await ctx["hass"].async_stop()


if __name__ == "__main__":
    asyncio.run(main())

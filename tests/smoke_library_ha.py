"""Real HA 2026.11 auth/HTTP/WS/reload smoke in a disposable local process.

Run with .venv/bin/python tests/smoke_library_ha.py. No live HA or AI calls.
"""
from __future__ import annotations

import asyncio
import sys
import tempfile
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

import probatio
from aiohttp.test_utils import TestClient, TestServer
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from homeassistant.auth import auth_manager_from_config  # noqa: E402
from homeassistant.auth.const import GROUP_ID_ADMIN, GROUP_ID_USER  # noqa: E402
from homeassistant.components import frontend, websocket_api  # noqa: E402
from homeassistant.components.http import StaticPathConfig  # noqa: E402
from homeassistant.components.http.server import HomeAssistantHTTP  # noqa: E402
from homeassistant.const import EVENT_HOMEASSISTANT_FINAL_WRITE, __version__  # noqa: E402
from homeassistant.core import CoreState, HomeAssistant  # noqa: E402
from homeassistant.exceptions import HomeAssistantError  # noqa: E402
from homeassistant.helpers import device_registry as dr  # noqa: E402
from homeassistant.helpers import storage as ha_storage  # noqa: E402
from homeassistant.helpers.data_entry_flow import _BaseFlowManagerView  # noqa: E402
from homeassistant.helpers.storage import Store  # noqa: E402
from homeassistant.util.file import WriteError, write_utf8_file_atomic  # noqa: E402
from homeassistant.util.ssl import SSLProfile  # noqa: E402

from custom_components.activity_images import (  # noqa: E402
    async_setup,
    async_setup_entry,
    async_unload_entry,
    deletion_files,  # noqa: E402
)
from custom_components.activity_images.config_flow import (  # noqa: E402
    ActivityImagesConfigFlow,
    ActivityImagesOptionsFlow,
)
from custom_components.activity_images.library_api import MODULE_URL, PANEL_ROUTE  # noqa: E402


class Entry:
    entry_id = "library_smoke"
    data = {}
    options = {}
    runtime_data: Any = None

    def add_update_listener(self, callback):
        return lambda: None

    def async_on_unload(self, callback):
        pass


async def prepare_http(hass):
    dr.async_setup(hass)
    await dr.async_load(hass, load_empty=True)
    hass.auth = await auth_manager_from_config(hass, [], [])
    hass.http = HomeAssistantHTTP(
        hass, ssl_certificate=None, ssl_peer_certificate=None, ssl_key=None,
        server_host=["127.0.0.1"], server_port=0, trusted_proxies=[],
        ssl_profile=SSLProfile.MODERN_V4,
    )
    await hass.http.async_initialize(cors_origins=[], use_x_forwarded_for=False,
                                     login_threshold=-1, is_ban_enabled=False,
                                     use_x_frame_options=True)
    await websocket_api.async_setup(hass, {})


async def native_write_failures(runtime):
    """Inject below real Store.async_save, which intentionally swallows WriteError."""
    cache, queue = runtime.cache, runtime.queue
    old, new = "/local/activity_images/standalone.png", "/local/activity_images/other.png"
    await cache.async_remember("Durable Selected", old)
    await cache.async_remember("Durable Alias", old)
    cache_before = cache.entries()
    cache_generation = cache._save_generation
    native = write_utf8_file_atomic
    failing_paths = set()
    def fail_native(filename, *args, **kwargs):
        if filename in failing_paths:
            raise WriteError("Injected native disk failure")
        return native(filename, *args, **kwargs)

    async def must_fail(operation):
        try:
            await operation
        except OSError as exc:
            assert "persistence verification failed" in str(exc), exc
        else:
            raise AssertionError("Swallowed native WriteError was reported as success")

    with patch.object(ha_storage, "write_utf8_file_atomic", fail_native):
        failing_paths.add(cache._store.path)
        await must_fail(cache.async_link("Durable Selected", new, replace=True))
        await must_fail(cache.async_record_image(new, {"origin": "generated"}))
        assert cache.entries() == cache_before
        assert cache._save_generation == cache_generation
        assert cache.image_metadata(new) == {}
        failing_paths.clear()
        # A failed enqueue must roll back before a worker is scheduled.
        failing_paths.add(queue._store.path)
        queue_generation = queue._save_generation
        with patch.object(queue.engine, "async_generate", new_callable=AsyncMock) as provider:
            await must_fail(queue.async_candidate("Undurable Enqueue"))
            assert queue.status()["pending"] == []
            assert queue._save_generation == queue_generation
            provider.assert_not_called()
            # Persist pending successfully, then fail the running-attempt boundary.
            failing_paths.clear()
            with patch.object(queue, "_kick", lambda: None):
                job = await queue.async_candidate("Undurable Attempt")
            failing_paths.add(queue._store.path)
            await must_fail(queue._run())
            assert queue._job(job["id"])["state"] == "interrupted"
            provider.assert_not_called()
        failing_paths.clear()
        await queue._save()
        candidate = queue._new_job("Durable Selected", "manual")
        candidate.update(state="review", result_url=new)
        queue._jobs.append(candidate)
        await queue._save()
        failing_paths.add(cache._store.path)
        await must_fail(queue.async_accept(candidate["id"]))
        assert cache.entries() == cache_before
        assert queue._job(candidate["id"])["state"] == "review"
        failing_paths.clear()
        failing_paths.add(queue._store.path)
        await must_fail(queue.async_accept(candidate["id"]))
        assert cache.exact("Durable Selected") == old
        assert cache.exact("Durable Alias") == old
        assert queue._job(candidate["id"])["state"] == "review"
        failing_paths.clear()
        assert (await queue.async_accept(candidate["id"]))["state"] == "accepted"
        assert cache.exact("Durable Selected") == new
        assert cache.exact("Durable Alias") == old


async def native_deletion_failures(runtime, folder):
    """New journal, mapping commit, and history redaction reject swallowed WriteError."""
    cache, deletions = runtime.cache, runtime.deletions
    path = folder / "native-delete.png"
    Image.new("RGB", (16, 12), "red").save(path)
    url = "/local/activity_images/native-delete.png"
    await cache.async_remember("Native historical alias", url)
    history = runtime.queue._new_job("Native deleted history", "manual")
    history.update(state="accepted", result_url=url)
    runtime.queue._jobs.append(history)
    await runtime.queue._save()
    plan = await deletions.async_plan([runtime.library.image_id(path.name)])
    native = write_utf8_file_atomic
    failing_paths = set()
    def fail_native(filename, *args, **kwargs):
        if filename in failing_paths:
            raise WriteError("Injected native deletion failure")
        return native(filename, *args, **kwargs)
    with patch.object(ha_storage, "write_utf8_file_atomic", fail_native):
        failing_paths.add(cache._store.path)
        try:
            await deletions.async_delete(plan["token"], False, True)
        except OSError:
            pass
        else:
            raise AssertionError("Undurable journal authorized deletion")
        assert path.exists() and cache.exact("Native historical alias") == url
        failing_paths.clear()
        with patch.object(deletions, "_kick", lambda: None):
            operation = await deletions.async_delete(plan["token"], False, True)
        commit = cache.async_commit_deletion
        async def fail_commit(*args):
            failing_paths.add(cache._store.path)
            try:
                return await commit(*args)
            finally:
                failing_paths.clear()
        with patch.object(cache, "async_commit_deletion", fail_commit):
            await deletions._run()
        assert deletions._operation(operation["id"])["state"] == "failed"
        assert path.exists() and cache.exact("Native historical alias") == url
        assert not cache.is_deleted(url)
        retry = await deletions.async_retry_plan(operation["id"])
        assert retry["replacement_requests"] == 0
        # Commit succeeds, but job-history Store failure must still prevent unlink.
        failing_paths.add(runtime.queue._store.path)
        await deletions.async_retry(operation["id"], retry["token"], True)
        await deletions._task
        assert deletions._operation(operation["id"])["state"] == "cleanup_failed"
        assert path.exists() and cache.exact("Native historical alias") is None and cache.is_deleted(url)
        failing_paths.clear()
        retry = await deletions.async_retry_plan(operation["id"])
        assert retry["replacement_requests"] == 0
        await deletions.async_retry(operation["id"], retry["token"], True)
        await deletions._task
        assert deletions._operation(operation["id"])["state"] == "done" and not path.exists()


async def stopping_writes_never_commit_after_reported_failure():
    from custom_components.activity_images.engine import ActivityImageGenerator
    from custom_components.activity_images.image_cache import ActivityImageCache
    from custom_components.activity_images.image_queue import ActivityImageQueue
    with tempfile.TemporaryDirectory(prefix="activity-stopping-smoke-") as directory:
        hass = HomeAssistant(directory)
        cache = ActivityImageCache(hass)
        await cache.async_setup()
        old, new = "/local/activity_images/old.png", "/local/activity_images/new.png"
        await cache.async_remember("Selected", old)
        engine = ActivityImageGenerator(hass, cache, {})
        queue = ActivityImageQueue(hass, engine)
        await queue.async_setup()
        candidate = queue._new_job("Selected", "manual")
        candidate.update(state="review", result_url=new)
        queue._jobs.append(candidate)
        await queue._save()
        cache_generation, queue_generation = cache._save_generation, queue._save_generation
        hass.set_state(CoreState.stopping)
        operations = (
            cache.async_link("Selected", new, replace=True),
            cache.async_record_image(new, {"origin": "generated"}),
            queue.async_candidate("Rejected During Stop"),
            queue.async_accept(candidate["id"]),
        )
        for operation in operations:
            try:
                await operation
            except OSError as exc:
                assert "is stopping" in str(exc)
            else:
                raise AssertionError("Stopping mutation must fail before Store retains data")
        assert cache.exact("Selected") == old
        assert cache.image_metadata(new) == {}
        assert queue._job(candidate["id"])["state"] == "review"
        assert cache._save_generation == cache_generation
        assert queue._save_generation == queue_generation
        await queue.async_shutdown()  # No false unload error or deferred shutdown write.
        hass.bus.async_fire(EVENT_HOMEASSISTANT_FINAL_WRITE)
        await hass.async_block_till_done()
        fresh_cache = Store(hass, 1, "activity_images_image_cache", atomic_writes=True)
        data = await fresh_cache.async_load()
        assert data is not None
        assert data["entries"]["selected"]["image_url"] == old
        assert new not in data["images"]
        fresh_queue = Store(hass, 1, "activity_images_jobs", atomic_writes=True)
        data = await fresh_queue.async_load()
        assert data is not None
        assert data["pending"] == [] and data["jobs"][-1]["state"] == "review"

        # Graceful stop cancels running work without saving through deferred Store.
        hass.set_state(CoreState.running)
        resumed = ActivityImageQueue(hass, engine)
        await resumed.async_setup()
        started = asyncio.Event()
        async def blocked_provider(*args, **kwargs):
            started.set()
            await asyncio.Event().wait()
        with patch.object(engine, "async_generate", side_effect=blocked_provider) as provider:
            running = await resumed.async_candidate("Running At Stop")
            await started.wait()
            generation = resumed._save_generation
            hass.set_state(CoreState.stopping)
            await resumed.async_shutdown()
            assert resumed._save_generation == generation
            assert resumed._job(running["id"])["state"] == "interrupted"
            hass.bus.async_fire(EVENT_HOMEASSISTANT_FINAL_WRITE)
            await hass.async_block_till_done()
            data = await Store(hass, 1, "activity_images_jobs").async_load()
            assert data is not None
            assert data["jobs"][-1]["state"] == "running"
            hass.set_state(CoreState.running)
            restarted = ActivityImageQueue(hass, engine)
            await restarted.async_setup()
            assert restarted._job(running["id"])["state"] == "interrupted"
            assert restarted.status()["pending"] == []
            assert provider.call_count == 1  # No automatic paid replay.
            await restarted.async_shutdown()
        hass.set_state(CoreState.not_running)
        await hass.async_stop()


async def run():
    assert __version__ == "2026.11.0", __version__
    await stopping_writes_never_commit_after_reported_failure()
    with tempfile.TemporaryDirectory(prefix="activity-library-smoke-") as directory:
        hass: Any = HomeAssistant(directory)
        await prepare_http(hass)
        async def no_forward(*args):
            pass
        async def no_unload(*args):
            return True
        hass.config_entries = SimpleNamespace(async_forward_entry_setups=no_forward,
                                              async_unload_platforms=no_unload)
        admin = await hass.auth.async_create_user("Smoke Admin", group_ids=[GROUP_ID_ADMIN])
        regular = await hass.auth.async_create_user("Smoke User", group_ids=[GROUP_ID_USER])
        async def token(user):
            refresh = await hass.auth.async_create_refresh_token(user, client_id="http://localhost/")
            return hass.auth.async_create_access_token(refresh)
        admin_token, user_token = await token(admin), await token(regular)
        folder = Path(directory) / "www/activity_images"
        folder.mkdir(parents=True)
        await hass.http.async_register_static_paths([StaticPathConfig("/local", str(folder.parent), False)])
        Image.new("RGB", (16, 12), "green").save(folder / "standalone.png")
        Image.new("RGB", (800, 400), "blue").save(folder / "other.png")
        (folder / "corrupt.png").write_bytes(b"corrupt")
        (folder / "escape.png").symlink_to(folder / "standalone.png")
        # Exercise the exact real-HA serializer used by config/options HTTP views.
        config_flow = ActivityImagesConfigFlow()
        config_flow.hass = hass
        form = await config_flow.async_step_user()
        flow_manager: Any = SimpleNamespace()
        view = _BaseFlowManagerView[Any, Any](flow_manager)
        assert len(view._prepare_result_json(form)["data_schema"]) == 7
        options_flow = ActivityImagesOptionsFlow(Entry())
        options_flow.hass = hass
        options_form = await options_flow.async_step_init()
        assert len(view._prepare_result_json(options_form)["data_schema"]) == 7
        assert await async_setup(hass, {})
        entry: Any = Entry()
        hass.config_entries.async_entries = lambda domain: [entry]
        hass.config_entries.async_update_entry = lambda item, *, options: setattr(item, "options", options)
        assert await async_setup_entry(hass, entry)
        panel = hass.data[frontend.DATA_PANELS][PANEL_ROUTE]
        assert panel.require_admin
        assert panel.config is not None
        assert panel.config["_panel_custom"]["name"] == "activity-images-panel"
        assert panel.config["_panel_custom"]["module_url"] == MODULE_URL
        settings_commands = {k for k in hass.data["websocket_api"] if k.startswith("activity_images/settings/")}
        assert settings_commands == {
            "activity_images/settings/get",
            "activity_images/settings/save",
            "activity_images/settings/diagnostics",
        }
        library_commands = {k for k in hass.data["websocket_api"] if k.startswith("activity_images/library/")}
        assert len(library_commands) == 17
        resources_before = tuple(hass.http.app.router.resources())
        handlers_before = {k: hass.data["websocket_api"][k][0] for k in library_commands}
        # A real aiohttp test server runs HA's actual bearer authentication middleware,
        # view wrapper, WS auth phase, schema validation and command decorators.
        async with TestClient(TestServer(hass.http.app)) as client:
            async def connect(access_token):
                ws = await client.ws_connect("/api/websocket")
                assert (await ws.receive_json())["type"] == "auth_required"
                await ws.send_json({"type": "auth", "access_token": access_token})
                assert (await ws.receive_json())["type"] == "auth_ok"
                return ws
            anonymous = await client.ws_connect("/api/websocket")
            assert (await anonymous.receive_json())["type"] == "auth_required"
            await anonymous.send_json({"type": "auth", "access_token": "invalid-smoke-token"})
            assert (await anonymous.receive_json())["type"] == "auth_invalid"
            await anonymous.close()
            admin_ws = await connect(admin_token)
            user_ws = await connect(user_token)
            counter = 0
            async def command(ws, name, **kwargs):
                nonlocal counter
                counter += 1
                await ws.send_json({"id": counter, "type": "activity_images/library/" + name, **kwargs})
                message = await asyncio.wait_for(ws.receive_json(), 5)
                assert message["id"] == counter, message
                return message
            for name, args in {
                "list": {}, "get": {"image_id": "x"}, "link": {"image_id": "x", "title": "Test"},
                "unlink": {"image_id": "x", "title": "Test"}, "generate": {"title": "Test"},
                "jobs": {}, "accept": {"job_id": "x"}, "discard": {"job_id": "x"},
                "cancel": {"titles": ["Test"]}, "retry": {"title": "Test"}, "clear_errors": {},
                "delete_plan": {"image_ids": ["a" * 32]}, "delete": {"token": "x", "regenerate": False, "confirm": True},
                "deletions": {}, "delete_retry_plan": {"operation_id": "x"},
                "delete_retry": {"operation_id": "x", "token": "x", "confirm": True},
                "delete_abandon": {"operation_id": "x", "confirm": True},
            }.items():
                denied = await command(user_ws, name, **args)
                assert not denied["success"] and denied["error"]["code"] == "unauthorized", denied
            for settings_type, data in (
                ("get", {}),
                ("save", {"revision": "0" * 64, "values": {}}),
                ("diagnostics", {}),
            ):
                counter += 1
                await user_ws.send_json({
                    "id": counter, "type": "activity_images/settings/" + settings_type, **data,
                })
                denied = await asyncio.wait_for(user_ws.receive_json(), 5)
                assert denied["id"] == counter, denied
                assert not denied["success"] and denied["error"]["code"] == "unauthorized", denied
            forbidden_preview = "/api/activity_images/settings/reference?media_id=media-source%3A%2F%2Fmedia_source%2Flocal%2Freference.png"
            assert (await client.get(forbidden_preview)).status == 401
            assert (await client.get(forbidden_preview, headers={"Authorization": "Bearer " + user_token})).status == 401
            async def settings_command(name, **data):
                nonlocal counter
                counter += 1
                await admin_ws.send_json({"id": counter, "type": "activity_images/settings/" + name, **data})
                answer = await asyncio.wait_for(admin_ws.receive_json(), 5)
                assert answer["id"] == counter, answer
                return answer
            snapshot = await settings_command("get")
            assert snapshot["success"], snapshot
            revision = snapshot["result"]["revision"]
            assert len(revision) == 64
            invalid_settings = await settings_command("save", revision=revision, values={"bad_key": "x"})
            assert invalid_settings["error"]["code"] == "settings_error", invalid_settings
            saved = await settings_command("save", revision=revision, values={"calendar_lookahead_days": 21})
            assert saved["success"] and saved["result"]["changed"], saved
            assert entry.options["calendar_lookahead_days"] == 21
            stale = await settings_command("save", revision=revision, values={"calendar_lookahead_days": 22})
            assert stale["error"]["code"] == "settings_conflict", stale
            diagnostics = await settings_command("diagnostics")
            assert diagnostics["success"] and diagnostics["result"]["provider"]["status"] == "not_configured"
            listed = await command(admin_ws, "list", limit=1)
            assert listed["success"], listed
            assert listed["result"]["total"] == 2
            selected = listed["result"]["items"][0]
            image_url, image_id = selected["image_url"], selected["id"]
            response = await client.get(image_url)
            assert response.status == 401
            response = await client.get(image_url, headers={"Authorization": "Bearer " + user_token})
            assert response.status == 401
            response = await client.get(image_url, headers={"Authorization": "Bearer " + admin_token})
            assert response.status == 200
            assert response.content_type == "image/png"
            assert response.headers["Cache-Control"] == "private, no-store"
            assert await response.read() == (folder / selected["filename"]).read_bytes()
            for args in ({"limit": 97}, {"offset": -1}, {"query": "x" * 256}, {"filter": "imported"}):
                invalid = await command(admin_ws, "list", **args)
                assert not invalid["success"] and invalid["error"]["code"] == "invalid_format", invalid
            assert not (await command(admin_ws, "get", image_id="../../private"))["success"]
            assert (await command(admin_ws, "link", image_id=image_id, title="Selected Activity"))["success"]
            others = await command(admin_ws, "list")
            other_id = next(item["id"] for item in others["result"]["items"] if item["id"] != image_id)
            conflict = await command(admin_ws, "link", image_id=other_id, title="Selected Activity")
            assert conflict["error"]["code"] == "conflict"
            assert (await command(admin_ws, "link", image_id=other_id, title="Selected Activity", replace=True))["success"]
            assert (await command(admin_ws, "unlink", image_id=other_id, title="Selected Activity"))["success"]
            # Running provider calls cannot happen in this smoke: the queue is frozen.
            with patch.object(entry.runtime_data.queue, "_kick", lambda: None):
                generated = await command(admin_ws, "generate", title="Explicit Candidate")
                assert generated["success"] and generated["result"]["state"] == "pending", generated
                duplicate = await command(admin_ws, "generate", title="EXPLICIT CANDIDATE!")
                assert duplicate["result"]["id"] == generated["result"]["id"]
                cancelled = await command(admin_ws, "cancel", titles=["Explicit Candidate"])
                assert cancelled["result"]["cancelled"] == 1
            # A synthetic provider result tests candidate transport/CAS without a paid request.
            queue = entry.runtime_data.queue
            candidate = queue._new_job("Stale Candidate", "manual")
            candidate.update(state="review", result_url="/local/activity_images/" + selected["filename"])
            queue._jobs.append(candidate)
            jobs = await command(admin_ws, "jobs")
            preview = next(job for job in jobs["result"]["jobs"] if job["id"] == candidate["id"])
            assert preview["image_url"].startswith("/api/activity_images/library/")
            assert preview["image_id"] == image_id
            assert preview["thumbnail_url"].endswith("/preview")
            assert (await client.get(preview["thumbnail_url"])).status == 401
            assert (await client.get(preview["thumbnail_url"], headers={"Authorization": "Bearer " + user_token})).status == 401
            response = await client.get(preview["thumbnail_url"], headers={"Authorization": "Bearer " + admin_token})
            assert response.status == 200 and response.content_type == "image/png"
            with Image.open(BytesIO(await response.read())) as thumbnail:
                assert thumbnail.size == (256, 128)
            await entry.runtime_data.cache.async_remember("Stale Candidate", "/local/activity_images/changed.png")
            stale = await command(admin_ws, "accept", job_id=candidate["id"])
            assert stale["error"]["code"] == "conflict", stale
            assert (await command(admin_ws, "discard", job_id=candidate["id"]))["success"]
            # Test public action validation through the actual current HA registry.
            for data in ({"title": "   "}, {"title": "!!!"}, {"title": 123}):
                try:
                    await hass.services.async_call("activity_images", "generate_image", data, blocking=True)
                except (probatio.Invalid, HomeAssistantError) as exc:
                    assert str(exc)
                else:
                    raise AssertionError("Invalid public action title accepted")
            try:
                await hass.services.async_call("activity_images", "link_existing_image", {
                    "title": "Escape", "image_url": "/local/activity_images/escape.png",
                }, blocking=True)
            except HomeAssistantError as exc:
                assert "missing or invalid" in str(exc)
            else:
                raise AssertionError("Public link accepted a symlink")
            await native_write_failures(entry.runtime_data)
            await native_deletion_failures(entry.runtime_data, folder)
            Image.new("RGB", (16, 12), "red").save(folder / "abandon.png")
            abandon_id = entry.runtime_data.library.image_id("abandon.png")
            abandon_plan = await command(admin_ws, "delete_plan", image_ids=[abandon_id])
            with patch.object(entry.runtime_data.cache, "async_commit_deletion", side_effect=OSError("Synthetic precommit failure")):
                abandon_op = await command(admin_ws, "delete", token=abandon_plan["result"]["token"], regenerate=False, confirm=True)
                await entry.runtime_data.deletions._task
            abandon_op_id = abandon_op["result"]["id"]
            native_write = write_utf8_file_atomic
            def fail_abandon_write(filename, *args, **kwargs):
                if filename == entry.runtime_data.cache._store.path:
                    raise WriteError("Injected abandonment journal failure")
                return native_write(filename, *args, **kwargs)
            with patch.object(ha_storage, "write_utf8_file_atomic", fail_abandon_write):
                rejected = await command(admin_ws, "delete_abandon", operation_id=abandon_op_id, confirm=True)
                assert not rejected["success"], rejected
            assert entry.runtime_data.cache.reserved(url="/local/activity_images/abandon.png")
            rejected = await command(admin_ws, "delete_abandon", operation_id=abandon_op_id, confirm=False)
            assert not rejected["success"], rejected
            abandoned = await command(admin_ws, "delete_abandon", operation_id=abandon_op_id, confirm=True)
            assert abandoned["success"] and abandoned["result"]["state"] == "abandoned" and not abandoned["result"]["can_abandon"], abandoned
            assert not entry.runtime_data.cache.reserved(url="/local/activity_images/abandon.png")
            assert (folder / "abandon.png").exists()
            assert not (await command(admin_ws, "delete_plan", image_ids=[abandon_id]))["result"]["items"][0]["blocked_reason"]
            Image.new("RGB", (16, 12), "red").save(folder / "delete-me.png")
            Image.new("RGB", (8, 6), "red").save(folder / "delete-me_thumb.webp")
            delete_id = entry.runtime_data.library.image_id("delete-me.png")
            for args in ({"image_ids": []}, {"image_ids": ["a"] * 65}):
                invalid = await command(admin_ws, "delete_plan", **args)
                assert not invalid["success"] and invalid["error"]["code"] == "invalid_format", invalid
            assert not (await command(admin_ws, "delete_plan", image_ids=["../../private"]))["success"]
            planned = await command(admin_ws, "delete_plan", image_ids=[delete_id])
            assert planned["success"] and planned["result"]["replacement_requests"] == 0, planned
            rejected = await command(admin_ws, "delete", token=planned["result"]["token"], regenerate=False, confirm=False)
            assert not rejected["success"], rejected
            with patch("custom_components.activity_images.library_deletion.cleanup", side_effect=OSError("Synthetic cleanup failure")):
                deleted = await command(admin_ws, "delete", token=planned["result"]["token"], regenerate=False, confirm=True)
                assert deleted["success"], deleted
                await entry.runtime_data.deletions._task
            retry_plan = await command(admin_ws, "delete_retry_plan", operation_id=deleted["result"]["id"])
            assert retry_plan["success"] and retry_plan["result"]["replacement_requests"] == 0, retry_plan
            retried = await command(admin_ws, "delete_retry", operation_id=deleted["result"]["id"], token=retry_plan["result"]["token"], confirm=True)
            assert retried["success"], retried
            await entry.runtime_data.deletions._task
            repeated = await command(admin_ws, "delete_retry", operation_id=deleted["result"]["id"], token=retry_plan["result"]["token"], confirm=True)
            assert repeated["success"] and repeated["result"]["id"] == deleted["result"]["id"]
            history = await command(admin_ws, "deletions")
            assert any(op["id"] == deleted["result"]["id"] and op["state"] == "done" for op in history["result"]["operations"])
            status = await command(admin_ws, "jobs")
            assert status["result"]["operations"] == history["result"]["operations"]
            assert not (folder / "delete-me.png").exists() and not (folder / "delete-me_thumb.webp").exists()
            assert not (await command(admin_ws, "delete_retry_plan", operation_id=deleted["result"]["id"]))["success"]
            Image.new("RGB", (16, 12), "yellow").save(folder / "private-cleanup.png")
            public_path = "/local/activity_images/private-cleanup.png"
            assert (await client.get(public_path)).status == 200
            private_plan = await command(admin_ws, "delete_plan", image_ids=[entry.runtime_data.library.image_id("private-cleanup.png")])
            native_unlink = deletion_files.os.unlink
            def leave_quarantined(path, *args, **kwargs):
                if isinstance(path, str) and len(path) == 64 and "dir_fd" in kwargs:
                    raise OSError("Synthetic unlink failure after private rename")
                return native_unlink(path, *args, **kwargs)
            with patch.object(deletion_files.os, "unlink", leave_quarantined):
                private_op = await command(admin_ws, "delete", token=private_plan["result"]["token"], regenerate=False, confirm=True)
                assert private_op["success"], private_op
                await entry.runtime_data.deletions._task
            private_root = Path(directory) / ".storage/activity_images_deletions"
            retained = list(private_root.iterdir())
            assert retained and (await client.get(public_path)).status == 404
            for path in (f"/local/activity_images/.deletions/{retained[0].name}",
                         f"/local/activity_images_deletions/{retained[0].name}",
                         f"/.storage/activity_images_deletions/{retained[0].name}",
                         f"/local/../.storage/activity_images_deletions/{retained[0].name}"):
                assert (await client.get(path)).status == 404, path
            assert not (folder / ".deletions").exists()
            retry_plan = await command(admin_ws, "delete_retry_plan", operation_id=private_op["result"]["id"])
            assert retry_plan["result"]["replacement_requests"] == 0
            assert (await command(admin_ws, "delete_retry", operation_id=private_op["result"]["id"], token=retry_plan["result"]["token"], confirm=True))["success"]
            await entry.runtime_data.deletions._task
            assert not list(private_root.iterdir())
            # Manager-only clear must preserve legacy paid retry authorization barriers.
            entry.runtime_data.queue._failed["manager legacy failure"] = "old error"
            assert (await command(admin_ws, "clear_errors"))["success"]
            assert await entry.runtime_data.queue.async_enqueue(["Manager Legacy Failure"]) == []
            assert entry.runtime_data.queue.status()["failed"] == {}
            assert "manager legacy failure" in entry.runtime_data.queue.status()["retry_blocked"]
            old_runtime = entry.runtime_data
            assert await async_unload_entry(hass, entry)
            assert not frontend.async_panel_exists(hass, PANEL_ROUTE)
            unavailable = await command(admin_ws, "list")
            assert not unavailable["success"] and unavailable["error"]["code"] == "library_error"
            response = await client.get(image_url, headers={"Authorization": "Bearer " + admin_token})
            assert response.status == 503
            assert await async_setup_entry(hass, entry)
            assert entry.runtime_data is not old_runtime
            assert tuple(hass.http.app.router.resources()) == resources_before
            assert {k: hass.data["websocket_api"][k][0] for k in library_commands} == handlers_before
            assert (await command(admin_ws, "list"))["success"]
            response = await client.get(image_url, headers={"Authorization": "Bearer " + admin_token})
            assert response.status == 200
            # Only the exact bundle URL has a public static handler; no directory exposure.
            paths = [resource.canonical for resource in resources_before]
            assert MODULE_URL in paths
            assert "/activity_images" not in paths
            for path in ("/activity_images/library.py", "/activity_images/image_cache.py", "/activity_images/frontend/"):
                assert (await client.get(path)).status == 404
            bundle = Path(__file__).resolve().parents[1] / "custom_components/activity_images/frontend/activity-images-panel.js"
            response = await client.get(MODULE_URL)
            assert response.status == (200 if bundle.exists() else 404)
            await admin_ws.close()
            await user_ws.close()
            assert await async_unload_entry(hass, entry)
        await hass.async_stop()
        print("HA_2026_11_LIBRARY_SMOKE_PASS: real bearer/WS auth, schema bounds, conflict, protected bytes, reload registrations; no AI calls")
        if not bundle.exists():
            print("Bundle content not checked: frontend lane has not supplied the file yet; exact static route verified")


if __name__ == "__main__":
    asyncio.run(run())

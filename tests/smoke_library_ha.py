"""Real HA 2026.11 auth/HTTP/WS/reload smoke in a disposable local process.

Run with .venv/bin/python tests/smoke_library_ha.py. No live HA or AI calls.
"""
from __future__ import annotations

import asyncio
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import probatio
from aiohttp.test_utils import TestClient, TestServer
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from homeassistant.auth import auth_manager_from_config  # noqa: E402
from homeassistant.auth.const import GROUP_ID_ADMIN, GROUP_ID_USER  # noqa: E402
from homeassistant.components import frontend, websocket_api  # noqa: E402
from homeassistant.components.http.server import HomeAssistantHTTP  # noqa: E402
from homeassistant.const import __version__  # noqa: E402
from homeassistant.core import HomeAssistant  # noqa: E402
from homeassistant.exceptions import HomeAssistantError  # noqa: E402
from homeassistant.helpers import device_registry as dr  # noqa: E402
from homeassistant.helpers.data_entry_flow import _BaseFlowManagerView  # noqa: E402
from homeassistant.util.ssl import SSLProfile  # noqa: E402

from custom_components.activity_images import (  # noqa: E402
    async_setup,
    async_setup_entry,
    async_unload_entry,
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


async def run():
    assert __version__ == "2026.11.0", __version__
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
        Image.new("RGB", (16, 12), "green").save(folder / "standalone.png")
        Image.new("RGB", (16, 12), "blue").save(folder / "other.png")
        (folder / "corrupt.png").write_bytes(b"corrupt")
        (folder / "escape.png").symlink_to(folder / "standalone.png")
        # Exercise the exact real-HA serializer used by config/options HTTP views.
        config_flow = ActivityImagesConfigFlow()
        config_flow.hass = hass
        form = await config_flow.async_step_user()
        flow_manager: Any = SimpleNamespace()
        view = _BaseFlowManagerView[Any, Any](flow_manager)
        assert len(view._prepare_result_json(form)["data_schema"]) == 6
        options_flow = ActivityImagesOptionsFlow(Entry())
        options_flow.hass = hass
        options_form = await options_flow.async_step_init()
        assert len(view._prepare_result_json(options_form)["data_schema"]) == 6
        assert await async_setup(hass, {})
        entry: Any = Entry()
        assert await async_setup_entry(hass, entry)
        panel = hass.data[frontend.DATA_PANELS][PANEL_ROUTE]
        assert panel.require_admin
        assert panel.config is not None
        assert panel.config["_panel_custom"]["name"] == "activity-images-panel"
        assert panel.config["_panel_custom"]["module_url"] == MODULE_URL
        library_commands = {k for k in hass.data["websocket_api"] if k.startswith("activity_images/library/")}
        assert len(library_commands) == 11
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
            }.items():
                denied = await command(user_ws, name, **args)
                assert not denied["success"] and denied["error"]["code"] == "unauthorized", denied
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

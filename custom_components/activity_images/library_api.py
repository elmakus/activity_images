"""Admin-only library transport and reload-safe Home Assistant registrations."""
from __future__ import annotations

from pathlib import Path

import probatio as p
from aiohttp import web
from homeassistant.components import frontend, panel_custom, websocket_api
from homeassistant.components.http import (
    KEY_HASS,
    HomeAssistantView,
    StaticPathConfig,
    require_admin,
)
from homeassistant.exceptions import HomeAssistantError

from .const import DOMAIN
from .image_cache import MappingConflict
from .library import IMAGE_API, validate_title

PANEL_ROUTE = "activity-images"
MODULE_URL = "/activity_images/activity-images-panel.js"
_REGISTRATION = f"{DOMAIN}_library_registration"
_TITLE = p.All(str, p.Length(min=1, max=255))
_ID = p.All(str, p.Length(min=1, max=128))
_SCHEMAS = {
    "list": {
        p.Optional("query", default=""): p.All(str, p.Length(max=255)),
        p.Optional("filter", default="all"): p.In(["all", "linked", "unlinked", "generated"]),
        p.Optional("offset", default=0): p.All(int, p.Range(min=0)),
        p.Optional("limit", default=48): p.All(int, p.Range(min=1, max=96)),
        p.Optional("refresh", default=False): bool,
    },
    "get": {p.Required("image_id"): _ID},
    "link": {p.Required("image_id"): _ID, p.Required("title"): _TITLE, p.Optional("replace", default=False): bool},
    "unlink": {p.Required("image_id"): _ID, p.Required("title"): _TITLE},
    "generate": {p.Required("title"): _TITLE, p.Optional("image_id"): _ID},
    "jobs": {},
    "accept": {p.Required("job_id"): _ID},
    "discard": {p.Required("job_id"): _ID},
    "cancel": {p.Required("titles"): p.All([_TITLE], p.Length(min=1, max=64))},
    "retry": {p.Optional("job_id"): _ID, p.Optional("title"): _TITLE},
    "clear_errors": {},
}


def _runtime(hass):
    runtimes = hass.data.get(DOMAIN, {})
    if not runtimes:
        raise HomeAssistantError("Activity Images is not configured")
    return next(iter(runtimes.values()))


async def _dispatch(hass, command: str, args: dict):
    runtime = _runtime(hass)
    library, queue = runtime.library, runtime.queue
    if command == "list":
        return await library.async_list(**args)
    if command == "get":
        return await library.async_get(args["image_id"])
    if command in {"link", "unlink"}:
        title = validate_title(args["title"])
        url = await library.async_url(args["image_id"])
        if command == "link":
            await runtime.cache.async_link(title, url, replace=args["replace"])
        else:
            await runtime.cache.async_unlink(title, url)
        await runtime.calendar.async_reconcile()
        return {"title": title, "image_url": url, "linked": command == "link"}
    if command == "generate":
        return await library.async_generate(queue, **args)
    if command == "jobs":
        return queue.status()
    if command == "accept":
        result = queue.candidate_url(args["job_id"])
        if result:
            await library.async_url(library.image_id(result.split("?", 1)[0].rsplit("/", 1)[-1]))
        response = await queue.async_accept(args["job_id"])
        library.invalidate()
        await runtime.calendar.async_reconcile()
        return response
    if command == "discard":
        return await queue.async_discard(args["job_id"])
    if command == "cancel":
        return {"cancelled": await queue.async_cancel(args["titles"])}
    if command == "retry":
        if ("job_id" in args) == ("title" in args):
            raise ValueError("Specify exactly one of job_id or title")
        return await queue.async_retry(**args)
    if command == "clear_errors":
        return {"cleared": await queue.async_clear_errors()}
    raise ValueError("Unknown command")


def _handler(command: str, fields: dict):
    @websocket_api.websocket_command({"type": f"{DOMAIN}/library/{command}", **fields})
    @websocket_api.require_admin
    @websocket_api.async_response
    async def handle(hass, connection, msg):
        try:
            result = await _dispatch(hass, command, {k: v for k, v in msg.items() if k not in {"id", "type"}})
        except MappingConflict as exc:
            connection.send_error(msg["id"], "conflict", str(exc))
        except (ValueError, OSError, HomeAssistantError) as exc:
            connection.send_error(msg["id"], "library_error", str(exc))
        else:
            connection.send_result(msg["id"], result)
    return handle


class LibraryImageView(HomeAssistantView):
    url = IMAGE_API + "/{image_id}/{asset}"
    name = "api:activity_images:library"
    requires_auth = True

    @require_admin
    async def get(self, request, image_id, asset):
        try:
            runtime = _runtime(request.app[KEY_HASS])
            data, content_type = await runtime.library.async_read(image_id, asset)
        except HomeAssistantError as exc:
            raise web.HTTPServiceUnavailable(text=str(exc)) from exc
        except (ValueError, OSError) as exc:
            raise web.HTTPNotFound() from exc
        return web.Response(body=data, content_type=content_type, headers={
            "Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff",
        })


async def async_register_manager(hass) -> None:
    """Durable routes resolve the current runtime; the panel is entry-owned."""
    registered = hass.data.setdefault(_REGISTRATION, set())
    if "static" not in registered:
        await hass.http.async_register_static_paths([StaticPathConfig(
            MODULE_URL, str(Path(__file__).parent / "frontend" / "activity-images-panel.js"), False,
        )])
        registered.add("static")
    if "http" not in registered:
        hass.http.register_view(LibraryImageView())
        registered.add("http")
    if "ws" not in registered:
        for command, fields in _SCHEMAS.items():
            websocket_api.async_register_command(hass, _handler(command, fields))
        registered.add("ws")
    await panel_custom.async_register_panel(
        hass, frontend_url_path=PANEL_ROUTE, webcomponent_name="activity-images-panel",
        sidebar_title="Activity Images", sidebar_icon="mdi:image-multiple",
        module_url=MODULE_URL, require_admin=True,
    )


def async_remove_manager(hass) -> None:
    frontend.async_remove_panel(hass, PANEL_ROUTE, warn_if_unknown=False)

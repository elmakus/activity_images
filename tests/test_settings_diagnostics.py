"""Passive diagnostics and reference file guard tests."""
import importlib
import sys
import types
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image

ROOT = Path(__file__).resolve().parents[1] / "custom_components" / "activity_images"
for name, root in (("custom_components", ROOT.parent), ("custom_components.activity_images", ROOT)):
    if name not in sys.modules:
        pkg = types.ModuleType(name)
        pkg.__path__ = [str(root)]
        sys.modules[name] = pkg

const = importlib.import_module("custom_components.activity_images.const")
diag = importlib.import_module("custom_components.activity_images.settings_diagnostics")


def test_diagnostics_are_read_only_and_service_safe():
    service_calls = []
    class Services:
        def has_service(self, domain, name):
            service_calls.append((domain, name))
            return domain == "ai_task"
    hass = SimpleNamespace(
        states=SimpleNamespace(get=lambda e: SimpleNamespace(state="idle") if e in ("ai_task.mock", "calendar.family") else None),
        services=Services(),
    )
    entry = SimpleNamespace(data={}, options={
        const.CONF_AI_ENTITY: "ai_task.mock",
        const.CONF_CALENDARS: ["calendar.family", "calendar.work"],
        const.CONF_LUCIDA_URL: "http://lucida.local:7000/",
    })
    result = diag.diagnostics(hass, entry)
    assert result["provider"]["status"] == "available"
    assert result["lucida"]["status"] == "configured_not_probed"
    assert [x["status"] for x in result["calendar"]["entities"]] == ["available", "unavailable"]
    assert service_calls == [("ai_task", "generate_image"), ("calendar", "get_events")]


def test_reference_preview_rejects_non_images_and_oversized_files(tmp_path):
    # Import media helpers via an isolated Home Assistant stub.
    aiohttp = pytest.importorskip("aiohttp")
    del aiohttp
    if "homeassistant" not in sys.modules:
        pkg = types.ModuleType("homeassistant")
        pkg.__path__ = []
        sys.modules["homeassistant"] = pkg
    if "homeassistant.components" not in sys.modules:
        pkg = types.ModuleType("homeassistant.components")
        pkg.__path__ = []
        sys.modules["homeassistant.components"] = pkg
    http = sys.modules.setdefault("homeassistant.components.http", types.ModuleType("ha_http"))
    http.KEY_HASS = "hass"
    http.HomeAssistantView = object
    http.require_admin = lambda f: f
    source = sys.modules.setdefault("homeassistant.components.media_source", types.ModuleType("media_source"))
    source.async_resolve_media = lambda *_: None
    media = importlib.import_module("custom_components.activity_images.settings_media")
    path = tmp_path / "ref.png"
    Image.new("RGB", (64, 64), (128, 20, 50)).save(path)
    data, kind = media.read_preview(path)
    assert kind == "image/png" and data
    path.write_bytes(b"<script>alert(1)</script>")
    with pytest.raises((ValueError, OSError)):
        media.read_preview(path)
    path.write_bytes(b"x" * (media.MAX_REFERENCE_BYTES + 1))
    with pytest.raises(ValueError):
        media.read_preview(path)

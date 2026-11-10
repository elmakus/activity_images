"""Passive diagnostics and reference file guard tests."""
import importlib
import os
import stat
import sys
import types
from enum import IntFlag
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

# The fast suite deliberately stubs HA; native transport runs in separate smokes.
for name in ("homeassistant", "homeassistant.components", "homeassistant.components.ai_task"):
    if name not in sys.modules:
        package = types.ModuleType(name)
        package.__path__ = []
        sys.modules[name] = package
features = sys.modules.setdefault("homeassistant.components.ai_task.const", types.ModuleType("ai_task_const"))
features.__dict__["AITaskEntityFeature"] = IntFlag("AITaskEntityFeature", {"GENERATE_IMAGE":4,"SUPPORT_ATTACHMENTS":2})
# Faithful empty entity registry for ownership checks; the shared fake suite
# provides the same mock when the full session already stubbed this module.
sys.modules.setdefault("homeassistant.helpers", types.ModuleType("homeassistant.helpers"))
er = sys.modules.setdefault("homeassistant.helpers.entity_registry", types.ModuleType("homeassistant.helpers.entity_registry"))
if not hasattr(er, "async_get"):
    er.__dict__["async_get"] = lambda hass: SimpleNamespace(entities=hass.registry, async_get=hass.registry.get)
if not hasattr(er, "async_entries_for_config_entry"):
    er.__dict__["async_entries_for_config_entry"] = (
        lambda registry, config_entry_id: [entry for entry in registry.entities.values()
                                            if entry.config_entry_id == config_entry_id]
    )

const = importlib.import_module("custom_components.activity_images.const")
diag = importlib.import_module("custom_components.activity_images.settings_diagnostics")


def test_diagnostics_are_read_only_and_service_safe():
    service_calls = []
    class Services:
        def has_service(self, domain, name):
            service_calls.append((domain, name))
            return domain == "ai_task"
    hass = SimpleNamespace(
        states=SimpleNamespace(get=lambda e: SimpleNamespace(state="idle", attributes={"supported_features":4}) if e in ("ai_task.mock", "calendar.family") else None),
        services=Services(),
        registry={},
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


@pytest.mark.parametrize("capabilities,reference,expected", [
    (1,"","image_generation_unsupported"),
    (4,"","available"),
    (4,"media-source://media_source/local/ref.png","attachments_unsupported"),
    (6,"media-source://media_source/local/ref.jpg","available"),
    ("bad","","image_generation_unsupported"),
])
def test_diagnostics_check_selected_provider_capabilities(capabilities, reference, expected):
    state = SimpleNamespace(state="idle", attributes={"supported_features":capabilities})
    hass = SimpleNamespace(states=SimpleNamespace(get=lambda _:state),
                           services=SimpleNamespace(has_service=lambda *_:True),
                           registry={})
    entry = SimpleNamespace(data={}, options={const.CONF_AI_ENTITY:"ai_task.test",
                                              const.CONF_REFERENCE_MEDIA_ID:reference})
    result = diag.diagnostics(hass, entry)
    assert result["provider"]["status"] == expected
    assert isinstance(result["provider"]["attachments_supported"],bool)


def test_reference_preview_rejects_non_images_and_oversized_files(tmp_path, monkeypatch):
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
    http.__dict__.update(KEY_HASS="hass", HomeAssistantView=object, require_admin=lambda f:f)
    source = sys.modules.setdefault("homeassistant.components.media_source", types.ModuleType("media_source"))
    source.__dict__["async_resolve_media"] = lambda *_: None
    source.__dict__.setdefault("MediaSourceError", type("MediaSourceError", (Exception,), {}))
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
    Image.new("RGB", (4, 4)).save(path)
    native_open = os.open
    flags = []
    def record_open(path, mode):
        flags.append(mode)
        return native_open(path, mode)
    native_stat = os.fstat
    def nonregular(fd):
        fields = list(native_stat(fd))
        fields[0] = stat.S_IFIFO
        return os.stat_result(fields)
    with monkeypatch.context() as scoped:
        scoped.setattr(os, "open", record_open)
        scoped.setattr(os, "fstat", nonregular)
        with pytest.raises(ValueError, match="regular"):
            media.read_preview(path)
    assert flags[0] & os.O_NONBLOCK

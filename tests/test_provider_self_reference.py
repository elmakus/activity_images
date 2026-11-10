"""Provider self-reference and fuzzy-tie regressions (fake HA, no paid calls)."""
# The HA-free harness deliberately supplies dynamic modules and runtime state.
# pyright: reportAttributeAccessIssue=false
# pyright: reportOptionalMemberAccess=false
# pyright: reportArgumentType=false
# pyright: reportGeneralTypeIssues=false
from __future__ import annotations

import importlib
import sys
import types
from enum import IntFlag
from types import SimpleNamespace

import pytest
import test_behavior as behavior
from PIL import Image

behavior._load_integration_entrypoint()

const = behavior.const_module
settings_api = importlib.import_module("custom_components.activity_images.settings")
settings_schema = importlib.import_module("custom_components.activity_images.settings_schema")

# Diagnostics needs only passive feature bits, like the existing diagnostics tests.
for _name in ("homeassistant.components.ai_task", "homeassistant.components.ai_task.const"):
    if _name not in sys.modules:
        _pkg = types.ModuleType(_name)
        _pkg.__path__ = []
        sys.modules[_name] = _pkg
sys.modules["homeassistant.components.ai_task.const"].__dict__.setdefault(
    "AITaskEntityFeature", IntFlag("AITaskEntityFeature", {"GENERATE_IMAGE": 4, "SUPPORT_ATTACHMENTS": 2})
)
diag = importlib.import_module("custom_components.activity_images.settings_diagnostics")

ha = behavior.ha
configured = behavior.configured


def own_entry(ha, entity_id, entry_id):
    ha.registry[entity_id] = SimpleNamespace(
        id=f"registry-{entity_id}", entity_id=entity_id, name=None,
        original_name=entity_id, disabled_by=None, platform="activity_images",
        config_entry_id=entry_id, domain="ai_task",
    )


def real_entry(ha, entity_id):
    ha.registry[entity_id] = SimpleNamespace(
        id=f"registry-{entity_id}", entity_id=entity_id, name=None,
        original_name=entity_id, disabled_by=None, platform="test_provider",
        config_entry_id="real_entry", domain="ai_task",
    )


def test_predicate_uses_registry_ownership_not_hardcoded_id(ha):
    entry = SimpleNamespace(entry_id="entry1")
    own_entry(ha, "ai_task.own", "entry1")
    own_entry(ha, "ai_task.renamed_wrapper", "entry1")
    own_entry(ha, "ai_task.other_entry", "other_entry")
    real_entry(ha, "ai_task.real")
    assert settings_api.is_own_provider_entity(ha, entry, "ai_task.own")
    assert settings_api.is_own_provider_entity(ha, entry, "ai_task.renamed_wrapper")
    assert not settings_api.is_own_provider_entity(ha, entry, "ai_task.other_entry")
    assert not settings_api.is_own_provider_entity(ha, entry, "ai_task.real")
    assert not settings_api.is_own_provider_entity(ha, entry, "ai_task.unknown")
    assert not settings_api.is_own_provider_entity(ha, entry, "")
    assert not settings_api.is_own_provider_entity(ha, entry, None)
    assert not settings_api.is_own_provider_entity(ha, entry, "sensor.not_ai_task")
    assert settings_api.is_own_provider_entity(ha, None, "ai_task.own")
    assert settings_api.is_own_provider_entity(ha, None, "ai_task.other_entry")
    assert not settings_api.is_own_provider_entity(ha, None, "ai_task.real")
    assert settings_api.own_provider_entity_ids(ha, entry) == ["ai_task.own", "ai_task.renamed_wrapper"]


@pytest.mark.asyncio
async def test_empty_and_unknown_providers_remain_savable_but_self_is_rejected(ha):
    entry = SimpleNamespace(entry_id="entry1", data={}, options={})
    ha.config_entries.async_entries = lambda _domain: [entry]
    own_entry(ha, "ai_task.own", "entry1")
    revision = settings_api.revision(entry)
    result = await settings_api.save_settings(ha, {const.CONF_AI_ENTITY: ""}, revision)
    assert result["changed"] is False
    assert settings_api.read_settings(ha)["values"][const.CONF_AI_ENTITY] == ""
    revision = result["revision"]
    await settings_api.save_settings(ha, {const.CONF_AI_ENTITY: "ai_task.unknown"}, revision)
    assert entry.options[const.CONF_AI_ENTITY] == "ai_task.unknown"
    revision = settings_api.revision(entry)
    with pytest.raises(ValueError, match="itself"):
        await settings_api.save_settings(ha, {const.CONF_AI_ENTITY: "ai_task.own"}, revision)
    assert entry.options[const.CONF_AI_ENTITY] == "ai_task.unknown"
    assert not ha.services.calls


@pytest.mark.asyncio
async def test_both_option_writers_share_self_rejection(ha):
    entry = SimpleNamespace(entry_id="entry1", data={}, options={const.CONF_AI_ENTITY: "ai_task.real"})
    ha.config_entries.async_entries = lambda _domain: [entry]
    own_entry(ha, "ai_task.own", "entry1")
    real_entry(ha, "ai_task.real")
    revision = settings_api.revision(entry)
    full_values = {**settings_api.read_settings(ha)["values"], const.CONF_AI_ENTITY: "ai_task.own"}
    with pytest.raises(ValueError, match="itself"):
        settings_api.options_for_update(ha, entry, full_values, revision)
    with pytest.raises(ValueError, match="itself"):
        await settings_api.save_settings(ha, {const.CONF_AI_ENTITY: "ai_task.own"}, revision)
    assert entry.options[const.CONF_AI_ENTITY] == "ai_task.real"
    assert not ha.services.calls


def test_read_settings_exposes_additive_exclusions(ha):
    entry = SimpleNamespace(entry_id="entry1", data={const.CONF_AI_ENTITY: "ai_task.real"}, options={})
    ha.config_entries.async_entries = lambda _domain: [entry]
    own_entry(ha, "ai_task.own", "entry1")
    snapshot = settings_api.read_settings(ha)
    assert snapshot["excluded_providers"] == ["ai_task.own"]
    assert snapshot["values"][const.CONF_AI_ENTITY] == "ai_task.real"
    assert "excluded_providers" not in snapshot["values"]
    assert len(snapshot["revision"]) == 64


def test_diagnostics_self_reference_takes_precedence(ha):
    state = SimpleNamespace(state="idle", attributes={"supported_features": 4})
    hass = SimpleNamespace(states=SimpleNamespace(get=lambda _e: state),
                           services=SimpleNamespace(has_service=lambda *_a: True),
                           data={})
    # Fake registry for the ownership predicate only; states stay available.
    hass.registry = ha.registry
    entry = SimpleNamespace(entry_id="entry1", data={}, options={const.CONF_AI_ENTITY: "ai_task.own"})
    own_entry(ha, "ai_task.own", "entry1")
    # Point the diagnostics hass at the same fake registry entries.
    sys.modules["homeassistant.helpers.entity_registry"].async_get = lambda _h: SimpleNamespace(
        entities=ha.registry, async_get=ha.registry.get,
    )
    try:
        assert diag.diagnostics(hass, entry)["provider"]["status"] == "self_reference"
        entry.options[const.CONF_AI_ENTITY] = "ai_task.real"
        real_entry(ha, "ai_task.real")
        assert diag.diagnostics(hass, entry)["provider"]["status"] == "available"
    finally:
        sys.modules["homeassistant.helpers.entity_registry"].async_get = lambda _hass: SimpleNamespace(
            entities=_hass.registry if hasattr(_hass, "registry") else ha.registry,
            async_get=(getattr(_hass, "registry", ha.registry)).get,
        )


@pytest.mark.asyncio
async def test_engine_runtime_backstop_blocks_self_without_paid_calls(ha, configured):
    cache, engine = configured
    own_entry(ha, "ai_task.own", "any_entry")
    engine.settings[const.CONF_AI_ENTITY] = "ai_task.own"
    with pytest.raises(ValueError, match="itself"):
        await engine.async_generate("Runtime self dispatch")
    assert not ha.services.calls
    engine.settings[const.CONF_AI_ENTITY] = "ai_task.mock"
    result = await engine.async_generate("Runtime self dispatch")
    assert result["reused"] is False
    assert len(ha.services.calls) == 1


@pytest.mark.asyncio
async def test_wrapper_registry_check_keeps_entity_id_backstop(ha):
    wrapper_module, ai = behavior._load_ai_wrapper()
    integration = behavior._load_integration_entrypoint()
    await integration.async_setup(ha, {})
    entry = behavior.MockConfigEntry()
    await integration.async_setup_entry(ha, entry)
    own_entry(ha, "ai_task.renamed_wrapper", entry.entry_id)
    entry.options[const.CONF_AI_ENTITY] = "ai_task.renamed_wrapper"
    entity = wrapper_module.ActivityImagesAITaskEntity(entry)
    entity.hass = ha
    entity._attr_entity_id = "ai_task.activity_images"
    assert not entity.available
    task = ai.GenImageTask(name="home_tasks_self",
                           instructions="Self\n\nThe image must have a square 1:1 aspect ratio.")
    with pytest.raises(wrapper_module.HomeAssistantError, match="itself"):
        await entity._async_generate_image(task, SimpleNamespace(conversation_id="test"))
    assert not ha.services.calls
    # Pre-registration backstop: same ID without a registry entry is still blocked.
    entry.options[const.CONF_AI_ENTITY] = "ai_task.activity_images"
    assert not entity.available
    with pytest.raises(wrapper_module.HomeAssistantError, match="itself"):
        await entity._async_generate_image(task, SimpleNamespace(conversation_id="test"))
    assert not ha.services.calls
    await integration.async_unload_entry(ha, entry)


def test_native_selector_excludes_own_current_ids():
    import probatio as vol

    if "homeassistant.config_entries" not in sys.modules:
        pkg = types.ModuleType("homeassistant.config_entries")
        pkg.__path__ = []
        sys.modules["homeassistant.config_entries"] = pkg
    sys.modules["homeassistant.config_entries"].__dict__["ConfigFlow"] = type(
        "ConfigFlow", (), {"__init_subclass__": classmethod(lambda cls, **kwargs: None)}
    )
    sys.modules["homeassistant.config_entries"].__dict__["OptionsFlow"] = type("OptionsFlow", (), {})
    if "homeassistant.core" not in sys.modules:
        pkg = types.ModuleType("homeassistant.core")
        pkg.__path__ = []
        sys.modules["homeassistant.core"] = pkg
    sys.modules["homeassistant.core"].callback = lambda func: func
    fake_selector = types.ModuleType("homeassistant.helpers.selector")

    class FakeConfig(dict):
        def __init__(self, **kwargs):
            super().__init__(kwargs)

    class FakeSelector:
        def __init__(self, config=None):
            self.config = dict(config or {})

    for name in ("EntitySelectorConfig", "MediaSelectorConfig", "TextSelectorConfig"):
        setattr(fake_selector, name, FakeConfig)
    fake_selector.BooleanSelector = FakeSelector
    fake_selector.EntitySelector = FakeSelector
    fake_selector.MediaSelector = FakeSelector
    fake_selector.TextSelector = FakeSelector
    sys.modules["homeassistant.helpers.selector"] = fake_selector
    flow = importlib.import_module("custom_components.activity_images.config_flow")
    importlib.reload(flow)
    try:
        plain = flow.schema({const.CONF_AI_ENTITY: "ai_task.real"})
        excluded = flow.schema({const.CONF_AI_ENTITY: "ai_task.real"},
                               exclude_providers=["ai_task.own", "ai_task.second"])
        def provider_config(schema):
            for value in schema.schema.values():
                if isinstance(value, FakeSelector) and value.config.get("domain") == "ai_task":
                    return value.config
            raise AssertionError("provider selector missing")
        assert "exclude_entities" not in provider_config(plain)
        assert provider_config(excluded)["exclude_entities"] == ["ai_task.own", "ai_task.second"]
        assert isinstance(plain, vol.Schema)
    finally:
        # Keep the fake selector module; production imports use the real package.
        pass


@pytest.mark.asyncio
async def test_fuzzy_tie_prefers_sorted_winner_with_real_files(ha):
    from custom_components.activity_images.image_cache import normalize_title

    folder = ha.base / "www" / "activity_images"
    folder.mkdir(parents=True, exist_ok=True)
    Image.new("RGBA", (8, 8), (10, 20, 30, 255)).save(folder / "best.png")
    Image.new("RGBA", (8, 8), (200, 40, 60, 255)).save(folder / "rest.png")
    cache = behavior.cache_module.ActivityImageCache(ha)
    await cache.async_setup()
    await cache.async_remember("activity best", "/local/activity_images/best.png")
    await cache.async_remember("activity rest", "/local/activity_images/rest.png")
    query = normalize_title("activity test")
    first = behavior.cache_module.SequenceMatcher(None, query, normalize_title("activity best")).ratio()
    second = behavior.cache_module.SequenceMatcher(None, query, normalize_title("activity rest")).ratio()
    assert first == second >= behavior.cache_module.DEFAULT_SIMILARITY_THRESHOLD
    assert await cache.async_find("activity best") == "/local/activity_images/best.png"
    assert await cache.async_find("activity rest") == "/local/activity_images/rest.png"
    winner = "/local/activity_images/rest.png"
    assert await cache.async_find("activity test") == winner
    assert await cache.async_find("activity test") == winner
    reordered = behavior.cache_module.ActivityImageCache(ha)
    reordered._entries = {}
    reordered._token_index = {}
    reordered._metadata = dict(cache._metadata)
    reordered._deletions = dict(cache._deletions)
    reordered._tombstones = set(cache._tombstones)
    reordered._loaded = True
    await reordered.async_remember("activity rest", "/local/activity_images/rest.png")
    await reordered.async_remember("activity best", "/local/activity_images/best.png")
    assert await reordered.async_find("activity test") == winner

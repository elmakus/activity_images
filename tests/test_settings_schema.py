"""Pure validation and render tests; no Home Assistant runtime needed."""
import importlib
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1] / "custom_components" / "activity_images"
for name, root in (("custom_components", ROOT.parent), ("custom_components.activity_images", ROOT)):
    if name not in sys.modules:
        pkg = types.ModuleType(name)
        pkg.__path__ = [str(root)]
        sys.modules[name] = pkg

schema = importlib.import_module("custom_components.activity_images.settings_schema")
prompts = importlib.import_module("custom_components.activity_images.prompts")
const = importlib.import_module("custom_components.activity_images.const")


def test_defaults_keep_original_prompt_and_data_precedence():
    entry = types.SimpleNamespace(data={const.CONF_AI_ENTITY: "ai_task.old"}, options={const.CONF_AI_ENTITY: "ai_task.new"})
    assert schema.effective_settings(entry)[const.CONF_AI_ENTITY] == "ai_task.new"
    original = prompts.build_activity_image_instructions("Bake")
    assert original == prompts.DEFAULT_PROMPT_TEMPLATE.replace("{title}", "Bake")
    assert schema.validate_settings({})[const.CONF_PROMPT_TEMPLATE] == prompts.DEFAULT_PROMPT_TEMPLATE


@pytest.mark.parametrize("changes", [
    {"unexpected": 1}, {const.CONF_AI_ENTITY: "sensor.fake"},
    {const.CONF_CALENDARS: ["calendar.one", "calendar.one"]},
    {const.CONF_CALENDARS: "calendar.one"},
    {const.CONF_CALENDARS: ["calendar.One"]},
    {const.CONF_LOOKAHEAD_DAYS: True},
    {const.CONF_LOOKAHEAD_DAYS: 0},
    {const.CONF_AUTO_GENERATE: "true"},
    {const.CONF_AUTO_GENERATE: True},
    {const.CONF_LUCIDA_URL: "file:///etc/passwd"},
    {const.CONF_LUCIDA_URL: "http://username:password@host/"},
    {const.CONF_REFERENCE_MEDIA_ID: "/local/private.png"},
    {const.CONF_PROMPT_TEMPLATE: "No placeholder"},
    {const.CONF_PROMPT_TEMPLATE: "Repeated {title} {title}"},
    {const.CONF_PROMPT_TEMPLATE: "{title.__class__}"},
    {const.CONF_PROMPT_TEMPLATE: "{title!r}"},
    {const.CONF_PROMPT_TEMPLATE: "broken {"},
])
def test_reject_bad_settings(changes):
    with pytest.raises(ValueError):
        schema.validate_settings(changes)


def test_partial_update_and_safe_prompt_rendering():
    current = schema.validate_settings({const.CONF_AI_ENTITY: "ai_task.google",
                                        const.CONF_CALENDARS: ["calendar.family"]})
    result = schema.validate_settings({const.CONF_LOOKAHEAD_DAYS: 12}, current)
    assert result[const.CONF_AI_ENTITY] == "ai_task.google"
    assert result[const.CONF_CALENDARS] == ["calendar.family"]
    assert result[const.CONF_LOOKAHEAD_DAYS] == 12
    rendered = prompts.build_activity_image_instructions(
        "Party {strange}", "Draw {{once}}: {title}"
    )
    assert rendered == "Draw {once}: Party {strange}"


def test_title_content_is_not_reinterpreted_as_template():
    assert prompts.build_activity_image_instructions(
        "Play {{braced}}", "Make {title} with {{literal}}"
    ) == "Make Play {{braced}} with {literal}"

"""No Home Assistant installation required for these pure-engine regression tests."""
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import sys

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1] / "custom_components" / "activity_images"


def load(name):
    spec = spec_from_file_location(name, ROOT / (name.removeprefix("ai_") + ".py"))
    module = module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def test_every_activity_uses_original_full_prompt_not_room_variant():
    prompts = load("ai_prompts")
    chore = prompts.build_activity_image_instructions("Odkurzanie")
    room = prompts.build_activity_image_instructions("Pokój dziecięcy")
    assert "tactile" in chore.lower()
    assert "isometric" not in chore.lower()
    assert "isometric" not in room.lower()
    assert "Pokój dziecięcy" in room
    assert chore.endswith(prompts.PROMPT_STYLE_SUFFIX)
    assert room.endswith(prompts.PROMPT_STYLE_SUFFIX)


def test_transparent_canvas_normalization():
    processing = load("ai_image_processing")
    original = Image.new("RGBA", (128, 128), (0, 0, 0, 0))
    ImageDraw.Draw(original).ellipse((32, 32, 96, 96), fill=(50, 100, 180, 255))
    normalized = processing.normalize_icon_canvas(original)
    assert normalized.mode == "RGBA"
    assert normalized.height == 512
    assert normalized.getchannel("A").getextrema()[0] == 0

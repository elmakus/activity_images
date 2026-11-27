"""Lightweight processing for generated activity images."""

from __future__ import annotations


from PIL import Image, ImageDraw


ICON_CANVAS_SIZE = 512
# Normalized display masters are tightly cropped and always retain a 512 px
# visible height. Width follows the artwork aspect ratio.
ICON_VISIBLE_SIZE = ICON_CANVAS_SIZE
VISIBLE_ALPHA_THRESHOLD = 8
MAX_IMAGE_PIXELS = 25_000_000
MAX_ICON_WIDTH = 4096


def validate_dimensions(image: Image.Image) -> None:
    if image.width <= 0 or image.height <= 0 or image.width * image.height > MAX_IMAGE_PIXELS:
        raise ValueError("Invalid or excessive image dimensions")


def has_meaningful_transparency(image: Image.Image) -> bool:
    """Require at least 1% almost-transparent pixels, not one faint alpha value."""
    validate_dimensions(image)
    if "A" not in image.getbands() and "transparency" not in image.info:
        return False
    alpha = image.getchannel("A") if "A" in image.getbands() else image.convert("RGBA").getchannel("A")
    return sum(alpha.histogram()[:VISIBLE_ALPHA_THRESHOLD + 1]) >= image.width * image.height * 0.01


def _validate_icon_width(width: int) -> None:
    if width > MAX_ICON_WIDTH:
        raise ValueError("Normalized image dimensions exceed the width limit")


def _visible_bbox(image: Image.Image) -> tuple[int, int, int, int] | None:
    """Return the bounding box of pixels visible above the alpha threshold."""
    alpha = image.getchannel("A")
    visible = alpha.point(
        lambda value: 255 if value > VISIBLE_ALPHA_THRESHOLD else 0
    )
    return visible.getbbox()


def normalize_icon_canvas(image: Image.Image) -> Image.Image:
    """Return a tightly cropped icon with a 512 px visible height.

    The output has no intentional transparent margin. Its height is fixed while
    its width follows the artwork aspect ratio, so consumers can render every
    icon at one optical height without distorting wider or narrower artwork.
    """
    validate_dimensions(image)
    rgba = image.convert("RGBA")
    bbox = _visible_bbox(rgba)
    if bbox is None:
        raise ValueError("Generated image is completely transparent")

    visible_width = bbox[2] - bbox[0]
    visible_height = bbox[3] - bbox[1]
    _validate_icon_width(max(1, round(visible_width * ICON_CANVAS_SIZE / visible_height)))
    if (
        rgba.size == (visible_width, ICON_CANVAS_SIZE)
        and visible_height == ICON_CANVAS_SIZE
        and bbox == (0, 0, rgba.width, rgba.height)
    ):
        return rgba.copy()

    cropped = rgba.crop(bbox)
    if cropped.height != ICON_CANVAS_SIZE:
        scale = ICON_CANVAS_SIZE / cropped.height
        target_size = (
            max(1, round(cropped.width * scale)),
            ICON_CANVAS_SIZE,
        )
        _validate_icon_width(target_size[0])
        cropped = cropped.resize(target_size, Image.Resampling.LANCZOS)

    # Lanczos may create a barely visible halo. Apply the same meaningful-alpha
    # threshold once more so the stored display master still has zero margins.
    resized_bbox = _visible_bbox(cropped)
    if resized_bbox is None:
        raise ValueError("Generated image is completely transparent")
    cropped = cropped.crop(resized_bbox)
    if cropped.height != ICON_CANVAS_SIZE:
        target_width = max(1, round(cropped.width * ICON_CANVAS_SIZE / cropped.height))
        _validate_icon_width(target_width)
        cropped = cropped.resize(
            (target_width, ICON_CANVAS_SIZE), Image.Resampling.LANCZOS
        )
        final_bbox = _visible_bbox(cropped)
        if final_bbox is not None:
            cropped = cropped.crop(final_bbox)
    return cropped


def create_theme_qa_preview(image: Image.Image) -> Image.Image:
    """Show a generated icon on dark and light backgrounds side by side."""
    icon = image.convert("RGBA")
    icon.thumbnail((232, 232), Image.Resampling.LANCZOS)
    preview = Image.new("RGB", (512, 256), "#f5f5f5")
    ImageDraw.Draw(preview).rectangle((0, 0, 255, 255), fill="#121212")
    y = (256 - icon.height) // 2
    left = ((256 - icon.width) // 2, y)
    right = (256 + (256 - icon.width) // 2, y)
    preview.paste(icon, left, icon)
    preview.paste(icon, right, icon)
    return preview

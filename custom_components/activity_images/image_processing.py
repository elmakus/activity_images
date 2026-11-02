"""Lightweight processing for generated activity images."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageOps


BACKGROUND_DISTANCE = 30
MAX_BACKGROUND_COLORS = 6
MIN_BORDER_COVERAGE = 0.72
ICON_CANVAS_SIZE = 512
# Normalized display masters are tightly cropped and always retain a 512 px
# visible height. Width follows the artwork aspect ratio.
ICON_VISIBLE_SIZE = ICON_CANVAS_SIZE
VISIBLE_ALPHA_THRESHOLD = 8


@dataclass(slots=True)
class ProcessedImage:
    """A normalized RGBA image and the background-processing result."""

    image: Image.Image
    background_status: str


def _border_palette(image: Image.Image) -> tuple[list[tuple[int, int, int]], float]:
    """Return dominant quantized border colours and their total coverage."""
    rgb = image.convert("RGB")
    width, height = rgb.size
    border = [
        *(rgb.getpixel((x, 0)) for x in range(width)),
        *(rgb.getpixel((x, height - 1)) for x in range(width)),
        *(rgb.getpixel((0, y)) for y in range(1, height - 1)),
        *(rgb.getpixel((width - 1, y)) for y in range(1, height - 1)),
    ]
    quantized = [tuple((channel // 16) * 16 + 8 for channel in pixel) for pixel in border]
    dominant = Counter(quantized).most_common(MAX_BACKGROUND_COLORS)
    coverage = sum(count for _colour, count in dominant) / max(1, len(border))
    return [colour for colour, _count in dominant], coverage


def _similar_to_palette_mask(
    image: Image.Image, palette: list[tuple[int, int, int]]
) -> Image.Image:
    """Return a white mask where pixels are close to a border colour."""
    rgb = image.convert("RGB")
    aggregate = Image.new("L", rgb.size, 0)
    for colour in palette:
        difference = ImageChops.difference(rgb, Image.new("RGB", rgb.size, colour))
        red, green, blue = difference.split()
        maximum = ImageChops.lighter(ImageChops.lighter(red, green), blue)
        close = maximum.point(lambda value: 255 if value <= BACKGROUND_DISTANCE else 0)
        aggregate = ImageChops.lighter(aggregate, close)
    return aggregate


def _edge_connected_background(candidate_mask: Image.Image) -> Image.Image:
    """Keep only candidate background pixels connected to an outer edge."""
    blocked = ImageOps.invert(candidate_mask)
    expanded = ImageOps.expand(blocked, border=1, fill=0)
    ImageDraw.floodfill(expanded, (0, 0), 128, thresh=0)
    connected = expanded.crop((1, 1, expanded.width - 1, expanded.height - 1))
    return connected.point(lambda value: 255 if value == 128 else 0)


def normalize_generated_image(image: Image.Image) -> ProcessedImage:
    """Normalize an image and remove simple edge-connected backgrounds."""
    rgba = image.convert("RGBA")
    if rgba.getchannel("A").getextrema()[0] < 250:
        return ProcessedImage(rgba, "existing_alpha")

    palette, coverage = _border_palette(rgba)
    if coverage < MIN_BORDER_COVERAGE:
        return ProcessedImage(rgba, "review_needed_complex_background")

    candidate_mask = _similar_to_palette_mask(rgba, palette)
    background_mask = _edge_connected_background(candidate_mask)
    histogram = background_mask.histogram()
    removed_fraction = histogram[255] / max(1, rgba.width * rgba.height)
    if removed_fraction < 0.01 or removed_fraction > 0.92:
        return ProcessedImage(rgba, "review_needed_unsafe_mask")

    alpha = ImageOps.invert(background_mask).filter(ImageFilter.GaussianBlur(0.7))
    rgba.putalpha(alpha)
    return ProcessedImage(rgba, "background_removed")


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
    rgba = image.convert("RGBA")
    bbox = _visible_bbox(rgba)
    if bbox is None:
        raise ValueError("Generated image is completely transparent")

    visible_width = bbox[2] - bbox[0]
    visible_height = bbox[3] - bbox[1]
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
        cropped = cropped.resize(target_size, Image.Resampling.LANCZOS)

    # Lanczos may create a barely visible halo. Apply the same meaningful-alpha
    # threshold once more so the stored display master still has zero margins.
    resized_bbox = _visible_bbox(cropped)
    if resized_bbox is None:
        raise ValueError("Generated image is completely transparent")
    cropped = cropped.crop(resized_bbox)
    if cropped.height != ICON_CANVAS_SIZE:
        target_width = max(1, round(cropped.width * ICON_CANVAS_SIZE / cropped.height))
        cropped = cropped.resize(
            (target_width, ICON_CANVAS_SIZE), Image.Resampling.LANCZOS
        )
        final_bbox = _visible_bbox(cropped)
        if final_bbox is not None:
            cropped = cropped.crop(final_bbox)
    return cropped


def create_theme_qa_preview(image: Image.Image) -> Image.Image:
    """Show a generated icon on dark and light backgrounds side by side."""
    icon = image.convert("RGBA").copy()
    icon.thumbnail((232, 232), Image.Resampling.LANCZOS)
    preview = Image.new("RGB", (512, 256), "#f5f5f5")
    ImageDraw.Draw(preview).rectangle((0, 0, 255, 255), fill="#121212")
    y = (256 - icon.height) // 2
    left = ((256 - icon.width) // 2, y)
    right = (256 + (256 - icon.width) // 2, y)
    preview.paste(icon, left, icon)
    preview.paste(icon, right, icon)
    return preview

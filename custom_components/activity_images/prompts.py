"""Configurable tactile 3D activity icon prompt, preserving the original default."""
from __future__ import annotations

from string import Formatter

DEFAULT_PROMPT_PREFIX = (
    "Create one compact, colorful, tactile 3D activity icon with one immediately "
    "recognizable dominant subject for:"
)
PROMPT_STYLE_SUFFIX = """
Match the attached board of approved icons: glossy, friendly, richly colored,
softly lit, rounded and tactile.

Design first for immediate recognition at 25 px. Identify the single object or
action that uniquely distinguishes the activity and make it the dominant,
unobstructed visual element, occupying roughly 50-65% of the visible icon.
Use at most one supporting person or one secondary cue. Supporting elements
must never compete with, cover or shrink the dominant subject. Omit furniture,
platforms, scenery and decorative micro-details unless they are essential to
recognition. Do not attempt to illustrate every part of the title.

Keep the simplified subject geometrically centered and make its longest visible
dimension fill about 90% of the square. Use a strong silhouette and clear color
separation. Preserve the approved tactile 3D illustration style; do not turn
the result into a flat generic pictogram. Before finalizing, evaluate the icon
as if reduced to 25 px: if the defining object would not be immediately
recognizable, enlarge and simplify it.

Do not create a photograph, room, wall, floor, frame, tile, letters, numbers,
words, caption, logo or UI. Do not draw or simulate transparency or a
checkerboard. Render the subject on one perfectly uniform chroma-key
background, with no texture, gradient or cast shadow on the background. Keep a
clear margin around the subject. Return a square 1:1 PNG.
""".strip()

DEFAULT_PROMPT_TEMPLATE = f"{DEFAULT_PROMPT_PREFIX} {{title}}\n\n{PROMPT_STYLE_SUFFIX}"
MAX_PROMPT_LENGTH = 16000


def validate_prompt_template(template: str) -> str:
    """Allow exactly one named {title} placeholder; reject format expressions."""
    if not isinstance(template, str) or not 1 <= len(template) <= MAX_PROMPT_LENGTH:
        raise ValueError("Prompt template must contain 1–16000 characters")
    try:
        fields = [(name, spec, conversion) for _, name, spec, conversion
                  in Formatter().parse(template) if name is not None]
    except ValueError as exc:
        raise ValueError("Invalid prompt braces") from exc
    if fields != [("title", "", None)]:
        raise ValueError("Prompt must contain exactly one {title} placeholder")
    return template


def build_activity_image_instructions(title: str, template: str | None = None) -> str:
    """Render one safe user-supplied title without interpreting its braces."""
    selected = validate_prompt_template(template if template is not None else DEFAULT_PROMPT_TEMPLATE)
    return selected.format(title=title)

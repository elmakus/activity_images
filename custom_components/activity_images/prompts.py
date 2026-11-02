"""Original Activity Images 3D-icon and room-vignette prompts.

Adapted from the user's Home Tasks customization. 
"""
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

ROOM_PROMPT_STYLE_SUFFIX = """
Match the attached board of approved icons: glossy, friendly, richly colored,
softly lit, rounded and tactile.

This title describes an interior space. Create a compact open-front isometric
room vignette, not an isolated object. Show a small floor plane and at most two
short corner walls inside the vignette so the spatial relationship is obvious.
Include every object explicitly named in the title and keep each one fully
visible and unobstructed. Do not replace the named furniture with a symbolic
object or a person. For a bedroom, the complete bed must remain the dominant
element. For a home cinema, both the sofa and projector screen must be clearly
visible facing each other. For a corridor, show visible hallway depth plus the
named entrance cues.

Design first for immediate recognition at 25 px. Make the main furniture or
architectural feature occupy roughly 45-60% of the visible icon and give every
named secondary cue enough size and color contrast to remain legible. Omit all
other furniture and decorative micro-details. Keep the complete room vignette
geometrically centered and make its longest visible dimension fill about 90%
of the square. Preserve the approved tactile 3D illustration style; do not turn
the result into a flat generic pictogram.

Do not create a photograph, full-size architectural rendering, frame, tile,
letters, numbers, words, caption, logo or UI. Do not draw or simulate
transparency or a checkerboard. Render the compact room vignette on one
perfectly uniform chroma-key background, with no texture, gradient or cast
shadow on the background. Keep a clear margin around the vignette. Return a
square 1:1 PNG.
""".strip()

ROOM_TITLE_MARKERS = (
    "pokój", "sypialnia", "salon", "korytarz", "łazienka", "jadalnia", "kuchnia",
    "room", "bedroom", "living room", "home cinema", "hallway", "corridor",
    "zimmer", "schlafzimmer", "wohnzimmer", "heimkino", "flur",
)


def build_activity_image_instructions(title: str, prompt_prefix: str = "") -> str:
    """Build the shared queue/manual prompt, with room-aware composition."""
    prefix = (prompt_prefix or DEFAULT_PROMPT_PREFIX).strip()
    normalized = title.casefold()
    suffix = (
        ROOM_PROMPT_STYLE_SUFFIX
        if any(marker in normalized for marker in ROOM_TITLE_MARKERS)
        else PROMPT_STYLE_SUFFIX
    )
    return f"{prefix} {title}\n\n{suffix}"

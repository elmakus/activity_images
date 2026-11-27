"""Privacy-safe native Home Assistant diagnostics (no provider calls)."""
from __future__ import annotations

from collections import Counter
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from . import ActivityImagesConfigEntry, ActivityImagesRuntime

from .settings_diagnostics import diagnostics


async def async_get_config_entry_diagnostics(hass: HomeAssistant, entry: ActivityImagesConfigEntry) -> dict[str, object]:
    """Expose only status and counts; never export titles, URLs or prompts."""
    status = diagnostics(hass, entry)
    provider: dict[str, object] = dict(status["provider"])
    provider.pop("entity_id", None)
    calendar = status["calendar"]
    result: dict[str, object] = {
        "provider": provider,
        "reference": status["reference"],
        "calendar": {
            "service_registered": calendar["service_registered"],
            "statuses": dict(Counter(item["status"] for item in calendar["entities"])),
        },
        "runtime_loaded": False,
    }
    runtime: ActivityImagesRuntime | None = getattr(entry, "runtime_data", None)
    if runtime is not None:
        result.update(
            runtime_loaded=True,
            mappings=len(runtime.cache._entries),
            queue_states=dict(Counter(job["state"] for job in runtime.queue._jobs)),
            deletion_states=dict(Counter(op["state"] for op in runtime.cache._deletions.values())),
        )
    return result

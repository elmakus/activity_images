"""Privacy-safe native Home Assistant diagnostics (no provider calls)."""
from __future__ import annotations

from collections import Counter

from .settings_diagnostics import diagnostics


async def async_get_config_entry_diagnostics(hass, entry) -> dict:
    """Expose only status and counts; never export titles, URLs or prompts."""
    status = diagnostics(hass, entry)
    provider = dict(status["provider"])
    provider.pop("entity_id", None)
    calendar = status["calendar"]
    result = {
        "provider": provider,
        "lucida": status["lucida"],
        "reference": status["reference"],
        "calendar": {
            "service_registered": calendar["service_registered"],
            "statuses": dict(Counter(item["status"] for item in calendar["entities"])),
        },
        "runtime_loaded": False,
    }
    runtime = getattr(entry, "runtime_data", None)
    if runtime is not None:
        result.update(
            runtime_loaded=True,
            mappings=len(runtime.cache._entries),
            queue_states=dict(Counter(job["state"] for job in runtime.queue._jobs)),
            deletion_states=dict(Counter(op["state"] for op in runtime.cache._deletions.values())),
        )
    return result

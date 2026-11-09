"""Read-only diagnostics for Settings; never calls a paid AI service.

Lucida diagnostics are intentionally passive: network probing a configured URL
could hit internal services and is not required for saving settings.
"""
from __future__ import annotations

from .const import (
    CONF_AI_ENTITY, CONF_CALENDARS, CONF_LUCIDA_URL, CONF_REFERENCE_MEDIA_ID,
)
from .settings_schema import effective_settings


def diagnostics(hass, entry) -> dict:
    values = effective_settings(entry)
    provider = values[CONF_AI_ENTITY]
    state = hass.states.get(provider) if provider else None
    provider_active = bool(state and state.state not in ("unknown", "unavailable"))
    provider_service = hass.services.has_service("ai_task", "generate_image")
    calendars = []
    for entity_id in values[CONF_CALENDARS]:
        item = hass.states.get(entity_id)
        calendars.append({
            "entity_id": entity_id,
            "status": "available" if item and item.state not in ("unknown", "unavailable")
                      else "unavailable",
        })
    return {
        "provider": {
            "entity_id": provider,
            "status": "not_configured" if not provider else (
                "available" if provider_active and provider_service else "unavailable"
            ),
            "image_service_registered": provider_service,
        },
        "lucida": {
            "configured": bool(values[CONF_LUCIDA_URL]),
            "status": "configured_not_probed" if values[CONF_LUCIDA_URL] else "not_configured",
        },
        "reference": {
            "configured": bool(values[CONF_REFERENCE_MEDIA_ID]),
        },
        "calendar": {
            "service_registered": hass.services.has_service("calendar", "get_events"),
            "entities": calendars,
        },
    }

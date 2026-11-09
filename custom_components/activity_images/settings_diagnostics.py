"""Read-only diagnostics for Settings; never calls a paid AI service.

Lucida diagnostics are intentionally passive: network probing a configured URL
could hit internal services and is not required for saving settings.
"""
from __future__ import annotations

from homeassistant.components.ai_task.const import AITaskEntityFeature

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
    features = state.attributes.get("supported_features", 0) if state else 0
    features = features if isinstance(features, int) else 0
    image_supported = bool(features & AITaskEntityFeature.GENERATE_IMAGE)
    attachments_supported = bool(features & AITaskEntityFeature.SUPPORT_ATTACHMENTS)
    if not provider:
        provider_status = "not_configured"
    elif not provider_active or not provider_service:
        provider_status = "unavailable"
    elif not image_supported:
        provider_status = "image_generation_unsupported"
    elif values[CONF_REFERENCE_MEDIA_ID] and not attachments_supported:
        provider_status = "attachments_unsupported"
    else:
        provider_status = "available"
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
            "status": provider_status,
            "image_service_registered": provider_service,
            "image_generation_supported": image_supported,
            "attachments_supported": attachments_supported,
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

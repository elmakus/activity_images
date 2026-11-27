"""Read-only diagnostics for Settings; never calls a paid AI service."""
from __future__ import annotations

from typing import TYPE_CHECKING, TypedDict

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from . import ActivityImagesConfigEntry

from homeassistant.components.ai_task.const import AITaskEntityFeature

from .const import (
    CONF_AI_ENTITY, CONF_CALENDARS, CONF_REFERENCE_MEDIA_ID,
)
from .settings_schema import Settings, effective_settings


class ProviderDiagnostics(TypedDict):
    entity_id: str
    status: str
    image_service_registered: bool
    image_generation_supported: bool
    attachments_supported: bool


def provider_diagnostics(hass: HomeAssistant, entry: ActivityImagesConfigEntry | None, values: Settings) -> ProviderDiagnostics:
    from .settings import is_own_provider_entity

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
    elif is_own_provider_entity(hass, entry, provider):
        provider_status = "self_reference"
    elif not provider_active or not provider_service:
        provider_status = "unavailable"
    elif not image_supported:
        provider_status = "image_generation_unsupported"
    elif values[CONF_REFERENCE_MEDIA_ID] and not attachments_supported:
        provider_status = "attachments_unsupported"
    else:
        provider_status = "available"
    return {
        "entity_id": provider,
        "status": provider_status,
        "image_service_registered": provider_service,
        "image_generation_supported": image_supported,
        "attachments_supported": attachments_supported,
    }


class CalendarDiagnostic(TypedDict):
    entity_id: str
    status: str


class CalendarDiagnostics(TypedDict):
    service_registered: bool
    entities: list[CalendarDiagnostic]


class ReferenceDiagnostics(TypedDict):
    configured: bool


class SettingsDiagnostics(TypedDict):
    provider: ProviderDiagnostics
    reference: ReferenceDiagnostics
    calendar: CalendarDiagnostics


def diagnostics(hass: HomeAssistant, entry: ActivityImagesConfigEntry) -> SettingsDiagnostics:
    values = effective_settings(entry)
    calendars: list[CalendarDiagnostic] = []
    for entity_id in values[CONF_CALENDARS]:
        item = hass.states.get(entity_id)
        calendars.append({
            "entity_id": entity_id,
            "status": "available" if item and item.state not in ("unknown", "unavailable")
                      else "unavailable",
        })
    return {
        "provider": provider_diagnostics(hass, entry, values),
        "reference": {
            "configured": bool(values[CONF_REFERENCE_MEDIA_ID]),
        },
        "calendar": {
            "service_registered": hass.services.has_service("calendar", "get_events"),
            "entities": calendars,
        },
    }

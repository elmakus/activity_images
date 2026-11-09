"""UI setup and options for the standalone Activity Images generator."""
from __future__ import annotations

import probatio as vol
from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import selector

from .const import (
    CONF_AI_ENTITY,
    CONF_AUTO_GENERATE,
    CONF_CALENDARS,
    CONF_LOOKAHEAD_DAYS,
    CONF_LUCIDA_URL,
    CONF_REFERENCE_MEDIA_ID,
    CONF_PROMPT_TEMPLATE,
    DEFAULT_LOOKAHEAD_DAYS,
    DOMAIN,
)
from .prompts import DEFAULT_PROMPT_TEMPLATE
from .settings import StaleSettingsError, options_for_update, revision, settings_lock
from .settings_schema import effective_settings, validate_settings


def schema(settings: dict | None = None) -> vol.Schema:
    settings = settings or {}
    # A provider is optional for library-only use and calendar previews.
    # An empty EntitySelector is invalid on current Home Assistant releases.
    provider = settings.get(CONF_AI_ENTITY)
    provider_key = (
        vol.Optional(CONF_AI_ENTITY, description={"suggested_value": provider})
        if provider else vol.Optional(CONF_AI_ENTITY)
    )
    reference = settings.get(CONF_REFERENCE_MEDIA_ID)
    if isinstance(reference, str) and reference:
        reference = {"media_content_id":reference, "media_content_type":"image"}
    reference_key = (
        vol.Optional(CONF_REFERENCE_MEDIA_ID, description={"suggested_value":reference})
        if reference else vol.Optional(CONF_REFERENCE_MEDIA_ID)
    )
    return vol.Schema({
        provider_key: selector.EntitySelector(selector.EntitySelectorConfig(domain="ai_task")),
        reference_key: selector.MediaSelector(selector.MediaSelectorConfig(accept=["image/*"])),
        vol.Optional(
            CONF_LUCIDA_URL, default=settings.get(CONF_LUCIDA_URL, "")
        ): selector.TextSelector(),
        vol.Optional(
            CONF_PROMPT_TEMPLATE, default=settings.get(CONF_PROMPT_TEMPLATE, DEFAULT_PROMPT_TEMPLATE),
        ): selector.TextSelector(selector.TextSelectorConfig(multiline=True)),
        vol.Optional(
            CONF_CALENDARS, default=settings.get(CONF_CALENDARS, [])
        ): selector.EntitySelector(selector.EntitySelectorConfig(domain="calendar", multiple=True)),
        vol.Required(
            CONF_LOOKAHEAD_DAYS,
            default=settings.get(CONF_LOOKAHEAD_DAYS, DEFAULT_LOOKAHEAD_DAYS),
        ): vol.All(vol.Coerce(int), vol.Range(min=1, max=60)),
        vol.Required(
            CONF_AUTO_GENERATE,
            default=settings.get(CONF_AUTO_GENERATE, False),
        ): selector.BooleanSelector(),
    })


def _form_settings(data: dict) -> dict:
    """Adapt native media-selector objects to the unchanged persisted string ID."""
    values = dict(data)
    reference = values.get(CONF_REFERENCE_MEDIA_ID)
    if isinstance(reference, dict):
        values[CONF_REFERENCE_MEDIA_ID] = reference.get("media_content_id", "")
    return validate_settings(values)


class ActivityImagesConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input=None):
        errors = {}
        if user_input is not None:
            try:
                values = _form_settings(user_input)
            except ValueError:
                errors["base"] = "invalid_settings"
            else:
                await self.async_set_unique_id(DOMAIN)
                self._abort_if_unique_id_configured()
                return self.async_create_entry(title="Activity Images", data=values)
        return self.async_show_form(step_id="user", data_schema=schema(user_input), errors=errors)

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return ActivityImagesOptionsFlow()


class ActivityImagesOptionsFlow(config_entries.OptionsFlow):
    def __init__(self):
        self._revision = None

    async def async_step_init(self, user_input=None):
        entry = self.config_entry
        if self._revision is None:
            self._revision = revision(entry)
        errors = {}
        if user_input is not None:
            try:
                async with settings_lock(entry):
                    # The full native form can omit a cleared optional provider.
                    values = _form_settings(user_input)
                    options = options_for_update(entry, values, self._revision)
                    # HA's flow manager persists this result without another await.
                    return self.async_create_entry(title="", data=options)
            except StaleSettingsError:
                errors["base"] = "settings_conflict"
            except (ValueError, HomeAssistantError):
                errors["base"] = "invalid_settings"
        return self.async_show_form(
            step_id="init", data_schema=schema(user_input if user_input is not None else effective_settings(entry)),
            errors=errors,
        )

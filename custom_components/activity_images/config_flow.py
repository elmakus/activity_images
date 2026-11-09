"""UI setup and options for the standalone Activity Images generator."""
from __future__ import annotations

import probatio as vol
from homeassistant import config_entries
from homeassistant.core import callback
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
from .settings_schema import validate_settings


def schema(settings: dict | None = None) -> vol.Schema:
    settings = settings or {}
    # A provider is optional for library-only use and calendar previews.
    # An empty EntitySelector is invalid on current Home Assistant releases.
    provider = settings.get(CONF_AI_ENTITY)
    provider_key = (
        vol.Optional(CONF_AI_ENTITY, default=provider)
        if provider else vol.Optional(CONF_AI_ENTITY)
    )
    return vol.Schema({
        provider_key: selector.EntitySelector(selector.EntitySelectorConfig(domain="ai_task")),
        vol.Optional(
            CONF_REFERENCE_MEDIA_ID,
            default=settings.get(CONF_REFERENCE_MEDIA_ID, ""),
        ): selector.TextSelector(),
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


def _valid(data: dict) -> bool:
    try:
        validate_settings(data)
    except ValueError:
        return False
    return True


class ActivityImagesConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input=None):
        errors = {}
        if user_input is not None:
            if not _valid(user_input):
                errors["base"] = "invalid_settings"
            else:
                await self.async_set_unique_id(DOMAIN)
                self._abort_if_unique_id_configured()
                return self.async_create_entry(title="Activity Images", data=user_input)
        return self.async_show_form(step_id="user", data_schema=schema(), errors=errors)

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return ActivityImagesOptionsFlow(config_entry)


class ActivityImagesOptionsFlow(config_entries.OptionsFlow):
    def __init__(self, config_entry):
        self._source_entry = config_entry

    async def async_step_init(self, user_input=None):
        errors = {}
        if user_input is not None:
            if not _valid(user_input):
                errors["base"] = "invalid_settings"
            else:
                return self.async_create_entry(title="", data=user_input)
        current = dict(self._source_entry.data)
        current.update(self._source_entry.options)
        return self.async_show_form(
            step_id="init", data_schema=schema(current), errors=errors
        )

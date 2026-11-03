"""UI setup and options for the standalone Activity Images generator."""
from __future__ import annotations

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers import selector

from .const import (
    DOMAIN, CONF_AI_ENTITY, CONF_REFERENCE_MEDIA_ID, CONF_LUCIDA_URL,
)


def schema(settings: dict | None = None) -> vol.Schema:
    settings = settings or {}
    return vol.Schema({
        vol.Required(
            CONF_AI_ENTITY, default=settings.get(CONF_AI_ENTITY, "")
        ): selector.EntitySelector(selector.EntitySelectorConfig(domain="ai_task")),
        vol.Optional(
            CONF_REFERENCE_MEDIA_ID,
            default=settings.get(CONF_REFERENCE_MEDIA_ID, ""),
        ): selector.TextSelector(),
        vol.Optional(
            CONF_LUCIDA_URL, default=settings.get(CONF_LUCIDA_URL, "")
        ): selector.TextSelector(),
    })


def _valid(data: dict) -> bool:
    return (
        (
            not data.get(CONF_LUCIDA_URL)
            or data[CONF_LUCIDA_URL].startswith(("http://", "https://"))
        )
        and (
            not data.get(CONF_REFERENCE_MEDIA_ID)
            or data[CONF_REFERENCE_MEDIA_ID].startswith("media-source://")
        )
    )


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

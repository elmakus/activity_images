"""UI setup and options for the standalone Activity Images generator."""
from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from . import ActivityImagesConfigEntry
    from homeassistant.config_entries import ConfigFlowResult

import probatio as vol
from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import selector

from .const import (
    CALENDAR_BINDINGS,
    CONF_AI_ENTITY,
    CONF_AUTO_CALENDARS,
    CONF_CALENDARS,
    CONF_LOOKAHEAD_DAYS,
    CONF_REFERENCE_MEDIA_ID,
    CONF_PROMPT_TEMPLATE,
    DEFAULT_LOOKAHEAD_DAYS,
    DOMAIN,
)
from .prompts import DEFAULT_PROMPT_TEMPLATE
from .settings import automatic_confirmation_ids, StaleSettingsError, calendar_identity, is_own_provider_entity, options_for_update, own_provider_entity_ids, revision, settings_lock
from .settings_schema import Settings, effective_settings, validate_settings


def schema(settings: Mapping[str, object] | None = None, *, exclude_providers: tuple[str, ...] | list[str] = ()) -> vol.Schema:
    settings = settings or {}
    # A provider is optional for library-only use and calendar previews.
    # An empty EntitySelector is invalid on current Home Assistant releases.
    provider = settings.get(CONF_AI_ENTITY)
    provider_key = (
        vol.Optional(CONF_AI_ENTITY, description={"suggested_value": provider})
        if provider else vol.Optional(CONF_AI_ENTITY)
    )
    provider_config = selector.EntitySelectorConfig(domain="ai_task")
    if exclude_providers:
        provider_config = selector.EntitySelectorConfig(
            domain="ai_task", exclude_entities=sorted(exclude_providers)
        )
    reference = settings.get(CONF_REFERENCE_MEDIA_ID)
    if isinstance(reference, str) and reference:
        reference = {"media_content_id":reference, "media_content_type":"image"}
    reference_key = (
        vol.Optional(CONF_REFERENCE_MEDIA_ID, description={"suggested_value":reference})
        if reference else vol.Optional(CONF_REFERENCE_MEDIA_ID)
    )
    return vol.Schema({
        provider_key: selector.EntitySelector(provider_config),
        reference_key: selector.MediaSelector(selector.MediaSelectorConfig(accept=["image/*"])),
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
        vol.Optional(
            CONF_AUTO_CALENDARS, default=settings.get(CONF_AUTO_CALENDARS, []),
        ): selector.EntitySelector(selector.EntitySelectorConfig(domain="calendar", multiple=True)),
    })


def _form_settings(data: Mapping[str, object]) -> Settings:
    """Adapt native media-selector objects to the unchanged persisted string ID."""
    values = dict(data)
    reference = values.get(CONF_REFERENCE_MEDIA_ID)
    if isinstance(reference, dict):
        values[CONF_REFERENCE_MEDIA_ID] = reference.get("media_content_id", "")
    return validate_settings(values)


def paid_confirmation_schema() -> vol.Schema:
    return vol.Schema({vol.Required("confirm_paid", default=False): selector.BooleanSelector()})


class _SettingsEditor(config_entries.ConfigEntryBaseFlow):
    _revision: str | None = None
    _confirmation_step: str
    _pending_values: Settings

    if TYPE_CHECKING:
        def _finish_settings(self, entry: ActivityImagesConfigEntry, options: dict[str, object]) -> ConfigFlowResult: ...

    def _provider_valid(self, entry: ActivityImagesConfigEntry, values: Settings) -> bool:
        from .settings_diagnostics import provider_diagnostics
        before = effective_settings(entry)
        return (not values[CONF_AI_ENTITY]
                or (values[CONF_AI_ENTITY] == before[CONF_AI_ENTITY] and values[CONF_REFERENCE_MEDIA_ID] == before[CONF_REFERENCE_MEDIA_ID])
                or provider_diagnostics(self.hass, entry, values)["status"] == "available")

    async def _edit_settings(self, entry: ActivityImagesConfigEntry, step_id: str, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if self._revision is None:
            self._revision = revision(entry)
        errors = {}
        if user_input is not None:
            try:
                async with settings_lock(entry):
                    # The full native form can omit a cleared optional provider.
                    values = _form_settings(user_input)
                    options = options_for_update(self.hass, entry, values, self._revision)
                    if self._confirmation_step == "confirm_reconfigure":
                        if not self._provider_valid(entry, values):
                            return self.async_show_form(step_id=step_id, data_schema=schema(user_input, exclude_providers=own_provider_entity_ids(self.hass, entry)), errors={"base": "provider_unavailable"})
                    if automatic_confirmation_ids(effective_settings(entry), values):
                        self._pending_values = values
                    else:
                        # HA's flow manager persists this result without another await.
                        return self._finish_settings(entry, options)
                return await self._confirm_settings(entry, self._confirmation_step)
            except StaleSettingsError:
                errors["base"] = "settings_conflict"
            except (ValueError, HomeAssistantError):
                errors["base"] = "invalid_settings"
        return self.async_show_form(
            step_id=step_id, data_schema=schema(user_input if user_input is not None else effective_settings(entry),
                                             exclude_providers=own_provider_entity_ids(self.hass, entry)),
            errors=errors,
        )

    async def _confirm_settings(self, entry: ActivityImagesConfigEntry, step_id: str, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        expected_revision = self._revision
        if expected_revision is None:
            return self.async_abort(reason="invalid_settings")
        if user_input and user_input.get("confirm_paid") is True:
            try:
                async with settings_lock(entry):
                    options = options_for_update(self.hass, entry, self._pending_values, expected_revision)
                    if self._confirmation_step == "confirm_reconfigure" and not self._provider_valid(entry, self._pending_values):
                        return self.async_abort(reason="provider_unavailable")
                    return self._finish_settings(entry, options)
            except StaleSettingsError:
                return self.async_abort(reason="settings_conflict")
            except (ValueError, HomeAssistantError):
                return self.async_abort(reason="invalid_settings")
        return self.async_show_form(
            step_id=step_id, data_schema=paid_confirmation_schema(),
            description_placeholders={"calendars": ", ".join(self._pending_values[CONF_AUTO_CALENDARS])},
        )


class ActivityImagesConfigFlow(_SettingsEditor, config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1
    _confirmation_step = "confirm_reconfigure"

    async def _finish(self) -> ConfigFlowResult:
        await self.async_set_unique_id(DOMAIN)
        self._abort_if_unique_id_configured()
        bindings = {entity: calendar_identity(self.hass, entity) for entity in self._pending_values[CONF_CALENDARS]}
        return self.async_create_entry(title="Activity Images", data={**self._pending_values, CALENDAR_BINDINGS: bindings})

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if self._async_current_entries():
            return self.async_abort(reason="already_configured")
        errors = {}
        if user_input is not None:
            try:
                self._pending_values = _form_settings(user_input)
                if self._pending_values[CONF_AI_ENTITY] and is_own_provider_entity(
                    self.hass, None, self._pending_values[CONF_AI_ENTITY]
                ):
                    raise ValueError("Activity Images cannot use itself as its image provider")
                from .settings_diagnostics import provider_diagnostics
                if self._pending_values[CONF_AI_ENTITY]:
                    status = provider_diagnostics(self.hass, None, self._pending_values)["status"]
                    if status in {"unavailable", "image_generation_unsupported", "attachments_unsupported"}:
                        errors["base"] = "provider_unavailable"
            except ValueError:
                errors["base"] = "invalid_settings"
            else:
                if errors:
                    return self.async_show_form(step_id="user", data_schema=schema(user_input), errors=errors)
                if self._pending_values[CONF_AUTO_CALENDARS]:
                    return await self.async_step_confirm_auto()
                return await self._finish()
        return self.async_show_form(step_id="user", data_schema=schema(user_input), errors=errors)

    async def async_step_confirm_auto(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input and user_input.get("confirm_paid") is True:
            return await self._finish()
        return self.async_show_form(
            step_id="confirm_auto", data_schema=paid_confirmation_schema(),
            description_placeholders={"calendars": ", ".join(self._pending_values[CONF_AUTO_CALENDARS])},
        )

    async def async_step_reconfigure(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        return await self._edit_settings(self._get_reconfigure_entry(), "reconfigure", user_input)

    async def async_step_confirm_reconfigure(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        return await self._confirm_settings(self._get_reconfigure_entry(), "confirm_reconfigure", user_input)

    def _finish_settings(self, entry: ActivityImagesConfigEntry, options: dict[str, object]) -> ConfigFlowResult:
        # Existing update listeners apply settings live without interrupting paid work.
        return self.async_update_and_abort(entry, options=options)

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: config_entries.ConfigEntry) -> config_entries.OptionsFlow:
        return ActivityImagesOptionsFlow()


class ActivityImagesOptionsFlow(_SettingsEditor, config_entries.OptionsFlow):
    _confirmation_step = "confirm_auto"

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        return await self._edit_settings(self.config_entry, "init", user_input)

    async def async_step_confirm_auto(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        # Reject an out-of-order step without resolving a missing flow context.
        if self._revision is None:
            return self.async_abort(reason="invalid_settings")
        return await self._confirm_settings(self.config_entry, "confirm_auto", user_input)

    def _finish_settings(self, entry: ActivityImagesConfigEntry, options: dict[str, object]) -> ConfigFlowResult:
        return self.async_create_entry(title="", data=options)

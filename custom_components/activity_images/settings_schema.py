"""Settings contract shared by the options flow and authenticated panel.

Only config-entry options are mutable; entry.data remains the installation defaults.
No side effects or Home Assistant imports, so validation can be tested in isolation.
"""
from __future__ import annotations

import re
from collections.abc import Iterable, Iterator, Mapping
from typing import TYPE_CHECKING, TypedDict, cast

if TYPE_CHECKING:
    from . import ActivityImagesConfigEntry

from .const import (
    CONF_AI_ENTITY, CONF_AUTO_GENERATE, CONF_AUTO_CALENDARS, CONF_CALENDARS, CONF_LOOKAHEAD_DAYS,
    CONF_PROMPT_TEMPLATE, CONF_REFERENCE_MEDIA_ID,
    CALENDAR_BINDINGS, DEFAULT_LOOKAHEAD_DAYS, MAX_CALENDARS,
)
from .prompts import DEFAULT_PROMPT_TEMPLATE, upgrade_default_prompt, validate_prompt_template

class Settings(TypedDict):
    ai_task_entity_id: str
    generate_missing_calendar_images: bool
    auto_generate_calendar_entity_ids: list[str]
    calendar_entity_ids: list[str]
    calendar_lookahead_days: int
    prompt_template: str
    reference_media_content_id: str


KEYS = frozenset((
    CONF_AI_ENTITY, CONF_AUTO_GENERATE, CONF_AUTO_CALENDARS, CONF_CALENDARS, CONF_LOOKAHEAD_DAYS,
    CONF_PROMPT_TEMPLATE, CONF_REFERENCE_MEDIA_ID,
))
DEFAULTS: dict[str, object] = {
    CONF_AI_ENTITY: "",
    CONF_AUTO_GENERATE: False,
    CONF_AUTO_CALENDARS: [],
    CONF_CALENDARS: [],
    CONF_LOOKAHEAD_DAYS: DEFAULT_LOOKAHEAD_DAYS,
    CONF_PROMPT_TEMPLATE: DEFAULT_PROMPT_TEMPLATE,
    CONF_REFERENCE_MEDIA_ID: "",
}
_ENTITY = re.compile(r"^ai_task\.[a-z0-9_]+$")
_CALENDAR = re.compile(r"^calendar\.[a-z0-9_]+$")


def calendar_auto_ids(settings: Mapping[str, object]) -> tuple[str, ...]:
    """Only unmigrated settings may use the legacy global authorization."""
    if CONF_AUTO_CALENDARS in settings:
        return tuple(cast(Iterable[str], settings.get(CONF_AUTO_CALENDARS) or ()))
    return tuple(cast(Iterable[str], settings.get(CONF_CALENDARS) or ())) if settings.get(CONF_AUTO_GENERATE) is True else ()


def effective_settings(entry: ActivityImagesConfigEntry) -> Settings:
    """Resolve settings and migrate only the previously selected authorization."""
    saved = {**{k: v for k, v in entry.data.items() if k in KEYS},
             **{k: v for k, v in entry.options.items() if k in KEYS}}
    values: dict[str, object] = {**DEFAULTS, **saved, CONF_AUTO_CALENDARS: list(calendar_auto_ids(saved))}
    # Native ConfigEntry is a heterogeneous persistence boundary. Setup validates
    # this snapshot, and subsequent settings transactions validate before writing.
    values[CONF_PROMPT_TEMPLATE] = upgrade_default_prompt(cast(str, values[CONF_PROMPT_TEMPLATE]))
    return cast(Settings, values)


class ConfigEntrySettings(Mapping[str, object]):
    """Read-only live view; the native entry remains the only source of settings."""

    def __init__(self, entry: ActivityImagesConfigEntry) -> None:
        self.entry = entry

    def __getitem__(self, key: str) -> object:
        if key == CALENDAR_BINDINGS:
            return self.entry.options.get(key, self.entry.data.get(key, {}))
        if key not in KEYS:
            raise KeyError(key)
        if key == CONF_AUTO_CALENDARS:
            return effective_settings(self.entry)[CONF_AUTO_CALENDARS]
        value = self.entry.options.get(key, self.entry.data.get(key, DEFAULTS[key]))
        return upgrade_default_prompt(cast(str, value)) if key == CONF_PROMPT_TEMPLATE else value

    def __iter__(self) -> Iterator[str]:
        return iter(DEFAULTS)

    def __len__(self) -> int:
        return len(DEFAULTS)


def validate_settings(changes: Mapping[str, object], current: Mapping[str, object] | None = None) -> Settings:
    """Return a complete validated snapshot; reject unknown keys and bad types.

    The API passes a partial update and current settings; options flow uses the
    same rules for its complete form submission.
    """
    if not isinstance(changes, dict):
        raise ValueError("Settings must be an object")
    if set(changes) - KEYS:
        raise ValueError("Unknown setting key")
    values: dict[str, object] = {**DEFAULTS, **(current or {}), **changes}
    if CONF_AUTO_CALENDARS not in changes:
        # A changed selection must not expand an old global paid authorization.
        values[CONF_AUTO_CALENDARS] = list(calendar_auto_ids(current if current is not None else changes))
    provider = values[CONF_AI_ENTITY]
    if not isinstance(provider, str) or (provider and not _ENTITY.fullmatch(provider)):
        raise ValueError("Provider must be an ai_task entity")
    calendars = values[CONF_CALENDARS]
    if (not isinstance(calendars, list) or len(calendars) > MAX_CALENDARS
            or any(not isinstance(x, str) or not _CALENDAR.fullmatch(x) for x in calendars)
            or len(set(calendars)) != len(calendars)):
        raise ValueError("Calendars must be unique calendar entities")
    days = values[CONF_LOOKAHEAD_DAYS]
    if type(days) is not int or not 1 <= days <= 60:
        raise ValueError("Lookahead must be an integer between 1 and 60")
    automatic = values[CONF_AUTO_GENERATE]
    if current is not None and changes.get(CONF_AUTO_GENERATE) is True:
        raise ValueError("Use per-calendar automatic generation and paid confirmation instead of the legacy flag")
    if type(automatic) is not bool:
        raise ValueError("Automatic generation must be a boolean")
    auto_calendars = values[CONF_AUTO_CALENDARS]
    if (not isinstance(auto_calendars, list) or len(auto_calendars) > MAX_CALENDARS
            or any(not isinstance(x, str) or x not in calendars for x in auto_calendars)
            or len(set(auto_calendars)) != len(auto_calendars)):
        raise ValueError("Automatic calendars must be a unique subset of monitored calendars")
    if (auto_calendars or automatic) and not provider:
        raise ValueError("Automatic generation requires a provider")
    # Older code must not see a global true flag when reviewing a downgrade.
    reference = values[CONF_REFERENCE_MEDIA_ID]
    if (not isinstance(reference, str) or len(reference) > 1024
            or "\x00" in reference
            or (reference and not reference.startswith("media-source://"))):
        raise ValueError("Reference must be a media-source ID")
    prompt = validate_prompt_template(values[CONF_PROMPT_TEMPLATE])
    return Settings(
        ai_task_entity_id=provider,
        generate_missing_calendar_images=False,
        auto_generate_calendar_entity_ids=cast(list[str], auto_calendars),
        calendar_entity_ids=cast(list[str], calendars),
        calendar_lookahead_days=days,
        prompt_template=prompt,
        reference_media_content_id=reference,
    )

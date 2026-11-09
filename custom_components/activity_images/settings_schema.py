"""Settings contract shared by the options flow and authenticated panel.

Only config-entry options are mutable; entry.data remains the installation defaults.
No side effects or Home Assistant imports, so validation can be tested in isolation.
"""
from __future__ import annotations

import re
from collections.abc import Mapping
from urllib.parse import urlsplit

from .const import (
    CONF_AI_ENTITY, CONF_AUTO_GENERATE, CONF_CALENDARS, CONF_LOOKAHEAD_DAYS,
    CONF_LUCIDA_URL, CONF_PROMPT_TEMPLATE, CONF_REFERENCE_MEDIA_ID,
    DEFAULT_LOOKAHEAD_DAYS, MAX_CALENDARS,
)
from .prompts import DEFAULT_PROMPT_TEMPLATE, validate_prompt_template

KEYS = frozenset((
    CONF_AI_ENTITY, CONF_AUTO_GENERATE, CONF_CALENDARS, CONF_LOOKAHEAD_DAYS,
    CONF_LUCIDA_URL, CONF_PROMPT_TEMPLATE, CONF_REFERENCE_MEDIA_ID,
))
DEFAULTS = {
    CONF_AI_ENTITY: "",
    CONF_AUTO_GENERATE: False,
    CONF_CALENDARS: [],
    CONF_LOOKAHEAD_DAYS: DEFAULT_LOOKAHEAD_DAYS,
    CONF_LUCIDA_URL: "",
    CONF_PROMPT_TEMPLATE: DEFAULT_PROMPT_TEMPLATE,
    CONF_REFERENCE_MEDIA_ID: "",
}
_ENTITY = re.compile(r"^ai_task\.[a-z0-9_]+$")
_CALENDAR = re.compile(r"^calendar\.[a-z0-9_]+$")


def effective_settings(entry) -> dict:
    """Resolve settings with options taking precedence over initial entry data."""
    return {**DEFAULTS, **{k: v for k, v in entry.data.items() if k in KEYS},
            **{k: v for k, v in entry.options.items() if k in KEYS}}


class ConfigEntrySettings(Mapping):
    """Read-only live view; the native entry remains the only source of settings."""

    def __init__(self, entry):
        self.entry = entry

    def __getitem__(self, key):
        if key not in KEYS:
            raise KeyError(key)
        return self.entry.options.get(key, self.entry.data.get(key, DEFAULTS[key]))

    def __iter__(self):
        return iter(DEFAULTS)

    def __len__(self):
        return len(DEFAULTS)


def validate_settings(changes: dict, current: dict | None = None) -> dict:
    """Return a complete validated snapshot; reject unknown keys and bad types.

    The API passes a partial update and current settings; options flow uses the
    same rules for its complete form submission.
    """
    if not isinstance(changes, dict):
        raise ValueError("Settings must be an object")
    if set(changes) - KEYS:
        raise ValueError("Unknown setting key")
    values = {**DEFAULTS, **(current or {}), **changes}
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
    if type(automatic) is not bool:
        raise ValueError("Automatic generation must be a boolean")
    if automatic and not provider:
        raise ValueError("Automatic generation requires a provider")
    reference = values[CONF_REFERENCE_MEDIA_ID]
    if (not isinstance(reference, str) or len(reference) > 1024
            or "\x00" in reference
            or (reference and not reference.startswith("media-source://"))):
        raise ValueError("Reference must be a media-source ID")
    url = values[CONF_LUCIDA_URL]
    if not isinstance(url, str) or len(url) > 2048:
        raise ValueError("Invalid Lucida URL")
    if url:
        parsed = urlsplit(url)
        if (parsed.scheme not in ("http", "https") or not parsed.hostname
                or parsed.username or parsed.password or parsed.fragment
                or any(char.isspace() for char in url)):
            raise ValueError("Lucida URL must be HTTP(S) without credentials or fragment")
        # Accessing port also rejects malformed and out-of-range port numbers.
        if parsed.port == 0:
            raise ValueError("Invalid Lucida port")
    validate_prompt_template(values[CONF_PROMPT_TEMPLATE])
    return {key: values[key] for key in DEFAULTS}

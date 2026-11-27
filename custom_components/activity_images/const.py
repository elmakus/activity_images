"""Configuration keys for Activity Images."""
from typing import Final

DOMAIN = "activity_images"
CONF_AI_ENTITY: Final = "ai_task_entity_id"
CONF_REFERENCE_MEDIA_ID: Final = "reference_media_content_id"
CONF_CALENDARS: Final = "calendar_entity_ids"
CONF_LOOKAHEAD_DAYS: Final = "calendar_lookahead_days"
CONF_AUTO_GENERATE: Final = "generate_missing_calendar_images"  # Legacy upgrade input only.
CONF_AUTO_CALENDARS: Final = "auto_generate_calendar_entity_ids"
CALENDAR_BINDINGS: Final = "_calendar_registry_ids"
DEFAULT_LOOKAHEAD_DAYS = 30
MAX_CALENDARS = 10
MAX_TRACKED_TITLES = 64
CALENDAR_SCAN_MINUTES = 15
CONF_PROMPT_TEMPLATE: Final = "prompt_template"

"""Shared logical device information for the Activity Images service."""
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo

from .const import DOMAIN


def service_device_info(entry_id: str) -> DeviceInfo:
    """Group both primary entities under one stable service device."""
    return DeviceInfo(
        identifiers={(DOMAIN, entry_id)},
        name="Activity Images",
        entry_type=DeviceEntryType.SERVICE,
    )

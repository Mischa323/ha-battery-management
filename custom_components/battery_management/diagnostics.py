"""Diagnostics download for one config entry.

Adds the "Download diagnostics" button to the integration page. During a shadow
month that button is the whole answer to "send me the data": it carries the
configuration, the live state, and a flight recorder of recent control ticks -
enough to explain any single decision without anyone taking screenshots.

The month-long trend is not in here on purpose. That lives in Home Assistant's
long-term statistics, which the setpoint and per-unit target sensors feed
automatically because they carry a state class.

Nothing is redacted: it is entity ids and watts, no credentials or coordinates.
"""
from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import DOMAIN
from .coordinator import BatteryCoordinator


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    coordinator: BatteryCoordinator = hass.data[DOMAIN][entry.entry_id]
    return coordinator.diagnostics()

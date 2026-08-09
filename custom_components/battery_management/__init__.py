"""The Battery Management integration."""
from __future__ import annotations

import logging
from pathlib import Path

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.helpers import config_validation as cv

from .const import DOMAIN
from .coordinator import BatteryCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [
    Platform.SWITCH,
    Platform.BUTTON,
    Platform.SENSOR,
    Platform.BINARY_SENSOR,
    Platform.NUMBER,
    Platform.SELECT,
]

SERVICE_SET_SETPOINT = "set_setpoint"
SERVICE_START_FAST_CHARGE = "start_fast_charge"
SERVICE_STOP_FAST_CHARGE = "stop_fast_charge"

ATTR_CONFIG_ENTRY_ID = "config_entry_id"
ATTR_SETPOINT = "setpoint"

_SERVICES_KEY = f"{DOMAIN}_services_registered"

_TARGET_SCHEMA = vol.Schema({vol.Optional(ATTR_CONFIG_ENTRY_ID): cv.string})
_SET_SETPOINT_SCHEMA = _TARGET_SCHEMA.extend(
    {vol.Required(ATTR_SETPOINT): vol.Coerce(float)}
)

CARD_FILENAME = "battery-management-card.js"
CARD_URL = f"/{DOMAIN}/{CARD_FILENAME}"
_CARD_KEY = f"{DOMAIN}_card_registered"


async def _async_register_card(hass: HomeAssistant) -> None:
    """Serve the Lovelace card and add it as an extra frontend module (once)."""
    if hass.data.get(_CARD_KEY):
        return
    card_path = str(Path(__file__).parent / "www" / CARD_FILENAME)
    try:
        from homeassistant.components.http import StaticPathConfig

        await hass.http.async_register_static_paths(
            [StaticPathConfig(CARD_URL, card_path, False)]
        )
    except Exception:  # noqa: BLE001  -- fall back to the legacy sync API
        try:
            hass.http.register_static_path(CARD_URL, card_path, False)
        except Exception:  # noqa: BLE001
            _LOGGER.warning("Could not register the Battery Management card static path")
            return

    try:
        from homeassistant.components.frontend import add_extra_js_url

        add_extra_js_url(hass, CARD_URL)
    except Exception:  # noqa: BLE001
        _LOGGER.warning(
            "Could not auto-add the card resource; add %s manually under Dashboards > Resources",
            CARD_URL,
        )

    hass.data[_CARD_KEY] = True


def _targets(hass: HomeAssistant, call: ServiceCall) -> list[BatteryCoordinator]:
    """Coordinators a service call applies to; all of them unless narrowed."""
    stored: dict = hass.data.get(DOMAIN, {})
    entry_id = call.data.get(ATTR_CONFIG_ENTRY_ID)
    if entry_id is None:
        return list(stored.values())
    coordinator = stored.get(entry_id)
    return [coordinator] if coordinator else []


@callback
def _async_register_services(hass: HomeAssistant) -> None:
    """Register the domain services once, not per config entry."""
    if hass.data.get(_SERVICES_KEY):
        return

    async def _set_setpoint(call: ServiceCall) -> None:
        for coordinator in _targets(hass, call):
            await coordinator.async_set_setpoint(call.data[ATTR_SETPOINT])

    async def _start_fast_charge(call: ServiceCall) -> None:
        for coordinator in _targets(hass, call):
            await coordinator.async_set_fast_charge(True)

    async def _stop_fast_charge(call: ServiceCall) -> None:
        for coordinator in _targets(hass, call):
            await coordinator.async_set_fast_charge(False)

    hass.services.async_register(
        DOMAIN, SERVICE_SET_SETPOINT, _set_setpoint, schema=_SET_SETPOINT_SCHEMA
    )
    hass.services.async_register(
        DOMAIN, SERVICE_START_FAST_CHARGE, _start_fast_charge, schema=_TARGET_SCHEMA
    )
    hass.services.async_register(
        DOMAIN, SERVICE_STOP_FAST_CHARGE, _stop_fast_charge, schema=_TARGET_SCHEMA
    )
    hass.data[_SERVICES_KEY] = True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Battery Management from a config entry."""
    await _async_register_card(hass)
    _async_register_services(hass)

    coordinator = BatteryCoordinator(hass, entry)
    await coordinator.async_start()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_reload))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry and revert the batteries to a safe mode."""
    coordinator: BatteryCoordinator = hass.data[DOMAIN].get(entry.entry_id)
    if coordinator:
        await coordinator.async_stop(revert=True)

    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id, None)
    return unload_ok


async def _async_reload(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload when options change."""
    await hass.config_entries.async_reload(entry.entry_id)

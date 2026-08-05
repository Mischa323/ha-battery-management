"""Select entity: which strategy the coordinator follows."""
from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import BatteryCoordinator


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: BatteryCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([ModeSelect(coordinator, entry)])


class ModeSelect(SelectEntity):
    """One strategy at a time, rather than a stack of rules fighting.

    Every mode is grid-zero regulation with a bound on the setpoint, so the
    packs keep responding to the house and the sun inside whatever you pick.

    Pause is not the kill-switch: the units stay under third-party control and
    simply hold at 0. The kill-switch hands them back to self-consumption
    entirely, which is a different thing to want.
    """

    _attr_has_entity_name = True
    _attr_should_poll = False
    _attr_name = "Mode"
    _attr_icon = "mdi:tune-variant"
    _attr_translation_key = "mode"

    def __init__(self, coordinator: BatteryCoordinator, entry: ConfigEntry) -> None:
        self.coordinator = coordinator
        # per entry, not per class: Dynamic only exists with a price sensor
        self._attr_options = coordinator.available_modes
        self._attr_unique_id = f"{entry.entry_id}_mode"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name="Battery Management",
            manufacturer="Battery Management",
        )

    async def async_added_to_hass(self) -> None:
        self.coordinator.async_add_listener(self.async_write_ha_state)

    @property
    def current_option(self) -> str:
        return self.coordinator.mode

    async def async_select_option(self, option: str) -> None:
        await self.coordinator.async_set_mode(option)

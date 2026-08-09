"""Button entities: re-run the phase detection on demand.

The coordinator probes on its own when a placement is missing. This is for the
cases it cannot know about - an electrician moved a socket, a pack was swapped
for a replacement under warranty, or the first probe landed on the wrong leg and
somebody looked in the meter cupboard and disagreed.
"""
from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import BatteryCoordinator


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: BatteryCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([DetectPhasesButton(coordinator, entry)])


class DetectPhasesButton(ButtonEntity):
    _attr_has_entity_name = True
    _attr_should_poll = False
    _attr_name = "Detect phases"
    _attr_icon = "mdi:transmission-tower"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: BatteryCoordinator, entry: ConfigEntry) -> None:
        self.coordinator = coordinator
        self._attr_unique_id = f"{entry.entry_id}_detect_phases"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name="Battery Management",
            manufacturer="Battery Management",
        )

    @property
    def available(self) -> bool:
        # nothing to detect without per-phase sensors to watch
        return self.coordinator.phase_protection

    async def async_press(self) -> None:
        # a typed-in phase survives this on purpose - it is re-applied every
        # tick anyway, so clearing it would only look like it worked. To
        # re-measure one of those, set the unit's phase field back to 0.
        self.coordinator.async_request_phase_detection()

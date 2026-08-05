"""Binary sensors: overall health, and reachability per unit."""
from __future__ import annotations

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import BatteryCoordinator, UnitConfig


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: BatteryCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        [HealthyBinarySensor(coordinator, entry)]
        + [
            UnitOnlineBinarySensor(coordinator, entry, index, unit)
            for index, unit in enumerate(coordinator.units)
        ]
    )


class _BaseBinarySensor(BinarySensorEntity):
    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, coordinator: BatteryCoordinator, entry: ConfigEntry) -> None:
        self.coordinator = coordinator
        self._entry = entry
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name="Battery Management",
            manufacturer="Battery Management",
        )

    async def async_added_to_hass(self) -> None:
        self.coordinator.async_add_listener(self.async_write_ha_state)


class HealthyBinarySensor(_BaseBinarySensor):
    _attr_name = "Healthy"
    _attr_device_class = BinarySensorDeviceClass.PROBLEM

    def __init__(self, coordinator, entry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_healthy"

    @property
    def is_on(self) -> bool:
        # PROBLEM device class: on = problem present
        return self.coordinator.status == "degraded"


class UnitOnlineBinarySensor(_BaseBinarySensor):
    """Whether this unit's SoC sensor was readable on the last tick.

    Off means the coordinator skipped the unit. Because Third-Party Control has
    no watchdog, a skipped unit keeps executing its last command - so pair this
    with the unit's target sensor when something looks wrong.
    """

    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY

    def __init__(
        self,
        coordinator: BatteryCoordinator,
        entry: ConfigEntry,
        index: int,
        unit: UnitConfig,
    ) -> None:
        super().__init__(coordinator, entry)
        self._unit_name = unit.name
        self._attr_name = f"{unit.name} online"
        # keyed on position, so renaming a unit keeps its history
        self._attr_unique_id = f"{entry.entry_id}_unit{index}_online"

    @property
    def is_on(self) -> bool:
        return self.coordinator.unit_status[self._unit_name].online

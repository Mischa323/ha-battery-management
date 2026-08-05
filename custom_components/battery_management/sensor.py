"""Sensor entities: the control setpoint, the status, and a per-unit target."""
from __future__ import annotations

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfPower, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, POLICIES
from .coordinator import BatteryCoordinator, UnitConfig


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: BatteryCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        [
            SetpointSensor(coordinator, entry),
            StatusSensor(coordinator, entry),
            ActivePolicySensor(coordinator, entry),
            MinutesToFullSensor(coordinator, entry),
        ]
        + [
            UnitTargetSensor(coordinator, entry, index, unit)
            for index, unit in enumerate(coordinator.units)
        ]
    )


class _BaseSensor(SensorEntity):
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


class SetpointSensor(_BaseSensor):
    _attr_name = "Setpoint"
    _attr_native_unit_of_measurement = UnitOfPower.WATT
    _attr_device_class = SensorDeviceClass.POWER
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:sine-wave"

    def __init__(self, coordinator, entry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_setpoint"

    @property
    def native_value(self) -> int:
        # positive = total discharge, negative = total charge
        return int(round(self.coordinator.setpoint))


class StatusSensor(_BaseSensor):
    _attr_name = "Status"
    _attr_icon = "mdi:state-machine"

    def __init__(self, coordinator, entry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_status"

    @property
    def native_value(self) -> str:
        return self.coordinator.status

    @property
    def extra_state_attributes(self) -> dict:
        # the first place anyone looks, so do not make them hunt for this
        return {
            "dry_run": self.coordinator.dry_run,
            "suppressed_commands": self.coordinator.suppressed_commands,
        }


class ActivePolicySensor(_BaseSensor):
    """Why the coordinator is doing what it is doing, right now.

    Once there is more than one rule that can hold the packs back, "the battery
    just sits there" becomes an unanswerable question at a site you do not live
    at. This turns it into a readable state instead of a log dig.
    """

    _attr_translation_key = "active_policy"
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = POLICIES
    _attr_icon = "mdi:help-circle-outline"

    def __init__(self, coordinator, entry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_active_policy"

    @property
    def native_value(self) -> str:
        return self.coordinator.active_policy


class MinutesToFullSensor(_BaseSensor):
    """How long a fast charge would take from right now.

    Unavailable until the empty-to-full time has been measured and entered - a
    "be full by 18:00" built on a guessed duration is worse than none at all.

    The integration does the arithmetic because it knows the state of charge and
    the limits; deciding *when* to act is left to an automation, the same split
    as the schedule blueprints.
    """

    _attr_name = "Minutes to full"
    _attr_native_unit_of_measurement = UnitOfTime.MINUTES
    _attr_device_class = SensorDeviceClass.DURATION
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:battery-clock"

    def __init__(self, coordinator, entry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_minutes_to_full"

    @property
    def available(self) -> bool:
        return self.coordinator.minutes_to_full() is not None

    @property
    def native_value(self) -> int | None:
        return self.coordinator.minutes_to_full()


class UnitTargetSensor(_BaseSensor):
    """What this unit was last commanded to do, signed like the setpoint.

    This is the coordinator's own command, not a reading back from the unit:
    the Modbus power sensors lag by 10-30 s, so comparing this against them is
    exactly how you spot a unit that is not following orders.
    """

    _attr_native_unit_of_measurement = UnitOfPower.WATT
    _attr_device_class = SensorDeviceClass.POWER
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:battery-charging-outline"

    def __init__(
        self,
        coordinator: BatteryCoordinator,
        entry: ConfigEntry,
        index: int,
        unit: UnitConfig,
    ) -> None:
        super().__init__(coordinator, entry)
        self._unit_name = unit.name
        self._attr_name = f"{unit.name} target"
        # keyed on position, so renaming a unit keeps its history
        self._attr_unique_id = f"{entry.entry_id}_unit{index}_target"

    @property
    def native_value(self) -> int:
        return self.coordinator.unit_status[self._unit_name].target

    @property
    def extra_state_attributes(self) -> dict:
        status = self.coordinator.unit_status[self._unit_name]
        return {
            "grid_flow": status.flow,
            "commanded_watts": abs(status.target),
            "soc": status.soc,
        }

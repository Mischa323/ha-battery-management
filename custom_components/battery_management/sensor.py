"""Sensor entities: the control setpoint, the status, and a per-unit target."""
from __future__ import annotations

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE, UnitOfEnergy, UnitOfPower, UnitOfTime
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
            SolarRemainingSensor(coordinator, entry),
            ChargeCeilingSensor(coordinator, entry),
            PlanSensor(coordinator, entry),
            GridObservedSensor(coordinator, entry),
            GridUsedSensor(coordinator, entry),
            OtherControllerSensor(coordinator, entry),
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


class _GridSensor(_BaseSensor):
    _attr_native_unit_of_measurement = UnitOfPower.WATT
    _attr_device_class = SensorDeviceClass.POWER
    _attr_state_class = SensorStateClass.MEASUREMENT


class GridObservedSensor(_GridSensor):
    """The meter reading as the coordinator actually read it.

    A mirror of your own P1 sensor, and that is the point: it proves the right
    entity was picked, that it parses, and that the sign convention matches
    (+ import). If this disagrees with your meter, nothing downstream can be
    trusted. Goes unavailable the moment the meter cannot be read, which is the
    fastest way to notice.
    """

    _attr_name = "Grid power (as read)"
    _attr_icon = "mdi:transmission-tower"

    def __init__(self, coordinator, entry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_grid_observed"

    @property
    def available(self) -> bool:
        return self.coordinator.last_grid_observed is not None

    @property
    def native_value(self):
        return self.coordinator.last_grid_observed


class GridUsedSensor(_GridSensor):
    """What it actually regulated against.

    Equal to the reading above when live. During a shadow run it is the
    reconstruction - the meter as it would read if this coordinator were in
    charge instead of the site's own automations - and comparing the two is how
    you check the reconstruction is sane.
    """

    _attr_name = "Grid power (regulated against)"
    _attr_icon = "mdi:transmission-tower-import"

    def __init__(self, coordinator, entry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_grid_used"

    @property
    def available(self) -> bool:
        return self.coordinator.last_grid_used is not None

    @property
    def native_value(self):
        return self.coordinator.last_grid_used


class OtherControllerSensor(_GridSensor):
    """What the packs are being told to do by whoever is in charge.

    Only meaningful during a shadow run: it is read back from the target and
    flow entities, so it is the *other* system's command. Signed like our own
    targets, + discharging.
    """

    _attr_name = "Other controller"
    _attr_icon = "mdi:account-arrow-left"

    def __init__(self, coordinator, entry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_other_controller"

    @property
    def available(self) -> bool:
        return self.coordinator.last_other_power is not None

    @property
    def native_value(self):
        return self.coordinator.last_other_power


class SolarRemainingSensor(_BaseSensor):
    """Sun still to come today. What the buy ceiling is computed from."""

    _attr_name = "Solar remaining"
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
    _attr_device_class = SensorDeviceClass.ENERGY_STORAGE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:weather-sunny"

    def __init__(self, coordinator, entry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_solar_remaining"

    @property
    def available(self) -> bool:
        return self.coordinator.solar_remaining() is not None

    @property
    def native_value(self):
        return self.coordinator.solar_remaining()

    @property
    def extra_state_attributes(self) -> dict:
        # the parts, so "0 kWh remaining" can be told apart from a sensor that
        # simply is not reading
        return self.coordinator.solar_breakdown()


class ChargeCeilingSensor(_BaseSensor):
    """How full it is worth buying to, after the hand-set bounds.

    Unavailable until the empty-to-full time is measured: without a capacity
    there is no way to turn kWh of expected sun into a percentage.
    """

    _attr_name = "Charge ceiling"
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:battery-arrow-up"

    def __init__(self, coordinator, entry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_charge_ceiling"

    @property
    def available(self) -> bool:
        return self.coordinator.charge_ceiling() is not None

    @property
    def native_value(self):
        ceiling = self.coordinator.charge_ceiling()
        return None if ceiling is None else round(ceiling)


class PlanSensor(_BaseSensor):
    """Today's intentions, for a dashboard to render.

    A summary of the inputs and the hours they pick out - not a prediction of
    the setpoint. That depends on the house minute by minute, and a graph
    claiming otherwise would look authoritative and be wrong.
    """

    _attr_name = "Plan"
    _attr_icon = "mdi:calendar-clock"

    def __init__(self, coordinator, entry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_plan"

    @property
    def native_value(self) -> str:
        plan = self.coordinator.plan()
        if not plan["has_prices"]:
            return "no prices"
        return f"{len(plan['cheap_hours'])} cheap, {len(plan['dear_hours'])} dear"

    @property
    def extra_state_attributes(self) -> dict:
        return self.coordinator.plan()


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

"""Sensor entities: the control setpoint, the status, and a per-unit target."""
from __future__ import annotations

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    EntityCategory,
    PERCENTAGE,
    UnitOfElectricCurrent,
    UnitOfEnergy,
    UnitOfPower,
    UnitOfTime,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    DOMAIN,
    PERIOD_DAY,
    PERIOD_MONTH,
    PERIOD_WEEK,
    PHASE_DETECT_STATES,
    POLICIES,
)
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
            CurrentPriceSensor(coordinator, entry),
            MarketPriceSensor(coordinator, entry),
            GridObservedSensor(coordinator, entry),
            GridUsedSensor(coordinator, entry),
            OtherControllerSensor(coordinator, entry),
            FuseHeadroomSensor(coordinator, entry),
            PhaseDetectionSensor(coordinator, entry),
            ChargedTotalSensor(coordinator, entry),
            ChargedFromGridSensor(coordinator, entry),
            ChargedTodaySensor(coordinator, entry),
            ChargedFromGridTodaySensor(coordinator, entry),
            ChargedThisWeekSensor(coordinator, entry),
            ChargedFromGridThisWeekSensor(coordinator, entry),
            ChargedThisMonthSensor(coordinator, entry),
            ChargedFromGridThisMonthSensor(coordinator, entry),
        ]
        + [
            entity
            for index, unit in enumerate(coordinator.units)
            for entity in (
                UnitTargetSensor(coordinator, entry, index, unit),
                UnitPhaseSensor(coordinator, entry, index, unit),
            )
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

    _attr_name = "Fast charge duration"
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


class CurrentPriceSensor(_BaseSensor):
    """What this hour costs. The first thing anyone looks for, and it was
    only reachable by reading an attribute of the Plan sensor."""

    _attr_name = "Current price"
    _attr_native_unit_of_measurement = "EUR/kWh"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 3
    _attr_icon = "mdi:cash-clock"

    def __init__(self, coordinator, entry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_current_price"

    @property
    def available(self) -> bool:
        return self.coordinator.current_price() is not None

    @property
    def native_value(self) -> float | None:
        now = self.coordinator.current_price()
        return None if now is None else now["price"]

    @property
    def extra_state_attributes(self) -> dict:
        now = self.coordinator.current_price()
        if now is None:
            return {}
        # the role is the same one the chart colours by, so a dashboard never
        # has to invent its own idea of "expensive"
        return {k: v for k, v in now.items() if k != "price"}


class MarketPriceSensor(_BaseSensor):
    """The exchange price this hour, without tax or markup.

    For the Energy dashboard: import is billed all-in, export is not, so the
    two want different entities. What a supplier pays back is calculated from
    this - check your own contract for what they add or take off.
    """

    _attr_name = "Market price"
    _attr_native_unit_of_measurement = "EUR/kWh"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 3
    _attr_icon = "mdi:transmission-tower-export"

    def __init__(self, coordinator, entry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_market_price"

    @property
    def available(self) -> bool:
        return self.coordinator.current_market_price() is not None

    @property
    def native_value(self) -> float | None:
        return self.coordinator.current_market_price()


class _ChargedSensor(_BaseSensor):
    """A cumulative kilowatt-hour counter kept by the coordinator.

    Energy, not power, so Home Assistant records it as a total and it can go
    straight onto the Energy dashboard. TOTAL_INCREASING because it only ever
    goes up: nothing resets it but a reinstall. For a monthly figure use the
    `…_this_month` pair below - counted here rather than left to a
    `utility_meter` helper, because that would be per-site YAML carrying each
    house's own entity ids, and this is installed at several sites from one
    repo.

    Deliberately **unavailable** rather than 0 while no pack has a charging
    power sensor configured. Zero is a measurement, and a graph flat at zero
    reads as "nothing was charged" rather than "nobody is counting" - the same
    reasoning that leaves minutes-to-full unavailable without a measured
    charge time.
    """

    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_suggested_display_precision = 2

    @property
    def available(self) -> bool:
        return self.coordinator.counts_charge_energy


class ChargedTotalSensor(_ChargedSensor):
    _attr_name = "Charged"
    _attr_icon = "mdi:battery-plus-variant"

    def __init__(self, coordinator, entry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_charged"

    @property
    def native_value(self) -> float:
        return round(self.coordinator.charged_wh / 1000.0, 3)


class ChargedFromGridSensor(_ChargedSensor):
    """The bought half. Whatever is left over came off the roof.

    The sun is published as the remainder rather than counted separately, so
    the two halves always add up to the total that really went in - two
    independent counters would drift apart within a day, and then the split
    would be a split of something that is not the whole.
    """

    _attr_name = "Charged from grid"
    _attr_icon = "mdi:transmission-tower-import"

    def __init__(self, coordinator, entry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_charged_from_grid"

    @property
    def native_value(self) -> float:
        return round(self.coordinator.charged_grid_wh / 1000.0, 3)

    @property
    def extra_state_attributes(self) -> dict:
        solar = max(self.coordinator.charged_wh - self.coordinator.charged_grid_wh, 0.0)
        return {"charged_from_solar_kwh": round(solar / 1000.0, 3)}


class _PeriodChargedSensor(_ChargedSensor):
    """The same measurement, over one calendar period.

    `TOTAL` rather than `TOTAL_INCREASING`, paired with `last_reset`. That pair
    is how Home Assistant is told "a drop to nought here is a new period, not a
    broken sensor" - without it the statistics engine would read every 1st of
    the month, and every Monday, and every midnight, as a meter that had been
    replaced, and the long-term sums would be wrong in a way that only shows up
    much later on a figure nobody can check by eye.

    Three lengths of the *same* accumulation, not three measurements - the
    coordinator counts once and these read it at day, week and month. The
    lifetime counters stay exactly as they were; they are the ones the Energy
    dashboard wants, and none of this replaces them.
    """

    _attr_state_class = SensorStateClass.TOTAL
    #: which period this one reads, from `const.PERIODS`
    _period: str

    @property
    def last_reset(self):
        return self.coordinator.period_started_at(self._period)


class _PeriodTotalSensor(_PeriodChargedSensor):
    """A period's total, carrying the periods before it as attributes.

    The history rides on the total rather than being published as its own
    state, because it is a record and not a measurement: it changes once per
    period, and Home Assistant stores an unchanged attribute set once however
    many state rows point at it. All three figures of every closed period are
    in the one dict, so a period can never be read with its total from one
    place and its grid share from another.
    """

    _attr_icon = "mdi:battery-plus-variant"

    @property
    def native_value(self) -> float:
        return self.coordinator.period_charged_kwh(self._period)

    @property
    def extra_state_attributes(self) -> dict:
        return {
            "period": self._period,
            "key": self.coordinator.periods[self._period]["key"],
            "history": self.coordinator.period_history(self._period),
        }


class _PeriodGridSensor(_PeriodChargedSensor):
    """The bought half of a period. The remainder came off the roof."""

    _attr_icon = "mdi:transmission-tower-import"

    @property
    def native_value(self) -> float:
        return self.coordinator.period_charged_kwh(self._period, grid=True)

    @property
    def extra_state_attributes(self) -> dict:
        # the remainder, never a second count: two independent counters would
        # drift apart within a day and then the split would be a split of
        # something that is not the whole
        solar = max(
            self.coordinator.period_charged_kwh(self._period)
            - self.coordinator.period_charged_kwh(self._period, grid=True),
            0.0,
        )
        return {"charged_from_solar_kwh": round(solar, 3)}


# Written out rather than generated in a loop. Six short classes are greppable
# from an entity id, which is what somebody staring at a dashboard actually
# has to work backwards from; a factory would leave nothing to search for.


class ChargedTodaySensor(_PeriodTotalSensor):
    _attr_name = "Charged today"
    _period = PERIOD_DAY

    def __init__(self, coordinator, entry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_charged_today"


class ChargedFromGridTodaySensor(_PeriodGridSensor):
    _attr_name = "Charged from grid today"
    _period = PERIOD_DAY

    def __init__(self, coordinator, entry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_charged_from_grid_today"


class ChargedThisWeekSensor(_PeriodTotalSensor):
    _attr_name = "Charged this week"
    _period = PERIOD_WEEK

    def __init__(self, coordinator, entry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_charged_this_week"


class ChargedFromGridThisWeekSensor(_PeriodGridSensor):
    _attr_name = "Charged from grid this week"
    _period = PERIOD_WEEK

    def __init__(self, coordinator, entry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_charged_from_grid_this_week"


class ChargedThisMonthSensor(_PeriodTotalSensor):
    _attr_name = "Charged this month"
    _period = PERIOD_MONTH

    def __init__(self, coordinator, entry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_charged_this_month"


class ChargedFromGridThisMonthSensor(_PeriodGridSensor):
    _attr_name = "Charged from grid this month"
    _period = PERIOD_MONTH

    def __init__(self, coordinator, entry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_charged_from_grid_this_month"


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
            # which leg of the supply this pack sits on, once known - the fuse
            # protection is only as good as this answer
            "phase": self.coordinator.unit_phase.get(self._unit_name),
        }


class FuseHeadroomSensor(_BaseSensor):
    """How close the busiest leg is to its main fuse.

    One number, because that is the one worth putting on a dashboard: the
    tightest leg is the one that trips. The per-leg detail is in the attributes,
    including which packs are believed to be on each.
    """

    _attr_name = "Fuse headroom"
    _attr_native_unit_of_measurement = UnitOfElectricCurrent.AMPERE
    _attr_device_class = SensorDeviceClass.CURRENT
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:fuse"

    def __init__(self, coordinator, entry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_fuse_headroom"

    @property
    def available(self) -> bool:
        return self.coordinator.fuse_headroom_amps() is not None

    @property
    def native_value(self) -> float | None:
        return self.coordinator.fuse_headroom_amps()

    @property
    def extra_state_attributes(self) -> dict:
        if not self.coordinator.phase_protection:
            return {}
        return self.coordinator.phase_report()


class PhaseDetectionSensor(_BaseSensor):
    """Whether we know which pack is on which leg, and how we found out."""

    # a translation key, not a name: without one Home Assistant never applies
    # the state translations and the dashboard shows the bare slug
    _attr_translation_key = "phase_detection"
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_options = list(PHASE_DETECT_STATES)
    _attr_icon = "mdi:transmission-tower"

    def __init__(self, coordinator, entry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_phase_detection"

    @property
    def native_value(self) -> str:
        return self.coordinator.phase_detection

    @property
    def extra_state_attributes(self) -> dict:
        return {
            "unit_phase": dict(self.coordinator.unit_phase),
            "detected_at": self.coordinator.phase_detected_at,
            # the deltas each probe saw - the only way to tell a confident
            # placement from a lucky one
            "probes": self.coordinator.phase_probe_detail,
        }


class UnitPhaseSensor(_BaseSensor):
    """Which leg of the supply this pack sits on.

    It is already an attribute on the target sensor and in the fuse-headroom
    detail, but an attribute is not something you can put on a dashboard
    without writing a template - and "which pack is on which phase" is exactly
    the thing somebody wants to check at a glance after a detection run.

    Not translated on purpose: L1, L2 and L3 read the same in every language.
    """

    _attr_icon = "mdi:transmission-tower"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(
        self,
        coordinator: BatteryCoordinator,
        entry: ConfigEntry,
        index: int,
        unit: UnitConfig,
    ) -> None:
        super().__init__(coordinator, entry)
        self._unit_name = unit.name
        self._attr_name = f"{unit.name} phase"
        self._attr_unique_id = f"{entry.entry_id}_unit{index}_phase"

    @property
    def available(self) -> bool:
        return self.coordinator.phase_protection

    @property
    def native_value(self) -> str | None:
        phase = self.coordinator.unit_phase.get(self._unit_name)
        return None if phase is None else f"L{phase}"

    @property
    def extra_state_attributes(self) -> dict:
        return {
            # a measured placement can be wrong; the evidence says how sure it is
            "source": self.coordinator.phase_source(self._unit_name),
            "detection": self.coordinator.phase_detection,
            "probe": self.coordinator.phase_probe_detail.get(self._unit_name),
        }

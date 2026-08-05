"""Core control logic for the Battery Management.

One "brain" that drives 2+ Anker SOLIX Max AC units (in Third-Party Control)
against a household grid-power sensor so they behave as a single system:

* anti-windup integral control -> the meter is driven toward ~0 (small import bias)
* both units always move the SAME direction (both charge or both discharge)
  -> cross-charging is structurally impossible
* SoC-weighted split -> the fuller unit discharges more / the emptier charges more
* min-output flooring -> tiny shares are consolidated onto one unit (no micro-cycling)
* kill-switch + fast-charge overrides, and a safe revert to self_consumption
"""
from __future__ import annotations

import logging
import time
from collections import deque
from dataclasses import dataclass

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .prices import is_cheap_now, parse_forecast
from .const import (
    CONF_BIAS,
    CONF_CHARGE_LIMIT,
    CONF_DEADBAND,
    CONF_DISCHARGE_LIMIT,
    CONF_FLOW_SELECT,
    CONF_GRID_POWER,
    CONF_INTERVAL,
    CONF_KP,
    CONF_MIN_OUTPUT,
    CONF_MODE_CONTROL,
    CONF_MODE_SAFE,
    CONF_MODE_SELECT,
    CONF_SOC_SENSOR,
    CONF_TARGET_NUMBER,
    CONF_FAST_CHARGE_HOLD,
    CONF_BATTERY_POWER_SENSOR,
    CONF_CHARGE_BELOW_SOC,
    CONF_CHEAP_HOURS,
    CONF_EXTERNAL_TIMEOUT,
    CONF_FULL_CHARGE_MINUTES,
    CONF_PRICE_SENSOR,
    CONF_SOLAR_FORECAST_MAX,
    CONF_SHADOW_SIMULATE,
    CONF_SOLAR_FORECAST_SENSOR,
    CONF_UNIT_MAX,
    CONF_UNIT_NAME,
    CONF_UNITS,
    DEFAULT_BIAS,
    DEFAULT_DEADBAND,
    DEFAULT_DRY_RUN,
    DEFAULT_EXTERNAL_TIMEOUT,
    DEFAULT_FAST_CHARGE_HOLD,
    DEFAULT_CHARGE_BELOW_SOC,
    DEFAULT_CHEAP_HOURS,
    DEFAULT_FULL_CHARGE_MINUTES,
    DEFAULT_INTERVAL,
    DEFAULT_KP,
    DEFAULT_MIN_OUTPUT,
    DEFAULT_MODE,
    DEFAULT_SHADOW_SIMULATE,
    DEFAULT_SOC_RESERVE,
    DEFAULT_SOLAR_FORECAST_MAX,
    PRICE_WINDOW_HOURS,
    DEFAULT_UNIT_MAX,
    DOMAIN,
    FALLBACK_CHARGE_LIMIT,
    FALLBACK_DISCHARGE_LIMIT,
    FLOW_CHARGE,
    FLOW_DISCHARGE,
    MAX_SETPOINT_AGE,
    MODE_CHARGE_ONLY,
    MODE_DISCHARGE_ONLY,
    MODE_DYNAMIC,
    MODE_EXTERNAL,
    MODE_PAUSE,
    MODES,
    DEVICE_MODE_SELF,
    DEVICE_MODE_THIRD_PARTY,
    POLICY_DEADBAND,
    POLICY_DISABLED,
    POLICY_DYNAMIC_CHARGE,
    POLICY_DYNAMIC_NO_PRICES,
    POLICY_EXTERNAL,
    POLICY_EXTERNAL_STALE,
    POLICY_FAST_CHARGE,
    POLICY_FAST_CHARGE_HOLD,
    POLICY_GRID_ZERO,
    POLICY_MODE_CHARGE_ONLY,
    POLICY_MODE_DISCHARGE_ONLY,
    POLICY_MODE_PAUSE,
    POLICY_NO_GRID_DATA,
    POLICY_PACKS_EMPTY,
    POLICY_PACKS_FULL,
    POLICY_SOC_RESERVE,
    SAVE_DELAY,
    TICK_LOG_SIZE,
    STORAGE_VERSION,
    UNAVAILABLE_STATES,
)

_LOGGER = logging.getLogger(__name__)


@dataclass
class UnitConfig:
    """Entity references for one battery unit."""

    name: str
    mode_select: str
    flow_select: str
    target_number: str
    soc_sensor: str
    charge_limit: str | None = None
    discharge_limit: str | None = None
    mode_control: str = DEVICE_MODE_THIRD_PARTY
    #: empty means "do not touch the mode, just command 0" - which is what a
    #: unit without its own meter needs, since it has no self-consumption mode
    #: to fall back to. Holding 0 W indefinitely is a safe resting state; it is
    #: holding a *non-zero* command that gotcha 1 warns about.
    mode_safe: str | None = DEVICE_MODE_SELF

    @classmethod
    def from_entry(cls, raw: dict) -> "UnitConfig":
        """Build from a config-entry dict, which is keyed by the CONF_* names.

        The stored keys deliberately differ from the field names (they are the
        keys the config flow and translations use), so map them explicitly.
        The two SoC-limit entities are optional and simply absent when the user
        leaves them blank.
        """
        return cls(
            name=raw[CONF_UNIT_NAME],
            mode_select=raw[CONF_MODE_SELECT],
            flow_select=raw[CONF_FLOW_SELECT],
            target_number=raw[CONF_TARGET_NUMBER],
            soc_sensor=raw[CONF_SOC_SENSOR],
            charge_limit=raw.get(CONF_CHARGE_LIMIT),
            discharge_limit=raw.get(CONF_DISCHARGE_LIMIT),
            # entries created before these existed keep the old fixed behaviour
            mode_control=raw.get(CONF_MODE_CONTROL) or DEVICE_MODE_THIRD_PARTY,
            mode_safe=raw.get(CONF_MODE_SAFE, DEVICE_MODE_SELF) or None,
        )


@dataclass
class UnitState:
    """Live snapshot of one unit used inside a control tick."""

    cfg: UnitConfig
    online: bool
    soc: float
    charge_limit: float
    discharge_limit: float
    unit_max: float


@dataclass
class UnitStatus:
    """What the coordinator last saw and last commanded per unit.

    `target` deliberately keeps the last commanded value when a unit drops
    offline: Third-Party Control has no watchdog, so the device is still
    executing that command. Zeroing it here would claim the unit had stopped.
    """

    online: bool = False
    soc: float | None = None
    target: int = 0             # + = discharging, - = charging (W)
    flow: str | None = None


class BatteryCoordinator:
    """Runs the periodic control loop and holds shared state."""

    def __init__(self, hass: HomeAssistant, entry) -> None:
        self.hass = hass
        self.entry = entry
        data = {**entry.data, **entry.options}

        self._grid_sensor: str = data[CONF_GRID_POWER]
        self._units: list[UnitConfig] = [
            UnitConfig.from_entry(u) for u in data[CONF_UNITS]
        ]

        self._bias: float = float(data.get(CONF_BIAS, DEFAULT_BIAS))
        self._deadband: float = float(data.get(CONF_DEADBAND, DEFAULT_DEADBAND))
        self._kp: float = float(data.get(CONF_KP, DEFAULT_KP))
        self._interval: int = int(data.get(CONF_INTERVAL, DEFAULT_INTERVAL))
        self._min_output: float = float(data.get(CONF_MIN_OUTPUT, DEFAULT_MIN_OUTPUT))
        self._unit_ceiling: float = float(data.get(CONF_UNIT_MAX, DEFAULT_UNIT_MAX))
        self._fast_charge_hold: bool = bool(
            data.get(CONF_FAST_CHARGE_HOLD, DEFAULT_FAST_CHARGE_HOLD)
        )
        self._full_charge_minutes: float = float(
            data.get(CONF_FULL_CHARGE_MINUTES, DEFAULT_FULL_CHARGE_MINUTES)
        )
        self._price_sensor: str | None = data.get(CONF_PRICE_SENSOR) or None
        self._cheap_hours: float = float(
            data.get(CONF_CHEAP_HOURS, DEFAULT_CHEAP_HOURS)
        )
        self._charge_below_soc: float = float(
            data.get(CONF_CHARGE_BELOW_SOC, DEFAULT_CHARGE_BELOW_SOC)
        )
        self._solar_forecast_sensor: str | None = (
            data.get(CONF_SOLAR_FORECAST_SENSOR) or None
        )
        self._solar_forecast_max: float = float(
            data.get(CONF_SOLAR_FORECAST_MAX, DEFAULT_SOLAR_FORECAST_MAX)
        )
        self._shadow_simulate: bool = bool(
            data.get(CONF_SHADOW_SIMULATE, DEFAULT_SHADOW_SIMULATE)
        )
        self._battery_power_sensor: str | None = (
            data.get(CONF_BATTERY_POWER_SENSOR) or None
        )
        self._external_timeout: float = float(
            data.get(CONF_EXTERNAL_TIMEOUT, DEFAULT_EXTERNAL_TIMEOUT)
        )

        # runtime state
        self.enabled: bool = False
        self.fast_charge: bool = False
        self.dry_run: bool = DEFAULT_DRY_RUN
        self.suppressed_commands: int = 0
        # a flight recorder, so a download can answer "what happened at 14:32"
        self.tick_log: deque = deque(maxlen=TICK_LOG_SIZE)
        # the last plan handed in from outside, and when
        self.external_setpoint: float | None = None
        self.external_setpoint_at = None
        self._external_issue_active = False
        self.fast_charge_holding: bool = False   # charged, now being kept full
        self.setpoint: float = 0.0          # + = total discharge, - = total charge (W)
        self.status: str = "idle"           # idle | charging | discharging | fast_charge | off | degraded
        self.soc_reserve: float = float(DEFAULT_SOC_RESERVE)
        self.mode: str = DEFAULT_MODE
        self.active_policy: str = POLICY_DISABLED
        self.last_tick = None
        self.unit_status: dict[str, UnitStatus] = {
            u.name: UnitStatus() for u in self._units
        }
        self._unsub = None
        # entities subscribe to this to refresh
        self._listeners: list = []
        # survives restarts and option changes (both of which reload the entry)
        self._store: Store = Store(hass, STORAGE_VERSION, f"{DOMAIN}.{entry.entry_id}")

    @property
    def units(self) -> list[UnitConfig]:
        """The configured units, in wizard order (the per-unit entities' order)."""
        return list(self._units)

    # -- lifecycle -----------------------------------------------------------
    async def async_start(self) -> None:
        await self._async_restore()
        if self.dry_run:
            _LOGGER.warning(
                "Battery Management started in DRY RUN: deciding, not commanding"
            )
        self._unsub = async_track_time_interval(
            self.hass, self._async_tick, self._interval_timedelta()
        )

    # -- persisted state -----------------------------------------------------
    def _state_to_save(self) -> dict:
        return {
            "enabled": self.enabled,
            "setpoint": self.setpoint,
            "soc_reserve": self.soc_reserve,
            "dry_run": self.dry_run,
            "mode": self.mode,
            "saved_at": time.time(),
        }

    @callback
    def _save_state(self) -> None:
        """Debounced: the setpoint moves every tick, disk should not."""
        self._store.async_delay_save(self._state_to_save, SAVE_DELAY)

    async def _async_restore(self) -> None:
        """Pick up where we left off after a restart or an options reload.

        Deliberately does NOT restore `fast_charge`: that is a manual emergency
        action, and silently resuming a full-power grid charge after an outage
        could be an expensive surprise. The on/off state is restored, because
        the alternative - a site that quietly stops coordinating until someone
        notices - is worse, and because a crashed HA leaves the packs holding
        their last command (there is no watchdog), so taking control back is
        the safer outcome.
        """
        try:
            stored = await self._store.async_load()
        except Exception:  # noqa: BLE001  -- never block setup on the store
            _LOGGER.exception("Could not read stored coordinator state")
            return
        if stored and stored.get("dry_run") is not None:
            self.dry_run = bool(stored["dry_run"])
        if stored and stored.get("mode") in self.available_modes:
            self.mode = stored["mode"]
        if stored and stored.get("soc_reserve") is not None:
            # a user setting, not runtime state: restore it even when the
            # coordinator was switched off, and regardless of age
            self.soc_reserve = float(stored["soc_reserve"])
        if not stored or not stored.get("enabled"):
            return

        self.enabled = True
        age = time.time() - float(stored.get("saved_at") or 0)
        if 0 <= age <= MAX_SETPOINT_AGE:
            self.setpoint = float(stored.get("setpoint") or 0.0)
            _LOGGER.debug(
                "restored: enabled, setpoint %.0f W (%.0f s old)", self.setpoint, age
            )
        else:
            self.setpoint = 0.0
            _LOGGER.debug(
                "restored: enabled, setpoint reset to 0 (stored value %.0f s old)", age
            )

        self.status = "idle"
        await self._take_control()
        self._notify()

    async def async_stop(self, revert: bool = True) -> None:
        if self._unsub:
            self._unsub()
            self._unsub = None
        if revert:
            await self._revert_all_to_self()

    def _interval_timedelta(self):
        from datetime import timedelta

        return timedelta(seconds=max(5, self._interval))

    @callback
    def async_add_listener(self, cb) -> None:
        self._listeners.append(cb)

    @callback
    def _notify(self) -> None:
        for cb in self._listeners:
            cb()

    # -- switch handlers -----------------------------------------------------
    async def async_set_enabled(self, value: bool) -> None:
        self.enabled = value
        if value:
            self.setpoint = 0.0
            await self._take_control()
        else:
            self.fast_charge = False
            self.setpoint = 0.0
            await self._revert_all_to_self()
        self.status = "off" if not value else "idle"
        # a deliberate flip is written straight through, not debounced, so a
        # restart seconds later still honours it
        await self._store.async_save(self._state_to_save())
        self._notify()
        # kick an immediate tick when turning on
        if value:
            await self._async_tick(dt_util.utcnow())

    async def async_set_setpoint(self, value: float) -> None:
        """Force the integrator state (the `set_setpoint` service).

        No clamping here: the next tick bounds it by what the packs can actually
        deliver, exactly as it bounds its own integration.
        """
        self.setpoint = float(value)
        # also the intake for MODE_EXTERNAL: remember who said what, and when
        self.external_setpoint = float(value)
        self.external_setpoint_at = dt_util.utcnow()
        await self._store.async_save(self._state_to_save())
        self._notify()
        if self.enabled or self.fast_charge:
            await self._async_tick(dt_util.utcnow())

    async def async_set_fast_charge(self, value: bool) -> None:
        self.fast_charge = value
        self.fast_charge_holding = False
        if value:
            await self._take_control()
            await self._async_tick(dt_util.utcnow())
        else:
            self.setpoint = 0.0
            if not self.enabled:
                await self._revert_all_to_self()
        self._notify()

    # -- helpers -------------------------------------------------------------
    def _read_float(self, entity_id: str | None, default: float | None = None):
        if not entity_id:
            return default
        st = self.hass.states.get(entity_id)
        if st is None or st.state in UNAVAILABLE_STATES:
            return default
        try:
            return float(st.state)
        except (ValueError, TypeError):
            return default

    def _unit_snapshot(self, cfg: UnitConfig) -> UnitState:
        soc = self._read_float(cfg.soc_sensor)
        online = soc is not None
        clim = self._read_float(cfg.charge_limit, FALLBACK_CHARGE_LIMIT)
        dlim = self._read_float(cfg.discharge_limit, FALLBACK_DISCHARGE_LIMIT)
        # per-unit hard ceiling: min(entity max attribute, configured ceiling)
        umax = self._unit_ceiling
        st = self.hass.states.get(cfg.target_number)
        if st is not None and (attr_max := st.attributes.get("max")) is not None:
            try:
                umax = min(float(attr_max), self._unit_ceiling)
            except (ValueError, TypeError):
                pass
        return UnitState(cfg, online, soc or 0.0, clim, dlim, umax)

    async def _svc_select(self, entity_id: str, option: str) -> None:
        if self.dry_run:
            self.suppressed_commands += 1
            return
        await self.hass.services.async_call(
            "select", "select_option",
            {"entity_id": entity_id, "option": option}, blocking=False,
        )

    async def _svc_number(self, entity_id: str, value: float) -> None:
        if self.dry_run:
            self.suppressed_commands += 1
            return
        await self.hass.services.async_call(
            "number", "set_value",
            {"entity_id": entity_id, "value": int(round(value))}, blocking=False,
        )

    def _discharge_floor(self, unit: UnitState) -> float:
        """How empty this unit may get: its own limit, raised by the reserve.

        Expressing the reserve this way rather than as a separate clamp means
        the SoC weighting tapers towards it - a pack near the reserve is already
        taking a smaller share - instead of running full tilt and then stopping
        dead the moment it crosses the line.
        """
        return max(unit.discharge_limit, self.soc_reserve)

    def _classify(
        self, error: float, sp: float, online: dict, maxdis: float, maxchg: float
    ) -> str:
        """Which rule is limiting us this tick, for the active-policy sensor."""
        wants_discharge = error >= self._deadband
        wants_charge = error <= -self._deadband

        if self.mode == MODE_PAUSE:
            return POLICY_MODE_PAUSE
        # the mode is the user's own choice, so name it before a capacity limit
        if wants_discharge and self.mode == MODE_CHARGE_ONLY:
            return POLICY_MODE_CHARGE_ONLY
        if wants_charge and self.mode == MODE_DISCHARGE_ONLY:
            return POLICY_MODE_DISCHARGE_ONLY
        if wants_discharge and maxdis <= 0:
            # would the packs have had anything to give without the reserve?
            free = any(s.soc > s.discharge_limit for s in online.values())
            return POLICY_SOC_RESERVE if free else POLICY_PACKS_EMPTY
        if wants_charge and maxchg <= 0:
            return POLICY_PACKS_FULL
        if not wants_discharge and not wants_charge and sp == 0:
            return POLICY_DEADBAND
        return POLICY_GRID_ZERO

    # -- dynamic tariff ------------------------------------------------------
    @property
    def available_modes(self) -> list[str]:
        """Dynamic is only offered once a price sensor is configured."""
        return [*MODES, MODE_DYNAMIC] if self._price_sensor else list(MODES)

    def _price_forecast(self):
        """Upcoming price slots, or None when the sensor cannot be read."""
        if not self._price_sensor:
            return None
        state = self.hass.states.get(self._price_sensor)
        if state is None or state.state in UNAVAILABLE_STATES:
            return None
        slots = parse_forecast(dict(state.attributes), dt_util.utcnow())
        return slots or None

    def _sun_is_enough(self) -> bool:
        """True when enough sun is expected that buying would be wasteful."""
        if not self._solar_forecast_sensor or self._solar_forecast_max <= 0:
            return False
        expected = self._read_float(self._solar_forecast_sensor)
        if expected is None:
            return False  # no forecast is not a reason to skip a cheap hour
        return expected >= self._solar_forecast_max

    def _dynamic_should_charge(self, online: dict) -> tuple[bool, str | None]:
        """Buy from the grid right now? Returns (yes/no, policy when it matters).

        Three conditions, all of which must hold: the current slot is among the
        cheapest ahead, the packs are empty enough to be worth filling, and not
        so much sun is expected that we would be paying for what is coming free.
        """
        slots = self._price_forecast()
        if slots is None:
            return False, POLICY_DYNAMIC_NO_PRICES
        if not is_cheap_now(
            slots, dt_util.utcnow(), self._cheap_hours, PRICE_WINDOW_HOURS
        ):
            return False, None
        if self._sun_is_enough():
            return False, None
        # only the packs that are actually low; a full one is not a reason to buy
        if not any(
            s.soc < min(self._charge_below_soc, s.charge_limit) for s in online.values()
        ):
            return False, None
        return True, POLICY_DYNAMIC_CHARGE

    def minutes_to_full(self) -> int | None:
        """How long a fast charge would take from right now, in minutes.

        None when the empty-to-full time has not been measured yet, or when no
        unit is reachable. Guessing a duration would be worse than admitting we
        cannot say: the whole point is to arrive full at a particular moment.

        The packs charge in parallel, so the answer is the slowest one, not the
        sum. Snapshots fresh rather than reusing the last tick, so the estimate
        is available while the coordinator is switched off too.
        """
        if self._full_charge_minutes <= 0:
            return None

        longest: float | None = None
        for cfg in self._units:
            unit = self._unit_snapshot(cfg)
            if not unit.online:
                continue
            missing = max(unit.charge_limit - unit.soc, 0.0)
            longest = max(longest or 0.0, missing / 100.0 * self._full_charge_minutes)
        if longest is None:
            return None
        return int(round(longest))

    async def async_set_dry_run(self, value: bool) -> None:
        """Switch between watching and acting.

        Going live is the dangerous direction, so it is logged at warning level:
        a month-old shadow install that quietly starts commanding packs is
        exactly the surprise this whole mode exists to avoid.
        """
        self.dry_run = value
        if value:
            _LOGGER.warning(
                "Battery Management is in DRY RUN: it will decide but command "
                "nothing"
            )
        else:
            _LOGGER.warning(
                "Battery Management is now LIVE and will command the packs "
                "(%d commands were suppressed while in dry run)",
                self.suppressed_commands,
            )
        await self._store.async_save(self._state_to_save())
        self._notify()
        if self.enabled:
            await self._async_tick(dt_util.utcnow())

    async def async_set_mode(self, mode: str) -> None:
        """Switch strategy. Grid-zero still runs underneath every mode."""
        if mode not in self.available_modes:
            raise ValueError(f"unknown mode: {mode}")
        self.mode = mode
        # do not wait for a tick to say what is missing, and do not leave the
        # warning up once the mode has moved on
        self._sync_external_issue(
            mode == MODE_EXTERNAL and self._external_target()[0] is None
        )
        await self._store.async_save(self._state_to_save())
        self._notify()
        if self.enabled:
            await self._async_tick(dt_util.utcnow())

    async def async_set_soc_reserve(self, value: float) -> None:
        """Set the reserve floor (%). Applies in every mode."""
        self.soc_reserve = max(0.0, min(100.0, float(value)))
        await self._store.async_save(self._state_to_save())
        self._notify()
        if self.enabled:
            await self._async_tick(dt_util.utcnow())

    # -- shadow simulation ---------------------------------------------------
    def _other_controller_power(self) -> float | None:
        """What the packs are doing right now, signed (+ = discharging).

        Prefers a measured sensor when one is configured. Otherwise reads back
        the target and flow entities we would have written to - whoever is in
        charge writes there, so their command is visible without any extra
        configuration. It is a command rather than a measurement, so it is only
        as accurate as the packs are obedient; a measured sensor is better where
        one exists.
        """
        if self._battery_power_sensor:
            return self._read_float(self._battery_power_sensor)

        total = 0.0
        seen = False
        for cfg in self._units:
            watts = self._read_float(cfg.target_number)
            if watts is None:
                continue
            state = self.hass.states.get(cfg.flow_select)
            flow = state.state if state is not None else None
            if flow not in (FLOW_CHARGE, FLOW_DISCHARGE):
                continue
            total += watts if flow == FLOW_DISCHARGE else -watts
            seen = True
        return total if seen else None

    def _shadow_grid(self, real_grid: float) -> tuple[float, float | None]:
        """The meter as it would read if *we* were in charge.

        Returns (grid to regulate against, the other controller's power). Falls
        back to the real meter when the other controller cannot be observed -
        a wrong simulation would be worse than an honest one that admits the
        loop is open.
        """
        other = self._other_controller_power()
        if other is None:
            return real_grid, None
        net_demand = real_grid + other
        return net_demand - self.setpoint, other

    @callback
    def _sync_external_issue(self, waiting: bool) -> None:
        """Tell the user *what to install*, not just that nothing is arriving.

        "External plan went quiet" is accurate and useless on its own: the mode
        needs an optimiser feeding `set_setpoint`, and nobody guesses that from
        a sensor state. A repair issue puts it where Home Assistant already
        collects things that need attention, and clears itself the moment a
        plan arrives.
        """
        if waiting == self._external_issue_active:
            return  # only on a transition: no registry churn four times a minute
        self._external_issue_active = waiting

        issue_id = f"{self.entry.entry_id}_external_no_plan"
        if waiting:
            ir.async_create_issue(
                self.hass,
                DOMAIN,
                issue_id,
                is_fixable=False,
                severity=ir.IssueSeverity.WARNING,
                translation_key="external_no_plan",
                translation_placeholders={
                    "timeout": str(int(self._external_timeout)),
                },
                learn_more_url=(
                    "https://github.com/Mischa323/ha-battery-management"
                    "#external-plan-emhass"
                ),
            )
        else:
            ir.async_delete_issue(self.hass, DOMAIN, issue_id)

    def external_plan_age(self) -> float | None:
        """Seconds since the last external setpoint, or None if never."""
        if self.external_setpoint_at is None:
            return None
        return (dt_util.utcnow() - self.external_setpoint_at).total_seconds()

    def _external_target(self) -> tuple[float | None, str]:
        """The external plan's setpoint, if it is still fresh enough to trust.

        A plan that stops arriving must hand control back rather than freeze the
        packs on its last instruction. The packs have no watchdog of their own,
        so this is it.
        """
        age = self.external_plan_age()
        if self.external_setpoint is None or age is None:
            return None, POLICY_EXTERNAL_STALE
        if age > self._external_timeout * 60:
            return None, POLICY_EXTERNAL_STALE
        return self.external_setpoint, POLICY_EXTERNAL

    def diagnostics(self) -> dict:
        """Everything needed to explain this coordinator's behaviour."""
        return {
            "settings": {
                "grid_sensor": self._grid_sensor,
                "bias_w": self._bias,
                "deadband_w": self._deadband,
                "kp": self._kp,
                "interval_s": self._interval,
                "min_output_w": self._min_output,
                "unit_max_w": self._unit_ceiling,
                "fast_charge_hold": self._fast_charge_hold,
                "full_charge_minutes": self._full_charge_minutes,
                "price_sensor": self._price_sensor,
                "cheap_hours": self._cheap_hours,
                "charge_below_soc": self._charge_below_soc,
                "solar_forecast_sensor": self._solar_forecast_sensor,
                "solar_forecast_max": self._solar_forecast_max,
            },
            "state": {
                # dry run first: every other number means something different
                # depending on whether any of it actually reached the packs
                "dry_run": self.dry_run,
                "suppressed_commands": self.suppressed_commands,
                "enabled": self.enabled,
                "mode": self.mode,
                "available_modes": self.available_modes,
                "fast_charge": self.fast_charge,
                "fast_charge_holding": self.fast_charge_holding,
                "setpoint_w": round(self.setpoint),
                "status": self.status,
                "active_policy": self.active_policy,
                "soc_reserve": self.soc_reserve,
                "minutes_to_full": self.minutes_to_full(),
                "external_setpoint_w": self.external_setpoint,
                "external_plan_age_s": self.external_plan_age(),
                "external_timeout_min": self._external_timeout,
                "last_tick": self.last_tick.isoformat() if self.last_tick else None,
            },
            "units": [
                {
                    "name": cfg.name,
                    "entities": {
                        "mode_select": cfg.mode_select,
                        "flow_select": cfg.flow_select,
                        "target_number": cfg.target_number,
                        "soc_sensor": cfg.soc_sensor,
                        "charge_limit": cfg.charge_limit,
                        "discharge_limit": cfg.discharge_limit,
                    },
                    "status": {
                        "online": self.unit_status[cfg.name].online,
                        "soc": self.unit_status[cfg.name].soc,
                        "target_w": self.unit_status[cfg.name].target,
                        "flow": self.unit_status[cfg.name].flow,
                    },
                }
                for cfg in self._units
            ],
            "recent_ticks": list(self.tick_log),
        }

    def _log_tick(
        self, grid, error, sp, flow, alloc, online, observed_grid=None, other_power=None
    ) -> None:
        """One row per tick for the diagnostics download.

        Recorded in dry run too - that is the entire point of dry run.
        """
        self.tick_log.append(
            {
                "at": dt_util.utcnow().isoformat(),
                "grid_w": round(grid),
                # what the meter really said, and what the other controller was
                # doing - both needed to check the reconstruction afterwards
                "observed_grid_w": (
                    round(observed_grid) if observed_grid is not None else None
                ),
                "other_controller_w": (
                    round(other_power) if other_power is not None else None
                ),
                "error_w": round(error),
                "setpoint_w": round(sp),
                "mode": self.mode,
                "policy": self.active_policy,
                "flow": flow,
                "dry_run": self.dry_run,
                "units": {
                    name: {
                        "target_w": alloc.get(name, 0),
                        "soc": online[name].soc,
                    }
                    for name in online
                },
            }
        )

    def _refresh_observations(self, snaps: dict | None = None) -> None:
        """Update what we can see, without touching what we last commanded.

        Reachability is an observation and must stay current even when the
        coordinator is switched off. The last commanded target is deliberately
        left alone: the pack is still executing it (gotcha 1).
        """
        if snaps is None:
            snaps = {u.name: self._unit_snapshot(u) for u in self._units}
        for name, snap in snaps.items():
            self.unit_status[name].online = snap.online
            self.unit_status[name].soc = snap.soc if snap.online else None

    def _record(self, name: str, flow: str, watts: int) -> None:
        """Remember what we just commanded, signed like the Setpoint sensor."""
        status = self.unit_status[name]
        status.flow = flow
        status.target = watts if flow == FLOW_DISCHARGE else -watts

    async def _take_control(self) -> None:
        """Put every unit into whatever option means 'driven from outside'."""
        for u in self._units:
            await self._svc_select(u.mode_select, u.mode_control)

    async def _revert_all_to_self(self) -> None:
        """Let go of every unit, as far as each one can be let go of.

        Zero first, always: a unit whose mode cannot be changed - no meter of
        its own, so no self-consumption mode to return to - is left holding a
        command of 0 W, which is a safe resting state. Skipping the zero and
        only switching modes would be the dangerous order.
        """
        for u in self._units:
            await self._svc_number(u.target_number, 0)
            if u.mode_safe:
                await self._svc_select(u.mode_select, u.mode_safe)
            status = self.unit_status[u.name]
            status.target = 0
            status.flow = None

    # -- distribution --------------------------------------------------------
    @staticmethod
    def _distribute(mag: float, weights: dict, umax: dict, min_output: float) -> dict:
        """Split `mag` W across units by weight, clamped to max, with min-output flooring.

        Shares below `min_output` are not viable - a pack that small just idles -
        so the lowest-weight unit is dropped and `mag` re-split over the rest,
        repeatedly, until every remaining share clears the floor or one unit is
        left holding all of it.

        Dropping the *lowest* weight is what makes this stable. The earlier
        version handed the leftover to "the other unit", so as the setpoint
        ramped through the sub-minimum band the whole load ping-ponged between
        packs every tick - the micro-cycling this floor exists to prevent. Now
        units join in weight order and never swap: the fullest pack (discharge)
        or the emptiest (charge) is always the last one standing.
        """
        active = [u for u in weights if weights[u] > 0]
        if mag <= 0 or not active:
            return {u: 0 for u in weights}

        while True:
            total = sum(weights[u] for u in active)
            shares = {u: min(mag * weights[u] / total, umax[u]) for u in active}
            if len(active) == 1:
                break
            below = [u for u in active if shares[u] < min_output]
            if not below:
                break
            # tie-break on the key so the outcome never depends on dict order
            active.remove(min(below, key=lambda u: (weights[u], str(u))))

        return {u: int(round(shares.get(u, 0.0))) for u in weights}

    # -- the control tick ----------------------------------------------------
    async def _async_tick(self, _now) -> None:
        if not self.enabled and not self.fast_charge:
            self.active_policy = POLICY_DISABLED
            # keep looking even while idle: "disconnected" has to mean the pack
            # cannot be reached, not merely that nobody asked
            self._refresh_observations()
            self._notify()
            return
        try:
            snaps = {u.name: self._unit_snapshot(u) for u in self._units}
            cfg_by_name = {u.name: u for u in self._units}

            self._refresh_observations(snaps)

            # ---- FAST CHARGE override --------------------------------------
            if self.fast_charge:
                self.status = "fast_charge"
                self.active_policy = POLICY_FAST_CHARGE
                all_full = True
                for name, s in snaps.items():
                    cfg = cfg_by_name[name]
                    if not s.online:
                        continue
                    if s.soc < s.charge_limit - 1:
                        all_full = False
                        await self._svc_select(cfg.flow_select, FLOW_CHARGE)
                        await self._svc_number(cfg.target_number, s.unit_max)
                        self._record(name, FLOW_CHARGE, int(s.unit_max))
                    else:
                        await self._svc_number(cfg.target_number, 0)
                        self._record(name, FLOW_CHARGE, 0)
                _LOGGER.debug(
                    "fast charge | %s",
                    ", ".join(
                        f"{n}={self.unit_status[n].target} W" for n in snaps
                    ),
                )
                if all_full and self._fast_charge_hold:
                    # Stay on and keep them there. Switching off here would hand
                    # control back to the mode, which discharges the packs again
                    # - exactly what you did not want when you pressed this
                    # before a storm. Releasing is the user's call.
                    if not self.fast_charge_holding:
                        _LOGGER.debug("fast charge: full, holding until released")
                    self.fast_charge_holding = True
                    self.status = "hold"
                    self.active_policy = POLICY_FAST_CHARGE_HOLD
                elif all_full:
                    _LOGGER.debug("fast charge: all units full, switching off")
                    await self.async_set_fast_charge(False)
                else:
                    self.fast_charge_holding = False
                self.last_tick = dt_util.utcnow()
                self._notify()
                return

            # ---- NORMAL grid-zero control ----------------------------------
            grid = self._read_float(self._grid_sensor)
            if grid is None:
                _LOGGER.debug(
                    "grid sensor %s unreadable; holding setpoint at %.0f W",
                    self._grid_sensor,
                    self.setpoint,
                )
                self.status = "degraded"
                self.active_policy = POLICY_NO_GRID_DATA
                self._notify()
                return

            # in dry run the meter reflects whoever *is* in charge, so close
            # the loop on reconstructed data instead of pretending it is ours
            observed_grid, other_power = grid, None
            if self.dry_run and self._shadow_simulate:
                grid, other_power = self._shadow_grid(grid)

            error = grid - self._bias
            online = {n: s for n, s in snaps.items() if s.online}

            maxdis = sum(
                s.unit_max
                for s in online.values()
                if s.soc > self._discharge_floor(s)
            )
            maxchg = sum(s.unit_max for s in online.values() if s.soc < s.charge_limit)

            # The mode is a bound on the setpoint, not a separate behaviour:
            # grid-zero keeps regulating inside it. Reusing the existing clamp
            # means the anti-windup guard covers mode limits too - the
            # integrator can never build up against a bound it cannot cross.
            upper, lower = maxdis, -maxchg
            if self.mode == MODE_CHARGE_ONLY:
                upper = 0.0
            elif self.mode == MODE_DISCHARGE_ONLY:
                lower = 0.0
            elif self.mode == MODE_PAUSE:
                upper = lower = 0.0

            # Buying from the grid is the one thing that cannot be expressed as
            # a bound: there is no surplus to regulate against, so this forces a
            # value. Everything else stays a bound on grid-zero.
            dynamic_charge, dynamic_policy = (False, None)
            if self.mode == MODE_DYNAMIC:
                dynamic_charge, dynamic_policy = self._dynamic_should_charge(online)

            external_sp, external_policy = (None, None)
            if self.mode == MODE_EXTERNAL:
                external_sp, external_policy = self._external_target()
            self._sync_external_issue(
                self.mode == MODE_EXTERNAL and external_sp is None
            )

            if dynamic_charge:
                sp = -maxchg
            elif external_sp is not None:
                # the plan proposes; the clamp below still disposes
                sp = external_sp
            elif abs(error) < self._deadband:
                sp = self.setpoint
            else:
                sp = self.setpoint + self._kp * error
            sp = max(min(sp, upper), lower)
            self.setpoint = sp

            umax = {n: s.unit_max for n, s in online.items()}

            self.active_policy = dynamic_policy or external_policy or self._classify(
                error, sp, online, maxdis, maxchg
            )

            if sp > 0:  # discharge
                flow = FLOW_DISCHARGE
                weights = {
                    n: max(s.soc - self._discharge_floor(s), 0.0)
                    for n, s in online.items()
                    if s.soc > self._discharge_floor(s)
                }
                alloc = self._distribute(sp, weights, umax, self._min_output)
                self.status = "discharging"
            elif sp < 0:  # charge
                flow = FLOW_CHARGE
                weights = {
                    n: max(s.charge_limit - s.soc, 0.0)
                    for n, s in online.items() if s.soc < s.charge_limit
                }
                alloc = self._distribute(-sp, weights, umax, self._min_output)
                self.status = "charging"
            else:
                flow = FLOW_CHARGE
                alloc = {n: 0 for n in online}
                self.status = "idle"

            for name, s in online.items():
                cfg = cfg_by_name[name]
                await self._svc_select(cfg.flow_select, flow)
                await self._svc_number(cfg.target_number, alloc.get(name, 0))
                self._record(name, flow, alloc.get(name, 0))

            _LOGGER.debug(
                "grid=%.0f W bias=%.0f -> error=%.0f W | setpoint=%.0f W (%s) | %s | %s",
                grid,
                self._bias,
                error,
                sp,
                self.status,
                flow,
                ", ".join(f"{n}={alloc.get(n, 0)} W" for n in online),
            )

            self.last_tick = dt_util.utcnow()
            self._log_tick(
                grid, error, sp, flow, alloc, online, observed_grid, other_power
            )
            self._save_state()
            self._notify()
        except Exception:  # noqa: BLE001  -- never let the loop die silently
            _LOGGER.exception("Battery Management control tick failed")
            self.status = "degraded"
            self._notify()

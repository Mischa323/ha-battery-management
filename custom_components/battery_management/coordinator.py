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

import asyncio
import logging
import time
from collections import deque
from dataclasses import dataclass

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .phases import (
    attribute_phase,
    effective_limit_w,
    other_load,
    room,
    unit_ceilings,
)
from .suppliers import FETCHERS, SOURCE_ENTITY, SOURCE_NONE
from .trace import Trace
from .prices import (
    cheapest_slots,
    dearest_slots,
    is_cheap_now,
    is_dear_now,
    parse_forecast,
    slot_at,
    to_hourly,
    slots_in_window,
)
from .const import (
    CONF_BIAS,
    CONF_CHARGE_LIMIT,
    CONF_DEADBAND,
    CONF_DISCHARGE_LIMIT,
    CONF_DISCHARGE_RECOVERY,
    CONF_FLOW_SELECT,
    CONF_GRID_POWER,
    CONF_INTERVAL,
    CONF_KP,
    CONF_KP_RETURN,
    CONF_MIN_OUTPUT,
    CONF_MODE_CONTROL,
    CONF_MODE_SAFE,
    CONF_MODE_SELECT,
    CONF_PHASE_DETECT,
    CONF_PHASE_LIMIT_AMPS,
    CONF_PHASE_MARGIN,
    CONF_PHASE_PROBE_SECONDS,
    CONF_PHASE_REDETECT,
    CONF_PHASE_SENSORS,
    CONF_PHASE_VOLTAGE,
    CONF_SOC_SENSOR,
    CONF_UNIT_POWER_SENSOR,
    CONF_UNIT_PHASE,
    CONF_TARGET_NUMBER,
    CONF_TRACE,
    CONF_TRACE_DAYS,
    CONF_FAST_CHARGE_HOLD,
    CONF_BATTERY_POWER_SENSOR,
    CONF_CHARGE_BELOW_SOC,
    CONF_CHEAP_HOURS,
    CONF_DISCHARGE_ANYWAY_SOC,
    CONF_EXPENSIVE_HOURS,
    CONF_EXTERNAL_TIMEOUT,
    CONF_FULL_CHARGE_MINUTES,
    CONF_PRICE_SENSOR,
    CONF_PRICE_RESOLUTION,
    CONF_PRICE_SOURCE,
    CONF_SOLAR_FORECAST_MAX,
    CONF_SHADOW_SIMULATE,
    CONF_SOLAR_FORECAST_SENSOR,
    CONF_SOLAR_FORECAST_SENSORS,
    CONF_SOLAR_PRODUCED_SENSOR,
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
    DEFAULT_BUY_CEILING_MAX,
    DEFAULT_BUY_CEILING_MIN,
    DEFAULT_DISCHARGE_ANYWAY_SOC,
    DEFAULT_DISCHARGE_RECOVERY,
    DEFAULT_EXPENSIVE_HOURS,
    DEFAULT_FULL_CHARGE_MINUTES,
    DEFAULT_INTERVAL,
    DEFAULT_KP,
    DEFAULT_MIN_OUTPUT,
    DEFAULT_TRACE,
    DEFAULT_TRACE_DAYS,
    TRACE_DIR,
    KP_RETURN_FACTOR,
    DEFAULT_MODE,
    DEFAULT_PHASE_DETECT,
    DEFAULT_PHASE_LIMIT_AMPS,
    DEFAULT_PHASE_MARGIN,
    DEFAULT_PHASE_PROBE_SECONDS,
    DEFAULT_PHASE_REDETECT,
    DEFAULT_PHASE_VOLTAGE,
    DEFAULT_PRICE_RESOLUTION,
    DEFAULT_SHADOW_SIMULATE,
    DEFAULT_SOC_RESERVE,
    DEFAULT_SOLAR_FORECAST_MAX,
    PRICE_REFRESH_MINUTES,
    RESOLUTION_HOURLY,
    PRICE_TIMEOUT,
    PRICE_WINDOW_HOURS,
    DEFAULT_UNIT_MAX,
    DOMAIN,
    FALLBACK_CHARGE_LIMIT,
    FALLBACK_DISCHARGE_LIMIT,
    FLOW_CHARGE,
    FLOW_DISCHARGE,
    MAX_PRICE_AGE,
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
    POLICY_DYNAMIC_HOLD,
    POLICY_SOLAR_HEADROOM,
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
    POLICY_RECOVERING,
    POLICY_PHASE_DETECT,
    POLICY_PHASE_LIMIT,
    POLICY_SOC_RESERVE,
    PHASE_DETECT_BLOCKED,
    PHASE_DETECT_GAVE_UP,
    PHASE_DETECT_DONE,
    PHASE_DETECT_INCONCLUSIVE,
    PHASE_DETECT_MANUAL,
    PHASE_DETECT_OFF,
    PHASE_DETECT_PARTIAL,
    PHASE_DETECT_RUNNING,
    PHASE_DETECT_UNKNOWN,
    PHASE_MAX_ATTEMPTS,
    PHASE_PROBE_MIN_WATTS,
    PHASE_PROBE_SAMPLE,
    PHASE_RETRY_SECONDS,
    PHASE_SETTLE_MAX,
    PHASE_SETTLE_SECONDS,
    PHASE_SETTLE_STEP,
    PHASE_SETTLE_TOLERANCE,
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
    #: the pack's own power reading; observation only, never control
    power_sensor: str | None = None
    mode_control: str = DEVICE_MODE_THIRD_PARTY
    #: False when this entry predates the mode step, so both values below are
    #: defaults rather than choices - which is exactly when they are wrong
    modes_configured: bool = False
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
            power_sensor=raw.get(CONF_UNIT_POWER_SENSOR),
            modes_configured=CONF_MODE_CONTROL in raw,
            mode_control=raw.get(CONF_MODE_CONTROL) or DEVICE_MODE_THIRD_PARTY,
            # An absent hand-back means two opposite things, and getting them
            # confused disarms the safety net on exactly the unit that needs it.
            # If the wizard stored a control mode, this entry went through the
            # mode step, so absent means the user deliberately left it empty -
            # what a pack with no self-consumption mode requires. Only an entry
            # predating the step gets the old fixed default.
            mode_safe=(
                raw.get(CONF_MODE_SAFE) or None
                if CONF_MODE_CONTROL in raw
                else raw.get(CONF_MODE_SAFE, DEVICE_MODE_SELF) or None
            ),
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
        configured_return = data.get(CONF_KP_RETURN)
        self._kp_return: float = (
            float(configured_return)
            if configured_return is not None
            else self._kp * KP_RETURN_FACTOR
        )
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
        self._price_resolution: str = (
            data.get(CONF_PRICE_RESOLUTION) or DEFAULT_PRICE_RESOLUTION
        )
        # entries made before suppliers could be asked directly have a sensor
        # and no source, which is exactly what SOURCE_ENTITY means
        self._price_source: str = data.get(CONF_PRICE_SOURCE) or (
            SOURCE_ENTITY if self._price_sensor else SOURCE_NONE
        )
        self._cheap_hours: float = float(
            data.get(CONF_CHEAP_HOURS, DEFAULT_CHEAP_HOURS)
        )
        self._charge_below_soc: float = float(
            data.get(CONF_CHARGE_BELOW_SOC, DEFAULT_CHARGE_BELOW_SOC)
        )
        forecast = data.get(CONF_SOLAR_FORECAST_SENSORS)
        if not forecast:
            # entries made before several planes could be picked
            single = data.get(CONF_SOLAR_FORECAST_SENSOR)
            forecast = [single] if single else []
        self._solar_forecast_sensors: list[str] = list(forecast)
        self._solar_produced_sensor: str | None = (
            data.get(CONF_SOLAR_PRODUCED_SENSOR) or None
        )
        self._expensive_hours: float = float(
            data.get(CONF_EXPENSIVE_HOURS, DEFAULT_EXPENSIVE_HOURS)
        )
        self._discharge_anyway_soc: float = float(
            data.get(CONF_DISCHARGE_ANYWAY_SOC, DEFAULT_DISCHARGE_ANYWAY_SOC)
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
        self._discharge_recovery: float = float(
            data.get(CONF_DISCHARGE_RECOVERY, DEFAULT_DISCHARGE_RECOVERY)
        )
        self._external_timeout: float = float(
            data.get(CONF_EXTERNAL_TIMEOUT, DEFAULT_EXTERNAL_TIMEOUT)
        )

        # --- per-phase fuse protection (entirely opt-in) ---------------------
        self._phase_sensors: list[str] = list(data.get(CONF_PHASE_SENSORS) or [])
        self._phase_amps: float = float(
            data.get(CONF_PHASE_LIMIT_AMPS, DEFAULT_PHASE_LIMIT_AMPS)
        )
        self._phase_volts: float = float(
            data.get(CONF_PHASE_VOLTAGE, DEFAULT_PHASE_VOLTAGE)
        )
        self._phase_margin: float = float(
            data.get(CONF_PHASE_MARGIN, DEFAULT_PHASE_MARGIN)
        )
        self._phase_detect: bool = bool(
            data.get(CONF_PHASE_DETECT, DEFAULT_PHASE_DETECT)
        )
        self._phase_redetect: bool = bool(
            data.get(CONF_PHASE_REDETECT, DEFAULT_PHASE_REDETECT)
        )
        self._probe_seconds: float = float(
            data.get(CONF_PHASE_PROBE_SECONDS, DEFAULT_PHASE_PROBE_SECONDS)
        )
        # Typed in per unit, and it wins over anything we work out ourselves:
        # somebody who has read the meter cupboard knows better than a probe.
        self._phase_manual: dict[str, int | None] = {
            u[CONF_UNIT_NAME]: (int(u.get(CONF_UNIT_PHASE) or 0) or None)
            for u in data[CONF_UNITS]
        }

        # runtime state
        self.enabled: bool = False
        self.fast_charge: bool = False
        self.dry_run: bool = DEFAULT_DRY_RUN
        self.suppressed_commands: int = 0
        # a flight recorder, so a download can answer "what happened at 14:32"
        self.tick_log: deque = deque(maxlen=TICK_LOG_SIZE)
        # A file, because every question so far has been about a day that had
        # already scrolled out of the ring above.
        # when each command went out, and how long the pack took to show it
        self._command_sent: dict[str, tuple[int, object]] = {}
        self._command_ack: dict[str, float] = {}
        self._trace: Trace | None = None
        if data.get(CONF_TRACE, DEFAULT_TRACE):
            self._trace = Trace(
                hass.config.path(TRACE_DIR),
                int(data.get(CONF_TRACE_DAYS, DEFAULT_TRACE_DAYS)),
            )
        # the last plan handed in from outside, and when
        self.external_setpoint: float | None = None
        self.external_setpoint_at = None
        self._external_issue_active = False
        self._hand_back_issues: dict[str, bool] = {}
        self.fast_charge_holding: bool = False   # charged, now being kept full
        self.setpoint: float = 0.0          # + = total discharge, - = total charge (W)
        self.status: str = "idle"           # idle | charging | discharging | fast_charge | off | degraded
        self.soc_reserve: float = float(DEFAULT_SOC_RESERVE)
        self.buy_ceiling_min: float = float(DEFAULT_BUY_CEILING_MIN)
        self.buy_ceiling_max: float = float(DEFAULT_BUY_CEILING_MAX)
        self.mode: str = DEFAULT_MODE
        self.active_policy: str = POLICY_DISABLED
        self.last_tick = None
        # what it read, what it regulated against, and what the other
        # controller was doing - the three numbers a shadow run is checked with
        self.last_grid_observed: float | None = None
        self.last_grid_used: float | None = None
        self.last_other_power: float | None = None
        self.unit_status: dict[str, UnitStatus] = {
            u.name: UnitStatus() for u in self._units
        }
        # packs that hit their floor and have not climbed back far enough yet
        self.recovering: dict[str, bool] = {u.name: False for u in self._units}
        # which leg each unit sits on (1-based), and how we came to believe it
        self.unit_phase: dict[str, int | None] = {u.name: None for u in self._units}
        self.phase_detection: str = PHASE_DETECT_UNKNOWN
        self.phase_detected_at: float | None = None
        self.phase_probe_detail: dict[str, dict] = {}
        # how often each unit has been asked, and when last - without this a
        # pack that never answers is re-probed every tick, forever
        self.phase_attempts: dict[str, int] = {u.name: 0 for u in self._units}
        self._phase_last_try: dict[str, float] = {}
        self._detecting: bool = False
        self._detect_task = None
        # a unit that dropped out may have come back on different wiring, so its
        # placement is retired the moment it goes offline
        self._was_online: dict[str, bool] = {u.name: False for u in self._units}
        self._unsub = None
        self._unsub_prices = None
        # what a supplier last told us, in the shape parse_forecast reads
        self._supplier_prices: dict = {}
        self.prices_fetched_at: float | None = None
        self.prices_error: str | None = None
        # entities subscribe to this to refresh
        self._listeners: list = []
        # survives restarts and option changes (both of which reload the entry)
        self._store: Store = Store(hass, STORAGE_VERSION, f"{DOMAIN}.{entry.entry_id}")

    @property
    def unit_max(self) -> float:
        """The per-pack ceiling the fast-charge estimate assumes."""
        return self._unit_ceiling

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
        if self._price_source in FETCHERS:
            from datetime import timedelta

            await self.async_refresh_prices()
            self._unsub_prices = async_track_time_interval(
                self.hass,
                self.async_refresh_prices,
                timedelta(minutes=PRICE_REFRESH_MINUTES),
            )

    # -- persisted state -----------------------------------------------------
    def _state_to_save(self) -> dict:
        return {
            "enabled": self.enabled,
            "setpoint": self.setpoint,
            "soc_reserve": self.soc_reserve,
            "buy_ceiling_min": self.buy_ceiling_min,
            "buy_ceiling_max": self.buy_ceiling_max,
            "dry_run": self.dry_run,
            "mode": self.mode,
            "recovering": dict(self.recovering),
            # without this every restart re-arms three more probes, and
            # with re-measure-on-restart that is guaranteed to happen
            "phase_attempts": dict(self.phase_attempts),
            "unit_phase": dict(self.unit_phase),
            "phase_detected_at": self.phase_detected_at,
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
        for key in ("buy_ceiling_min", "buy_ceiling_max"):
            if stored and stored.get(key) is not None:
                setattr(self, key, float(stored[key]))
        for name, count in (stored or {}).get("phase_attempts", {}).items():
            if name in self.phase_attempts:
                self.phase_attempts[name] = int(count)
        for name, value in (stored or {}).get("recovering", {}).items():
            # a restart at 6 % would otherwise resume dumping immediately,
            # which is the whole thing this exists to prevent
            if name in self.recovering:
                self.recovering[name] = bool(value)
        self._restore_phases(stored)
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

    def _restore_phases(self, stored: dict | None) -> None:
        """Bring back which pack sits on which leg, unless we were told not to.

        The owner asked for a fresh probe after every restart, and that is the
        default: an integration that has been down cannot vouch for what an
        electrician did while it was. Turning it off keeps the stored placement,
        which is what a stable site wants once the wiring is known - a probe
        parks the packs for a minute every time Home Assistant is restarted, and
        during development that is every few minutes.
        """
        self._apply_manual_phases()
        if self._phase_redetect and self._phase_detect:
            self._refresh_phase_detection_state()
            return
        for name, phase in (stored or {}).get("unit_phase", {}).items():
            if name in self.unit_phase and phase and self._phase_manual.get(name) is None:
                self.unit_phase[name] = int(phase)
        self.phase_detected_at = (stored or {}).get("phase_detected_at")
        self._refresh_phase_detection_state()

    async def async_stop(self, revert: bool = True) -> None:
        if self._unsub:
            self._unsub()
            self._unsub = None
        if self._unsub_prices:
            self._unsub_prices()
            self._unsub_prices = None
        # A probe left running would command a pack *after* the safe revert had
        # already let go of it, and per gotcha 1 that pack then holds power
        # indefinitely. Cancel first, revert second.
        if self._detect_task is not None:
            self._detect_task.cancel()
            self._detect_task = None
            self._detecting = False
        if revert:
            await self._revert_all_to_self()
        # the last minute of a run is the interesting one when something went
        # wrong on the way down, and it is exactly what sits unflushed
        if self._trace is not None:
            await self.hass.async_add_executor_job(self._trace.flush)

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

    def _update_recovery(self, online: dict) -> None:
        """Latch a pack that has been emptied until it has charged back up.

        The floor is one threshold, so without this it is both where
        discharging stops and where it starts again: the sun lifts a pack off
        5 % and it is discharged straight back to 5 %. Observed at the primary
        site, and the bottom of the pack is the worst place to cycle.

        Deliberately a latch rather than a raised floor. A pack coming down
        from full still discharges all the way to its own limit - raising the
        floor to 10 % would just cost those points. Only the way back *out*
        waits.
        """
        if self._discharge_recovery <= 0:
            for name in self.recovering:
                self.recovering[name] = False
            return
        for name, unit in online.items():
            floor = self._discharge_floor(unit)
            if unit.soc <= floor:
                if not self.recovering[name]:
                    _LOGGER.debug(
                        "%s reached its floor (%.0f %%); holding until %.0f %%",
                        name,
                        floor,
                        floor + self._discharge_recovery,
                    )
                self.recovering[name] = True
            elif unit.soc >= floor + self._discharge_recovery:
                if self.recovering[name]:
                    _LOGGER.debug("%s has recovered to %.0f %%", name, unit.soc)
                self.recovering[name] = False

    def _may_discharge(self, name: str, unit: UnitState) -> bool:
        """Is this pack allowed out, given its floor and its recovery?"""
        return unit.soc > self._discharge_floor(unit) and not self.recovering[name]

    def _gain(self, error: float) -> float:
        """How hard to act on this error - which depends on which way it points.

        Going further out and coming back are not the same risk. Winding the
        command up too eagerly is what oscillates (gotcha 3), because every
        step is a bet on a pack that answers 10-30 s later. Winding it *down*
        cannot run away: the far end of "less" is a pack sitting at 0 W.

        And it is worth doing quickly, because export has exactly one cause -
        a pack still discharging into a load that has already gone away. On the
        primary site's own hour, one pack, coming back at 0.5 instead of 0.25
        cut export from 296 to 227 Wh; the theoretical floor for any loop that
        cannot see the future is 210 Wh.
        """
        if (self.setpoint > 0 and error < 0) or (self.setpoint < 0 and error > 0):
            return self._kp_return
        return self._kp

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
            # three different silences, and they deserve different answers:
            # holding some back, waiting to recover, or genuinely empty
            if any(
                self.recovering[n] and s.soc > self._discharge_floor(s)
                for n, s in online.items()
            ):
                return POLICY_RECOVERING
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
        """Dynamic is only offered once prices can actually be had."""
        return [*MODES, MODE_DYNAMIC] if self.prices_configured else list(MODES)

    @property
    def price_source(self) -> str:
        return self._price_source

    @property
    def prices_configured(self) -> bool:
        """Is there anywhere for prices to come from at all?"""
        if self._price_source in FETCHERS:
            return True
        return self._price_source == SOURCE_ENTITY and bool(self._price_sensor)

    def _session(self):
        """Home Assistant's shared HTTP session. A seam, like `_sleep`: the
        tests hand in their own rather than standing up a real hass."""
        return async_get_clientsession(self.hass)

    async def async_refresh_prices(self, _now=None) -> None:
        """Ask the supplier what today and tomorrow cost.

        Failure is not an error state to recover from: an unreachable supplier
        means no forecast, which disables cheap-hour charging and leaves
        grid-zero regulating exactly as it does without a dynamic contract.
        The previous answer is kept - prices do not change retroactively, and
        its slots expire on their own because the ranking window starts at now.
        """
        fetcher = FETCHERS.get(self._price_source)
        if fetcher is None:
            return
        build, parse = fetcher
        url, body = build(dt_util.now().date())
        try:
            session = self._session()
            async with session.post(url, json=body, timeout=PRICE_TIMEOUT) as response:
                response.raise_for_status()
                payload = await response.json()
        except Exception as err:  # noqa: BLE001 - a supplier is not our problem
            self.prices_error = f"{type(err).__name__}: {err}"
            _LOGGER.warning("Could not fetch prices from %s: %s", self._price_source, err)
            self._notify()
            return

        parsed = parse(payload)
        if not parsed:
            # reached them and understood nothing: say so rather than keeping
            # yesterday's answer around looking healthy
            self.prices_error = "no prices in the response"
            _LOGGER.warning("%s returned no usable prices", self._price_source)
        else:
            self._supplier_prices = parsed
            self.prices_fetched_at = time.time()
            self.prices_error = None
            _LOGGER.debug(
                "fetched %d price slots from %s",
                len(parsed.get("prices", [])),
                self._price_source,
            )
        self._notify()

    def _price_attributes(self) -> dict | None:
        """Whatever currently passes for a published price list."""
        if self._price_source in FETCHERS:
            if not self._supplier_prices or self.prices_fetched_at is None:
                return None
            if time.time() - self.prices_fetched_at > MAX_PRICE_AGE:
                return None
            return self._supplier_prices
        if self._price_source != SOURCE_ENTITY or not self._price_sensor:
            return None
        state = self.hass.states.get(self._price_sensor)
        if state is None or state.state in UNAVAILABLE_STATES:
            return None
        return dict(state.attributes)

    def _price_forecast(self):
        """Upcoming price slots, or None when there are none to be had."""
        attributes = self._price_attributes()
        if attributes is None:
            return None
        slots = parse_forecast(attributes, dt_util.utcnow())
        if slots and self._price_resolution == RESOLUTION_HOURLY:
            # applied here, not at the chart, so the picture and the decisions
            # cannot end up disagreeing about what "cheap" meant
            slots = to_hourly(slots)
        return slots or None

    def solar_remaining(self) -> float | None:
        """kWh of sun still to come today, or None when unknown.

        Several sensors are summed: Forecast.Solar publishes one per roof plane.
        Prefer its "remaining today" entities - a whole-day total is right at
        02:00 and wrong at 17:00, when most of it has already been produced, and
        17:00 is exactly when topping up for the evening matters. Where only a
        day total exists, subtract what has already been produced instead.
        """
        if not self._solar_forecast_sensors:
            return None
        total = 0.0
        seen = False
        for entity_id in self._solar_forecast_sensors:
            value = self._read_float(entity_id)
            if value is not None:
                total += value
                seen = True
        if not seen:
            return None
        if self._solar_produced_sensor:
            produced = self._read_float(self._solar_produced_sensor)
            if produced is not None:
                total -= produced
        return max(total, 0.0)

    def minutes_to_full_at_current_rate(self) -> int | None:
        """The same question, at the rate actually being commanded.

        `minutes_to_full` assumes full power because it exists to schedule a
        fast charge. Next to a pack trickling in from the sun that reads as a
        promise it is not making, so answer the other question too: how long at
        what we are doing now. None when nothing is charging - "never" would be
        the honest answer and a sensor cannot say it.
        """
        if self._full_charge_minutes <= 0:
            return None
        longest: float | None = None
        for cfg in self._units:
            unit = self._unit_snapshot(cfg)
            status = self.unit_status[cfg.name]
            if not unit.online or status.target >= 0:
                continue
            watts = -status.target
            missing = max(unit.charge_limit - unit.soc, 0.0)
            at_full = missing / 100.0 * self._full_charge_minutes
            longest = max(longest or 0.0, at_full * unit.unit_max / watts)
        return int(round(longest)) if longest is not None else None

    def charge_ceiling(self) -> float | None:
        """How full it is worth buying to right now, after the user's bounds."""
        computed = self._solar_headroom_ceiling()
        if computed is None:
            return None
        return self._bound_ceiling(computed)

    def current_price(self) -> dict | None:
        """What this hour costs, and what the next one does.

        The Plan sensor says how many hours are cheap; it does not say what you
        are paying right now, which is the first thing anyone looks for.
        """
        slots = self._price_forecast()
        if not slots:
            return None
        now = dt_util.utcnow()
        current = slot_at(slots, now)
        if current is None:
            return None
        later = [s for s in slots if s.start >= current.end]
        nxt = min(later, key=lambda s: s.start) if later else None
        return {
            "price": round(current.price, 4),
            "role": self._price_role(current, now),
            "until": current.end.isoformat(),
            "next_price": round(nxt.price, 4) if nxt else None,
        }

    def current_market_price(self) -> float | None:
        """The exchange component of this hour, without tax or markup.

        Import is billed all-in; export is not, so the two need different
        numbers. What a supplier actually pays back is their own arrangement -
        this is the exchange price it is calculated from, and nothing more is
        claimed for it. Only available on the direct route; a third-party
        sensor publishes whichever single number it publishes.
        """
        attributes = self._price_attributes() or {}
        rows = attributes.get("market_prices")
        if not rows:
            return None
        slots = parse_forecast({"prices": rows}, dt_util.utcnow())
        current = slot_at(slots, dt_util.utcnow()) if slots else None
        return None if current is None else round(current.price, 4)

    def _price_role(self, slot, now) -> str:
        """Which decision this slot belongs to - the same one the chart draws."""
        forecast = self._price_forecast() or []
        if slot in cheapest_slots(
            forecast, now, self._cheap_hours, PRICE_WINDOW_HOURS
        ):
            return "cheap"
        if slot in dearest_slots(
            forecast, now, self._expensive_hours, PRICE_WINDOW_HOURS
        ):
            return "dear"
        return "normal"

    def plan(self) -> dict:
        """What it expects and intends today, for the dashboard.

        Deliberately a plain summary of the inputs and the resulting hours -
        not a forecast of what it will command. The setpoint depends on the
        house minute by minute, and pretending otherwise would be a graph that
        looks authoritative and is wrong.
        """
        now = dt_util.utcnow()
        slots = self._price_forecast()

        def describe(chosen):
            return [
                {
                    "start": slot.start.isoformat(),
                    "end": slot.end.isoformat(),
                    "price": round(slot.price, 4),
                }
                for slot in chosen
            ]

        cheapest = cheapest_slots(
            slots, now, self._cheap_hours, PRICE_WINDOW_HOURS
        ) if slots else []
        dearest = dearest_slots(
            slots, now, self._expensive_hours, PRICE_WINDOW_HOURS
        ) if slots else []

        # The whole day, not just what is left of it. A chart that starts at
        # "now" shows nothing of today by the evening, which is the opposite of
        # what somebody asking for today's prices wants.
        #
        # The roles are computed here rather than left to a dashboard picking a
        # threshold: "cheap" has to mean the hours this will actually buy on.
        # An hour that has already passed gets no role at all - the ranking is
        # forward-looking, so claiming one would be inventing a decision that
        # was never made.
        cheap_at = {slot.start for slot in cheapest}
        dear_at = {slot.start for slot in dearest}
        day_start = dt_util.as_local(now).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        hours = [
            {
                "start": slot.start.isoformat(),
                "end": slot.end.isoformat(),
                "price": round(slot.price, 4),
                "past": slot.end <= now,
                "role": (
                    "past" if slot.end <= now
                    else "cheap" if slot.start in cheap_at
                    else "dear" if slot.start in dear_at
                    else "normal"
                ),
            }
            for slot in sorted(slots or [], key=lambda s: s.start)
            if slot.end > day_start
        ]

        return {
            "has_prices": slots is not None,
            "hours": hours,
            "cheap_hours": describe(cheapest),
            "dear_hours": describe(dearest),
            "solar_remaining_kwh": self.solar_remaining(),
            "usable_capacity_kwh": self.usable_capacity_kwh(),
            "charge_ceiling": self.charge_ceiling(),
            "buy_ceiling_min": self.buy_ceiling_min,
            "buy_ceiling_max": self.buy_ceiling_max,
            "soc_reserve": self.soc_reserve,
            "mode": self.mode,
        }

    def solar_breakdown(self) -> dict:
        """Every number behind the remaining-sun figure, for checking it.

        "0.0 kWh remaining" is correct at midnight and alarming at noon, and
        from the figure alone there is no telling which sensor is at fault - a
        forecast that is not reading, or a production sensor that is too high.
        So show the parts.
        """
        per_sensor = {
            entity_id: self._read_float(entity_id)
            for entity_id in self._solar_forecast_sensors
        }
        values = [v for v in per_sensor.values() if v is not None]
        produced = (
            self._read_float(self._solar_produced_sensor)
            if self._solar_produced_sensor
            else None
        )
        return {
            "forecast_per_sensor": per_sensor,
            "forecast_total_kwh": round(sum(values), 2) if values else None,
            "produced_today_sensor": self._solar_produced_sensor,
            "produced_today_kwh": produced,
            "remaining_kwh": self.solar_remaining(),
        }

    def usable_capacity_kwh(self) -> float | None:
        """Roughly what the packs hold, from the measured empty-to-full time.

        No pack reports its capacity, but charging at `unit_max` for the
        measured duration is exactly that energy - so the number the owner has
        to measure anyway gives this for free.
        """
        if self._full_charge_minutes <= 0:
            return None
        total = 0.0
        for cfg in self._units:
            unit = self._unit_snapshot(cfg)
            if unit.online:
                total += unit.unit_max * self._full_charge_minutes / 60.0 / 1000.0
        return total or None

    def _solar_headroom_ceiling(self) -> float | None:
        """How full it is worth buying to, given the sun still coming.

        Do not buy what arrives free: filling to 100 % at 02:00 leaves nowhere
        to put the day's production, while at 17:00 there is nothing left to
        wait for and topping up is exactly right.
        """
        remaining = self.solar_remaining()
        capacity = self.usable_capacity_kwh()
        if remaining is None or capacity is None or capacity <= 0:
            return None
        return max(0.0, min(100.0, 100.0 - remaining / capacity * 100.0))

    def _sun_is_enough(self) -> bool:
        """Fallback for when the capacity is not known yet: a plain threshold."""
        if not self._solar_forecast_sensors or self._solar_forecast_max <= 0:
            return False
        remaining = self.solar_remaining()
        if remaining is None:
            return False  # no forecast is not a reason to skip a cheap hour
        return remaining >= self._solar_forecast_max

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
        # How full is it worth buying to? Prefer the solar-aware ceiling: it
        # answers "how much can I still get free" instead of guessing with a
        # fixed threshold. Falls back while the capacity is unmeasured.
        ceiling = self._solar_headroom_ceiling()
        # only name the sun when the sun is actually why: with no forecast the
        # plain threshold is doing the work, and saying otherwise would send
        # someone hunting through Forecast.Solar for nothing
        blame_the_sun = ceiling is not None
        if ceiling is None:
            if self._sun_is_enough():
                return False, None
            ceiling = self._charge_below_soc

        ceiling = self._bound_ceiling(ceiling)
        if ceiling <= 0:
            # more sun coming than the packs could hold: buying nothing is right
            return False, POLICY_SOLAR_HEADROOM if blame_the_sun else None
        if not any(s.soc < min(ceiling, s.charge_limit) for s in online.values()):
            return False, POLICY_SOLAR_HEADROOM if blame_the_sun else None
        return True, POLICY_DYNAMIC_CHARGE

    def _dynamic_should_hold(self, online: dict) -> bool:
        """Refuse to discharge now, to spend the charge on a dearer hour.

        The packs hold less than a day's consumption, so the question is not
        whether they can be filled but where the stored kWh are spent. Covering
        a cheap midday hour from the battery and then buying at the evening peak
        is the expensive way round.

        Two exceptions, both about not wasting what is free: a nearly full pack
        discharges anyway - refusing leaves nowhere for the sun still coming -
        and without prices there is nothing to be clever with.
        """
        if self._expensive_hours <= 0:
            return False
        slots = self._price_forecast()
        if slots is None:
            return False
        if is_dear_now(slots, dt_util.utcnow(), self._expensive_hours, PRICE_WINDOW_HOURS):
            return False
        if any(s.soc >= self._discharge_anyway_soc for s in online.values()):
            return False
        return True

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

        Going back the other way is not free either. "Stop commanding" leaves
        the packs holding whatever they were last told, indefinitely - there is
        no watchdog on the device (gotcha 1). So hand them back first, exactly
        as the kill-switch does, and only then stop writing. Someone reaching
        for this switch to make it stop expects it to stop.

        Only on a deliberate flip: at startup dry run is simply the state, and
        writing a revert on the way up would be commanding packs this mode
        exists never to touch.
        """
        if value and not self.dry_run:
            await self._revert_all_to_self()
        self.dry_run = value
        if value:
            _LOGGER.warning(
                "Battery Management is in DRY RUN: it will decide but command "
                "nothing (the packs have been handed back first)"
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

    async def async_set_buy_ceiling(self, *, low=None, high=None) -> None:
        """Bound the computed buy-up-to ceiling by hand.

        The calculation leans on the solar forecast, and a site whose forecast
        reads half the real production would otherwise buy far too much. Both
        ends are adjustable live rather than through a reload, because this is
        the number you tune while watching what actually happened.
        """
        if low is not None:
            self.buy_ceiling_min = max(0.0, min(100.0, float(low)))
        if high is not None:
            self.buy_ceiling_max = max(0.0, min(100.0, float(high)))
        await self._store.async_save(self._state_to_save())
        self._notify()
        if self.enabled:
            await self._async_tick(dt_util.utcnow())

    def _bound_ceiling(self, ceiling: float) -> float:
        """Apply the user's floor and ceiling; the floor never wins outright."""
        low = min(self.buy_ceiling_min, self.buy_ceiling_max)
        return max(low, min(ceiling, self.buy_ceiling_max))

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

    def _mode_options(self, entity_id: str) -> list[str] | None:
        state = self.hass.states.get(entity_id)
        options = state.attributes.get("options") if state is not None else None
        if isinstance(options, (list, tuple)) and options:
            return [str(o) for o in options]
        return None

    @callback
    def _check_hand_back(self) -> None:
        """Warn when a unit is told to return to a mode it cannot accept.

        Found the hard way: one pack has no P1 meter of its own, so its firmware
        offers no self-consumption at all. Setting that as its hand-back means
        the safe revert is silently refused - and per gotcha 1 the pack then
        keeps its last instruction forever. Dry run never surfaces it, because
        nothing is ever written.
        """
        for cfg in self._units:
            issue_id = f"{self.entry.entry_id}_hand_back_{cfg.name}"
            options = self._mode_options(cfg.mode_select)
            broken = bool(cfg.mode_safe and options and cfg.mode_safe not in options)
            if broken == self._hand_back_issues.get(cfg.name, False):
                continue
            self._hand_back_issues[cfg.name] = broken
            if broken:
                ir.async_create_issue(
                    self.hass,
                    DOMAIN,
                    issue_id,
                    is_fixable=False,
                    severity=ir.IssueSeverity.WARNING,
                    translation_key="hand_back_impossible",
                    translation_placeholders={
                        "unit": cfg.name,
                        "mode": cfg.mode_safe,
                        "options": ", ".join(options or []),
                    },
                )
            else:
                ir.async_delete_issue(self.hass, DOMAIN, issue_id)

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
                "kp_return": self._kp_return,
                "interval_s": self._interval,
                "min_output_w": self._min_output,
                "unit_max_w": self._unit_ceiling,
                "fast_charge_hold": self._fast_charge_hold,
                "full_charge_minutes": self._full_charge_minutes,
                "price_source": self._price_source,
                "price_sensor": self._price_sensor,
                "price_resolution": self._price_resolution,
                "cheap_hours": self._cheap_hours,
                "charge_below_soc": self._charge_below_soc,
                "solar_forecast_sensors": self._solar_forecast_sensors,
                "solar_produced_sensor": self._solar_produced_sensor,
                "solar_forecast_max": self._solar_forecast_max,
                "expensive_hours": self._expensive_hours,
                "discharge_anyway_soc": self._discharge_anyway_soc,
                "discharge_recovery": self._discharge_recovery,
                "phase_sensors": self._phase_sensors,
                "phase_limit_amps": self._phase_amps,
                "phase_voltage": self._phase_volts,
                "phase_margin_percent": self._phase_margin,
                "phase_detect": self._phase_detect,
                "phase_redetect_on_restart": self._phase_redetect,
                "phase_probe_seconds": self._probe_seconds,
                "phase_manual": self._phase_manual,
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
                "minutes_to_full_at_current_rate": (
                    self.minutes_to_full_at_current_rate()
                ),
                "solar_remaining_kwh": self.solar_remaining(),
                "solar_breakdown": self.solar_breakdown(),
                "usable_capacity_kwh": self.usable_capacity_kwh(),
                "solar_headroom_ceiling_soc": self._solar_headroom_ceiling(),
                "buy_ceiling_min": self.buy_ceiling_min,
                "buy_ceiling_max": self.buy_ceiling_max,
                "external_setpoint_w": self.external_setpoint,
                "external_plan_age_s": self.external_plan_age(),
                "external_timeout_min": self._external_timeout,
                "prices_fetched_at": self.prices_fetched_at,
                "prices_error": self.prices_error,
                "price_slots": len((self._price_attributes() or {}).get("prices", [])),
                "current_price": self.current_price(),
                "last_tick": self.last_tick.isoformat() if self.last_tick else None,
                # the fuse protection, including the evidence behind each
                # placement - a probe that guessed wrong is only findable here
                "phase_protection": self.phase_report() if self.phase_protection else None,
                "fuse_headroom_amps": self.fuse_headroom_amps(),
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
                        "power_sensor": cfg.power_sensor,
                    },
                    "modes": {
                        "control": cfg.mode_control,
                        # None means: command 0 and leave the mode alone
                        "safe": cfg.mode_safe,
                        # a defaulted value looks identical to a chosen one,
                        # which is how a wrong hand-back stayed invisible in an
                        # entry that predates the mode step
                        "explicitly_set": cfg.modes_configured,
                        "select_offers": self._mode_options(cfg.mode_select),
                    },
                    "recovering": self.recovering[cfg.name],
                    "phase": self.unit_phase.get(cfg.name),
                    "phase_source": self.phase_source(cfg.name),
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
            # a trace nobody notices has stopped is worse than none, so
            # the counters travel with the download that would be used to
            # ask for it
            "trace": self._trace.summary() if self._trace else None,
        }

    def _log_tick(
        self,
        grid,
        error,
        sp,
        flow,
        alloc,
        online,
        observed_grid=None,
        other_power=None,
        bounds=None,
    ) -> None:
        """One row per tick, to the diagnostics ring *and* to the trace file.

        Recorded in dry run too - that is the entire point of dry run.

        The rule for what goes in: every number the loop read, every bound it
        applied, and the reason it settled on the setpoint it did. Anything
        left out is a question that will need a code change to answer, and the
        questions have all been about days that already scrolled away.
        """
        bounds = bounds or {}
        legs = self.phase_power() or {}
        cfg_by_name_all = {u.name: u for u in self._units}
        row = {
            "at": dt_util.utcnow().isoformat(),
            "grid_w": round(grid),
            # how old the number we just regulated on actually was
            "grid_age_s": self._state_age(self._grid_sensor),
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
            "status": self.status,
            "flow": flow,
            "dry_run": self.dry_run,
            "phases_w": {str(leg): round(w) for leg, w in legs.items()},
            "units": {
                name: {
                    "target_w": alloc.get(name, 0),
                    "soc": online[name].soc,
                    # what the pack itself says it is doing. Never used for
                    # control (gotcha 2), but it is the only way to see how far
                    # behind our command the hardware actually runs.
                    "actual_w": self._unit_power(name),
                    "actual_age_s": self._state_age(
                        getattr(cfg_by_name_all.get(name), "power_sensor", None)
                    ),
                    "soc_age_s": self._state_age(
                        getattr(cfg_by_name_all.get(name), "soc_sensor", None)
                    ),
                    # what the device holds, and how long it took to hold it
                    "readback_w": self._read_float(
                        getattr(cfg_by_name_all.get(name), "target_number", None)
                    ),
                    "ack_s": (
                        self._check_ack(cfg_by_name_all[name])
                        if name in cfg_by_name_all
                        else None
                    ),
                    "phase": self.unit_phase.get(name),
                    "recovering": self.recovering.get(name),
                }
                for name in online
            },
            "offline": [u.name for u in self._units if u.name not in online],
            # watts round to whole numbers; the gain does not - it is 0.25 or
            # 0.5, and rounding it as if it were a wattage logged both as "0"
            # for a whole day, which is worse than not logging it
            **{
                k: (
                    round(v, 3)
                    if k == "gain" and isinstance(v, float)
                    else round(v) if isinstance(v, float) else v
                )
                for k, v in bounds.items()
                if k != "unit_cap_w"
            },
        }
        self.tick_log.append(row)
        self._trace_tick(row, bounds.get("unit_cap_w") or {})

    def _state_age(self, entity_id: str | None) -> float | None:
        """Seconds since this entity last published anything.

        How stale the inputs are is half of "what is going wrong" and it was
        not recorded at all. The P1 meter should be a second or two old; the
        packs publish in bursts 10-30 s apart (gotcha 2). A tick that regulated
        on a 40 s old meter reading explains itself once this is in the row.
        """
        state = self.hass.states.get(entity_id) if entity_id else None
        stamp = getattr(state, "last_updated", None) if state else None
        if stamp is None:
            return None
        try:
            return round((dt_util.utcnow() - stamp).total_seconds(), 1)
        except TypeError:
            return None

    def _note_command(self, name: str, value: int) -> None:
        """Remember when a new command went out, so the ack can be timed."""
        pending = self._command_sent.get(name)
        if pending is not None and pending[0] == value:
            return                      # same value; the clock keeps running
        self._command_sent[name] = (value, dt_util.utcnow())
        self._command_ack.pop(name, None)

    def _check_ack(self, cfg: UnitConfig) -> float | None:
        """How long the pack took to show the value we last sent it.

        The target entity reflects what the *device* holds, so the gap between
        our service call and that number changing is the command round-trip -
        the other half of the lag, and the half nobody had measured. A pack
        that never acknowledges leaves this empty, which is itself the finding.
        """
        pending = self._command_sent.get(cfg.name)
        if pending is None:
            return self._command_ack.get(cfg.name)
        wanted, sent_at = pending
        readback = self._read_float(cfg.target_number)
        if readback is None:
            return self._command_ack.get(cfg.name)
        if abs(abs(readback) - abs(wanted)) <= 1:
            took = round((dt_util.utcnow() - sent_at).total_seconds(), 1)
            self._command_ack[cfg.name] = took
            self._command_sent.pop(cfg.name, None)
            return took
        return None                     # still outstanding

    def _unit_power(self, name: str) -> float | None:
        """What this pack reports it is doing, signed like the setpoint.

        Optional and purely observational. The packs publish this 10-30 s late
        and in bursts, which is precisely why it must never steer anything -
        and precisely why it belongs in the trace next to what we commanded.
        """
        cfg = next((u for u in self._units if u.name == name), None)
        if cfg is None or not cfg.power_sensor:
            return None
        value = self._read_float(cfg.power_sensor)
        if value is None:
            return None
        # the packs publish magnitude; the flow select says which way
        if value > 0 and self.unit_status[name].flow == FLOW_CHARGE:
            value = -value
        return round(value)

    def _trace_tick(self, row: dict, caps: dict) -> None:
        """Flatten one tick into CSV columns and hand it to the writer.

        Flat on purpose: a spreadsheet or `pandas.read_csv` should be able to
        plot any column without anyone unpacking JSON first.
        """
        if self._trace is None:
            return
        flat = {k: v for k, v in row.items() if k not in ("units", "phases_w", "offline")}
        flat["offline"] = "|".join(row.get("offline") or ())
        for leg, watts in (row.get("phases_w") or {}).items():
            flat[f"phase{leg}_w"] = watts
        for name, unit in (row.get("units") or {}).items():
            key = name.lower().replace(" ", "_")
            flat[f"{key}_target_w"] = unit.get("target_w")
            flat[f"{key}_actual_w"] = unit.get("actual_w")
            flat[f"{key}_readback_w"] = unit.get("readback_w")
            flat[f"{key}_ack_s"] = unit.get("ack_s")
            flat[f"{key}_actual_age_s"] = unit.get("actual_age_s")
            flat[f"{key}_soc_age_s"] = unit.get("soc_age_s")
            flat[f"{key}_soc"] = unit.get("soc")
            flat[f"{key}_phase"] = unit.get("phase")
            flat[f"{key}_cap_w"] = round(caps.get(name)) if name in caps else None
            flat[f"{key}_recovering"] = unit.get("recovering")
        if self._trace.add(flat):
            self.hass.async_add_executor_job(self._trace.flush)

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
            # the owner's rule: a pack that dropped out gets re-placed when it
            # returns, because it is no longer provably the pack we measured
            if self._was_online[name] and not snap.online:
                self._forget_phase(name)
            self._was_online[name] = snap.online
        self._apply_manual_phases()
        self._refresh_phase_detection_state()

    def _record(self, name: str, flow: str, watts: int) -> None:
        """Remember what we just commanded, signed like the Setpoint sensor."""
        status = self.unit_status[name]
        status.flow = flow
        status.target = watts if flow == FLOW_DISCHARGE else -watts

    # -- per-phase fuse protection -------------------------------------------
    @property
    def phase_protection(self) -> bool:
        """Configured at all? Without per-phase sensors none of this exists."""
        return bool(self._phase_sensors)

    @property
    def phase_limit_w(self) -> float:
        """What one leg may carry, after the margin."""
        return effective_limit_w(self._phase_amps, self._phase_volts, self._phase_margin)

    def phase_power(self) -> dict[int, float]:
        """Read every leg. A leg we cannot read is simply left out.

        Leaving it out is not the same as calling it zero: `unit_ceilings`
        treats an unknown placement as possibly-on-the-worst-leg, and a leg that
        has gone unreadable drops out of that comparison rather than pretending
        to be empty.
        """
        readings: dict[int, float] = {}
        for index, entity_id in enumerate(self._phase_sensors, start=1):
            value = self._read_float(entity_id)
            if value is not None:
                readings[index] = value
        return readings

    def _our_load_per_phase(self) -> dict[int, float]:
        """Our own last commanded watts, gathered per leg (+ discharge)."""
        totals: dict[int, float] = {}
        for name, status in self.unit_status.items():
            phase = self.unit_phase.get(name)
            if phase is not None:
                totals[phase] = totals.get(phase, 0.0) + status.target
        return totals

    def phase_report(self) -> dict:
        """Everything the fuse protection currently believes, for the sensors."""
        limit = self.phase_limit_w
        measured = self.phase_power()
        other = other_load(measured, self._our_load_per_phase())
        discharge_room, charge_room = room(other, limit)
        volts = self._phase_volts or 1.0
        return {
            "limit_w": round(limit),
            "limit_amps": self._phase_amps,
            "margin_percent": self._phase_margin,
            "phases": {
                phase: {
                    "measured_w": round(watts),
                    "without_us_w": round(other[phase]),
                    # what the fuse is carrying right now, packs included -
                    # this is the one the headline headroom is measured from
                    "amps": round(abs(watts) / volts, 1),
                    # and the same leg with our own command taken back out,
                    # which is what the ceilings are computed against
                    "amps_without_us": round(abs(other[phase]) / volts, 1),
                    "headroom_amps": round((limit - abs(watts)) / volts, 1),
                    "discharge_room_w": round(discharge_room[phase]),
                    "charge_room_w": round(charge_room[phase]),
                    "units": [
                        n for n, p in self.unit_phase.items() if p == phase
                    ],
                }
                for phase, watts in measured.items()
            },
            # which leg the headline number is about; without it the sensor
            # says how close things are without saying to what
            "tightest_phase": (
                min(measured, key=lambda p: limit - abs(measured[p]))
                if measured
                else None
            ),
            "usable_amps": round(self._phase_amps * (1 - self._phase_margin / 100), 1),
            "detection": self.phase_detection,
            "unit_phase": dict(self.unit_phase),
            "detected_at": self.phase_detected_at,
            "probes": self.phase_probe_detail,
            "attempts": dict(self.phase_attempts),
            "gave_up": self._phase_gave_up(),
        }

    def fuse_headroom_amps(self) -> float | None:
        """The tightest leg's remaining amps - one number worth watching."""
        if not self.phase_protection:
            return None
        measured = self.phase_power()
        if not measured:
            return None
        volts = self._phase_volts or 1.0
        limit = self.phase_limit_w
        # what is left before the fuse, in whichever direction the leg is going
        return round(min((limit - abs(w)) / volts for w in measured.values()), 1)

    def _phase_ceilings(
        self, names: list[str], unit_max: dict[str, float], charging: bool
    ) -> dict[str, float]:
        """Per-unit watt ceiling from the fuse, for one direction."""
        measured = self.phase_power()
        if not measured:
            # Configured but unreadable. Falling back to "no limit" would quietly
            # disarm the protection exactly when the meter is misbehaving, so
            # hold the packs instead - the bound is the whole point.
            return {name: 0.0 for name in names}
        other = other_load(measured, self._our_load_per_phase())
        discharge_room, charge_room = room(other, self.phase_limit_w)
        return unit_ceilings(
            names,
            self.unit_phase,
            charge_room if charging else discharge_room,
            unit_max,
        )

    def _unit_caps(self, states: dict, *, charging: bool) -> dict[str, float]:
        """Deliverable watts per unit: its own rating, and the fuse on its leg."""
        caps = {n: s.unit_max for n, s in states.items()}
        if not self.phase_protection or not caps:
            return caps
        ceilings = self._phase_ceilings(list(caps), caps, charging)
        return {n: min(caps[n], ceilings.get(n, caps[n])) for n in caps}

    def phase_source(self, name: str) -> str:
        """How this unit's leg came to be believed - typed in, or measured."""
        if self._phase_manual.get(name) is not None:
            return "manual"
        return "measured" if self.unit_phase.get(name) is not None else "unknown"

    def _phase_unplaced(self) -> list[UnitConfig]:
        """Units whose leg is neither known nor typed in."""
        return [
            u
            for u in self._units
            if self.unit_phase.get(u.name) is None
            and self._phase_manual.get(u.name) is None
        ]

    def _phase_needing_detection(self) -> list[UnitConfig]:
        """Unplaced units it is worth asking again, right now.

        A probe that came back unreadable used to be retried on the very next
        tick. At the primary site one pack answered `too_small` every time, so
        the packs spent the whole day being measured and the control loop never
        got a turn. Now a failure costs a wait, and after a few tries it stops
        asking: an unplaced pack is held to the tightest leg, which is safe,
        whereas a coordinator that never coordinates is not.
        """
        now = time.time()
        ready = []
        for unit in self._phase_unplaced():
            if self.phase_attempts.get(unit.name, 0) >= PHASE_MAX_ATTEMPTS:
                continue
            last = self._phase_last_try.get(unit.name)
            if last is not None and now - last < PHASE_RETRY_SECONDS:
                continue
            ready.append(unit)
        return ready

    def _phase_gave_up(self) -> list[str]:
        """Units it has stopped asking about until told otherwise."""
        return [
            u.name
            for u in self._phase_unplaced()
            if self.phase_attempts.get(u.name, 0) >= PHASE_MAX_ATTEMPTS
        ]

    def _apply_manual_phases(self) -> None:
        """A typed-in leg always wins; it is the one source that cannot be wrong."""
        for name, phase in self._phase_manual.items():
            if phase is not None:
                self.unit_phase[name] = phase

    def _single_phase_shortcut(self) -> bool:
        """One sensor means one leg, and everything is on it. Nothing to probe."""
        if len(self._phase_sensors) != 1:
            return False
        for name in self.unit_phase:
            self.unit_phase[name] = 1
        return True

    def _refresh_phase_detection_state(self) -> None:
        placed = [n for n, p in self.unit_phase.items() if p is not None]
        if not self.phase_protection:
            self.phase_detection = PHASE_DETECT_OFF
        elif len(placed) == len(self.unit_phase):
            self.phase_detection = (
                PHASE_DETECT_MANUAL
                if all(self._phase_manual.get(n) is not None for n in placed)
                else PHASE_DETECT_DONE
            )
        elif not self._phase_detect:
            self.phase_detection = PHASE_DETECT_OFF
        elif self.dry_run:
            # a shadow run writes nothing, and this needs to write to see
            self.phase_detection = PHASE_DETECT_BLOCKED
        elif self._phase_gave_up():
            self.phase_detection = PHASE_DETECT_GAVE_UP
        elif placed:
            self.phase_detection = PHASE_DETECT_PARTIAL
        elif self.phase_detection not in (
            PHASE_DETECT_INCONCLUSIVE,
            PHASE_DETECT_RUNNING,
        ):
            self.phase_detection = PHASE_DETECT_UNKNOWN

    def _forget_phase(self, name: str) -> None:
        """Retire a placement. Called when a unit drops out, per the owner's rule.

        A pack that disappeared and came back is not provably the same pack on
        the same leg - it could have been moved, or replaced. Re-probing costs a
        minute; guarding the wrong leg costs a fuse.
        """
        if self._phase_manual.get(name) is not None:
            return
        if self.unit_phase.get(name) is not None:
            _LOGGER.info("%s went offline; its phase placement is retired", name)
        self.unit_phase[name] = None

    @callback
    def async_request_phase_detection(self) -> None:
        """Ask for a probe. Idempotent - the tick starts it when it is safe to.

        Typed-in placements are left alone: they are re-applied every tick, so
        clearing them here would only make the button appear to do something.
        Someone who wants those re-measured sets the field back to 0.
        """
        for name in self.unit_phase:
            if self._phase_manual.get(name) is None:
                self.unit_phase[name] = None
        # clearing the counters is what makes this a retry rather than a no-op
        # once it has given up
        self.phase_attempts = {name: 0 for name in self.phase_attempts}
        self._phase_last_try = {}
        self._save_state()
        self.phase_detection = PHASE_DETECT_UNKNOWN
        self.phase_probe_detail = {}
        self._notify()

    def _maybe_start_detection(self) -> None:
        """Start a probe if one is wanted, allowed, and not already running."""
        if self._detecting or not self.phase_protection or not self._phase_detect:
            return
        if self.dry_run or not self.enabled or self.fast_charge:
            return
        if self._single_phase_shortcut() or not self._phase_needing_detection():
            return
        # claim the packs *now*, not when the task happens to start: between
        # those two moments this tick would otherwise regulate them, and the
        # next tick would launch a second probe on top of the first
        self._detecting = True
        self.phase_detection = PHASE_DETECT_RUNNING
        self.active_policy = POLICY_PHASE_DETECT
        self.status = "detecting"
        self._detect_task = self.hass.async_create_task(self._async_detect_phases())

    async def _async_detect_phases(self) -> None:
        """Work out which pack is on which leg by moving one and watching.

        Crude on purpose. Nothing in the Modbus data says how the installer ran
        the cables, and asking the owner to read a meter cupboard is how a
        safety feature ends up switched off at the sites nobody lives at.

        The probe is itself bounded by the fuse: the legs are unknown, so the
        power is capped by whichever leg has least room. That is also why it can
        decline - on a busy evening there is no room to push into, and waiting
        is the right answer.
        """
        self._notify()
        try:
            for cfg in self._phase_needing_detection():
                await self._probe_unit(cfg)
        except Exception:  # noqa: BLE001 - a failed probe must not kill the loop
            _LOGGER.exception("Phase detection failed")
        finally:
            # whatever happened, leave nothing running: the packs have no
            # watchdog, so a half-finished probe must not be left holding power
            for cfg in self._units:
                await self._svc_number(cfg.target_number, 0)
                self._record(cfg.name, FLOW_CHARGE, 0)
            self._detecting = False
            self._refresh_phase_detection_state()
            # the loop is about to take over again; leaving "detecting" on the
            # Status sensor describes an activity that has finished, and the
            # next tick may be a whole interval away
            if self.status == "detecting":
                self.status = "idle"
            if self.active_policy == POLICY_PHASE_DETECT:
                self.active_policy = POLICY_GRID_ZERO
            if self.phase_detection in (PHASE_DETECT_DONE, PHASE_DETECT_MANUAL):
                self.phase_detected_at = time.time()
            self._save_state()
            self._notify()

    async def _sleep(self, seconds: float) -> None:
        """Waiting for real hardware to respond. A seam, so tests need not."""
        await asyncio.sleep(seconds)

    @staticmethod
    def _steadiest(series: list[dict]) -> dict[int, float] | None:
        """The two adjacent samples that agree best, averaged.

        The pack ramps and the house wanders, so the last sample is not
        necessarily the truest one - it is merely the latest. Picking the
        quietest neighbouring pair prefers a moment when nothing was moving,
        which is the same standard the baseline is held to.
        """
        readings = [
            {int(k): float(v) for k, v in s.items() if k != "t"} for s in series
        ]
        readings = [r for r in readings if r]
        if not readings:
            return None
        if len(readings) == 1:
            return readings[0]
        best, spread = None, None
        for first, second in zip(readings, readings[1:]):
            legs = set(first) & set(second)
            if not legs:
                continue
            moved = max(abs(first[leg] - second[leg]) for leg in legs)
            if spread is None or moved < spread:
                best, spread = {leg: (first[leg] + second[leg]) / 2 for leg in legs}, moved
        return best or readings[-1]

    def _trace_probe(self, name: str, detail: dict) -> None:
        """Put the probe in the trace file too, so it outlives a restart."""
        if self._trace is None:
            return
        row = {
            "at": detail.get("at"),
            "event": "phase_probe",
            "unit": name,
            "policy": detail.get("reason"),
            "setpoint_w": detail.get("probe_w"),
            "sp_reason": f"winner={detail.get('winner')}",
            "error_w": detail.get("runner_up_w"),
        }
        for leg, delta in (detail.get("deltas") or {}).items():
            row[f"phase{leg}_w"] = delta
        if self._trace.add(row):
            self.hass.async_add_executor_job(self._trace.flush)

    async def _await_rest(self) -> dict[int, float]:
        """Hold until the legs stop moving, then read them.

        Counting to thirty was a guess, and at the primary site it was wrong:
        the baseline was taken while the pack was still delivering its previous
        3500 W, so the probe measured a 144 W difference, failed, and tried
        again - which is exactly what stopped the pack ever coming to rest.

        The minimum wait still applies, because nothing the packs report is
        trustworthy for the first 10-30 s (gotcha 2). Past that, two readings
        that agree mean the house is holding still; if it never does - somebody
        is cooking - the cap gives up and reads anyway rather than postponing
        the measurement for ever.
        """
        await self._sleep(PHASE_SETTLE_SECONDS)
        previous = self.phase_power()
        waited = PHASE_SETTLE_SECONDS
        while waited < PHASE_SETTLE_MAX:
            await self._sleep(PHASE_SETTLE_STEP)
            waited += PHASE_SETTLE_STEP
            current = self.phase_power()
            if not current or not previous:
                return current
            if all(
                abs(current[leg] - previous.get(leg, current[leg]))
                <= PHASE_SETTLE_TOLERANCE
                for leg in current
            ):
                return current
            _LOGGER.debug("phase probe: legs still moving after %.0f s", waited)
            previous = current
        _LOGGER.debug("phase probe: never went quiet; measuring anyway")
        return self.phase_power()

    async def _probe_unit(self, cfg: UnitConfig) -> None:
        state = self._unit_snapshot(cfg)
        if not state.online:
            return
        # counted here, not on success: it is the asking that costs the packs
        # their minute, and an unanswerable question asked forever is the bug
        self.phase_attempts[cfg.name] = self.phase_attempts.get(cfg.name, 0) + 1
        self._phase_last_try[cfg.name] = time.time()

        # charge if there is room to take power in, otherwise push it out; a
        # charge is preferred because it is a load like any other and cannot
        # collide with an export limit
        headroom_soc = state.charge_limit - state.soc
        floor = self._discharge_floor(state)
        if headroom_soc > 5:
            charging, flow = True, FLOW_CHARGE
        elif state.soc - floor > 5:
            charging, flow = False, FLOW_DISCHARGE
        else:
            _LOGGER.debug("%s: no room to probe with, skipping", cfg.name)
            self.phase_probe_detail[cfg.name] = {"reason": "no_soc_room"}
            return

        # everything at rest first, so the baseline is the house alone
        for other in self._units:
            await self._svc_number(other.target_number, 0)
            self._record(other.name, flow, 0)

        baseline = await self._await_rest()
        if not baseline:
            self.phase_probe_detail[cfg.name] = {"reason": "no_phase_readings"}
            return

        # every pack is at rest, so the baseline *is* the house on its own
        discharge_room, charge_room = room(baseline, self.phase_limit_w)
        # we do not know the leg yet, so the tightest one sets the probe
        available = min((charge_room if charging else discharge_room).values())
        probe_w = min(state.unit_max, available)
        if probe_w < PHASE_PROBE_MIN_WATTS:
            _LOGGER.info(
                "%s: only %.0f W of fuse room, too little to probe with; will retry",
                cfg.name,
                probe_w,
            )
            self.phase_probe_detail[cfg.name] = {
                "reason": "no_fuse_room",
                "available_w": round(available),
            }
            return

        await self._svc_select(cfg.flow_select, flow)
        await self._svc_number(cfg.target_number, int(probe_w))
        self._record(cfg.name, flow, int(probe_w))
        # Watched rather than sampled once. The baseline already waits for the
        # legs to go quiet; taking a single reading at the end was the other
        # half of that and it was never done - so a kettle switching on during
        # those 20 s was indistinguishable from the pack, and the answer could
        # come out on the wrong leg with nothing in the record to show why.
        series: list[dict] = []
        try:
            waited = 0.0
            while waited < self._probe_seconds:
                await self._sleep(min(PHASE_PROBE_SAMPLE, self._probe_seconds - waited))
                waited += PHASE_PROBE_SAMPLE
                sample = self.phase_power()
                if sample:
                    series.append(
                        {"t": round(waited), **{str(k): round(v) for k, v in sample.items()}}
                    )
            during = self._steadiest(series) or self.phase_power()
        finally:
            await self._svc_number(cfg.target_number, 0)
            self._record(cfg.name, flow, 0)

        phase, detail = attribute_phase(baseline, during, probe_w, charging)
        # the whole measurement, not just its conclusion: a refusal that says
        # "too_small" and nothing else cannot be told apart from a pack that
        # never obeyed, and that ambiguity cost a day of probing already
        detail["baseline"] = {str(k): round(v) for k, v in baseline.items()}
        detail["series"] = series
        detail["settled_on"] = {str(k): round(v) for k, v in (during or {}).items()}
        detail["at"] = dt_util.utcnow().isoformat()
        self.phase_probe_detail[cfg.name] = detail
        self._trace_probe(cfg.name, detail)
        if phase is None:
            attempts = self.phase_attempts.get(cfg.name, 0)
            _LOGGER.warning(
                "%s: could not tell which phase it is on (%s, attempt %d of %d); %s",
                cfg.name,
                detail.get("reason"),
                attempts,
                PHASE_MAX_ATTEMPTS,
                detail.get("deltas"),
            )
            if attempts >= PHASE_MAX_ATTEMPTS:
                _LOGGER.warning(
                    "%s: giving up on measuring its phase. Type it in on the "
                    "unit's page, or press Detect phases to try again. Until "
                    "then it is held to whichever leg has least room.",
                    cfg.name,
                )
            self.phase_detection = PHASE_DETECT_INCONCLUSIVE
            return
        _LOGGER.info("%s is on phase L%d (%s)", cfg.name, phase, detail.get("deltas"))
        self.unit_phase[cfg.name] = phase
        self.phase_attempts[cfg.name] = 0

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
    def _split(mag: float, weights: dict, umax: dict, pool: list) -> dict:
        """Proportional split, re-offering whatever a ceiling refuses.

        A unit that hits its ceiling stops taking a share and what it could not
        take goes back to the others. Without that, a lopsided weighting quietly
        under-delivers - `sp = 5000` with weights 3:1 gave 3500 + 1250, and the
        missing 250 W is a steady-state error the integrator cannot correct,
        because it is already sitting on its own bound.

        This never overshoots: what is handed out is only ever what was left of
        `mag`, and the tick has already clamped `mag` to the sum of the
        ceilings. It matters much more now than it did, because the fuse
        protection produces ceilings of a few hundred watts, not 3500.
        """
        shares: dict = {}
        pool = list(pool)
        remaining = mag
        while pool:
            total = sum(weights[u] for u in pool)
            if total <= 0:
                break
            capped = [
                u for u in pool if remaining * weights[u] / total >= umax[u]
            ]
            if not capped:
                for u in pool:
                    shares[u] = remaining * weights[u] / total
                return shares
            for u in capped:
                shares[u] = umax[u]
                remaining -= umax[u]
                pool.remove(u)
            remaining = max(remaining, 0.0)
        for u in pool:
            shares[u] = 0.0
        return shares

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
            shares = BatteryCoordinator._split(mag, weights, umax, active)
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
        if self._detecting:
            # a probe owns the packs for its minute; regulating underneath it
            # would be measuring our own interference
            return
        if not self.enabled and not self.fast_charge:
            self.active_policy = POLICY_DISABLED
            # keep looking even while idle: "disconnected" has to mean the pack
            # cannot be reached, not merely that nobody asked, and the meter
            # reading is worth checking before anything is switched on
            self._refresh_observations()
            self._check_hand_back()
            self.last_grid_observed = self._read_float(self._grid_sensor)
            self._notify()
            return
        try:
            snaps = {u.name: self._unit_snapshot(u) for u in self._units}
            cfg_by_name = {u.name: u for u in self._units}

            self._refresh_observations(snaps)
            self._check_hand_back()
            self._maybe_start_detection()
            if self._detecting:
                self._notify()
                return

            # ---- FAST CHARGE override --------------------------------------
            if self.fast_charge:
                self.status = "fast_charge"
                self.active_policy = POLICY_FAST_CHARGE
                all_full = True
                # "as fast as possible" still means "within the main fuse" -
                # this is the one place that commands full rating outright, so
                # it is the most likely single thing to drop a leg
                wanting = {
                    n: s
                    for n, s in snaps.items()
                    if s.online and s.soc < s.charge_limit - 1
                }
                caps = self._unit_caps(wanting, charging=True)
                if caps and min(caps.values()) < max(
                    s.unit_max for s in wanting.values()
                ):
                    self.active_policy = POLICY_PHASE_LIMIT
                for name, s in snaps.items():
                    cfg = cfg_by_name[name]
                    if not s.online:
                        continue
                    if s.soc < s.charge_limit - 1:
                        all_full = False
                        watts = int(caps.get(name, s.unit_max))
                        await self._svc_select(cfg.flow_select, FLOW_CHARGE)
                        await self._svc_number(cfg.target_number, watts)
                        self._record(name, FLOW_CHARGE, watts)
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
                self.last_grid_observed = None
                self._notify()
                return

            # in dry run the meter reflects whoever *is* in charge, so close
            # the loop on reconstructed data instead of pretending it is ours
            observed_grid, other_power = grid, None
            if self.dry_run and self._shadow_simulate:
                grid, other_power = self._shadow_grid(grid)
            self.last_grid_observed = observed_grid
            self.last_grid_used = grid
            self.last_other_power = other_power

            error = grid - self._bias
            online = {n: s for n, s in snaps.items() if s.online}

            self._update_recovery(online)
            can_discharge = {
                n: s for n, s in online.items() if self._may_discharge(n, s)
            }
            can_charge = {n: s for n, s in online.items() if s.soc < s.charge_limit}

            # What the packs could deliver if only their own state of charge
            # and rating mattered...
            free_dis = sum(s.unit_max for s in can_discharge.values())
            free_chg = sum(s.unit_max for s in can_charge.values())
            # ...and what the main fuse leaves of that, leg by leg. This is a
            # bound like every other one here, so the anti-windup clamp covers
            # it too: the integrator cannot wind up against a fuse either.
            dis_cap = self._unit_caps(can_discharge, charging=False)
            chg_cap = self._unit_caps(can_charge, charging=True)
            maxdis = sum(dis_cap.values())
            maxchg = sum(chg_cap.values())

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

            hold_policy = None
            if self.mode == MODE_DYNAMIC and not dynamic_charge:
                if self._dynamic_should_hold(online):
                    upper = min(upper, 0.0)
                    hold_policy = POLICY_DYNAMIC_HOLD

            external_sp, external_policy = (None, None)
            if self.mode == MODE_EXTERNAL:
                external_sp, external_policy = self._external_target()
            self._sync_external_issue(
                self.mode == MODE_EXTERNAL and external_sp is None
            )

            # everything from here is recorded, so the trace can answer "why
            # that number" without anyone having to re-derive it afterwards
            sp_before, gain_used, sp_reason = self.setpoint, None, "integrate"
            if dynamic_charge:
                sp, sp_reason = -maxchg, "dynamic_buy"
            elif external_sp is not None:
                # the plan proposes; the clamp below still disposes
                sp, sp_reason = external_sp, "external_plan"
            elif abs(error) < self._deadband:
                sp, sp_reason = self.setpoint, "deadband"
            else:
                gain_used = self._gain(error)
                sp = self.setpoint + gain_used * error
            sp_wanted = sp
            sp = max(min(sp, upper), lower)
            if sp != sp_wanted:
                sp_reason = "clamped_upper" if sp_wanted > upper else "clamped_lower"
            self.setpoint = sp

            umax = {n: s.unit_max for n, s in online.items()}
            umax.update(dis_cap if sp > 0 else chg_cap)

            # "the fuse is what is stopping me" outranks the generic reasons,
            # because it is the one a flat graph would otherwise never explain
            phase_policy = None
            if sp > 0 and maxdis < free_dis - 1 and sp >= maxdis - 1:
                phase_policy = POLICY_PHASE_LIMIT
            elif sp < 0 and maxchg < free_chg - 1 and -sp >= maxchg - 1:
                phase_policy = POLICY_PHASE_LIMIT

            self.active_policy = (
                dynamic_policy
                or hold_policy
                or external_policy
                or phase_policy
                or self._classify(error, sp, online, maxdis, maxchg)
            )

            if sp > 0:  # discharge
                flow = FLOW_DISCHARGE
                weights = {
                    n: max(s.soc - self._discharge_floor(s), 0.0)
                    for n, s in online.items()
                    if self._may_discharge(n, s)
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
                # started before the readback is looked at, so the round-trip
                # is timed from the command rather than from noticing it
                self._note_command(name, alloc.get(name, 0))

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
                grid,
                error,
                sp,
                flow,
                alloc,
                online,
                observed_grid,
                other_power,
                bounds={
                    "sp_before_w": sp_before,
                    "sp_wanted_w": sp_wanted,
                    "sp_reason": sp_reason,
                    "gain": gain_used,
                    "upper_w": upper,
                    "lower_w": lower,
                    "free_discharge_w": free_dis,
                    "free_charge_w": free_chg,
                    "fuse_discharge_w": maxdis,
                    "fuse_charge_w": maxchg,
                    "unit_cap_w": dis_cap if sp > 0 else chg_cap,
                },
            )
            self._save_state()
            self._notify()
        except Exception:  # noqa: BLE001  -- never let the loop die silently
            _LOGGER.exception("Battery Management control tick failed")
            self.status = "degraded"
            self._notify()

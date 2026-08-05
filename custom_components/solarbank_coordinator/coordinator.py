"""Core control logic for the Solarbank Coordinator.

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
from dataclasses import dataclass

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.util import dt as dt_util

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
    CONF_MODE_SELECT,
    CONF_SOC_SENSOR,
    CONF_TARGET_NUMBER,
    CONF_UNIT_MAX,
    CONF_UNIT_NAME,
    CONF_UNITS,
    DEFAULT_BIAS,
    DEFAULT_DEADBAND,
    DEFAULT_INTERVAL,
    DEFAULT_KP,
    DEFAULT_MIN_OUTPUT,
    DEFAULT_UNIT_MAX,
    FALLBACK_CHARGE_LIMIT,
    FALLBACK_DISCHARGE_LIMIT,
    FLOW_CHARGE,
    FLOW_DISCHARGE,
    MODE_SELF,
    MODE_THIRD_PARTY,
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
    charge_limit: str | None
    discharge_limit: str | None


@dataclass
class UnitState:
    """Live snapshot of one unit used inside a control tick."""

    cfg: UnitConfig
    online: bool
    soc: float
    charge_limit: float
    discharge_limit: float
    unit_max: float


class SolarbankCoordinator:
    """Runs the periodic control loop and holds shared state."""

    def __init__(self, hass: HomeAssistant, entry) -> None:
        self.hass = hass
        self.entry = entry
        data = {**entry.data, **entry.options}

        self._grid_sensor: str = data[CONF_GRID_POWER]
        self._units: list[UnitConfig] = [UnitConfig(**u) for u in data[CONF_UNITS]]

        self._bias: float = float(data.get(CONF_BIAS, DEFAULT_BIAS))
        self._deadband: float = float(data.get(CONF_DEADBAND, DEFAULT_DEADBAND))
        self._kp: float = float(data.get(CONF_KP, DEFAULT_KP))
        self._interval: int = int(data.get(CONF_INTERVAL, DEFAULT_INTERVAL))
        self._min_output: float = float(data.get(CONF_MIN_OUTPUT, DEFAULT_MIN_OUTPUT))
        self._unit_ceiling: float = float(data.get(CONF_UNIT_MAX, DEFAULT_UNIT_MAX))

        # runtime state
        self.enabled: bool = False
        self.fast_charge: bool = False
        self.setpoint: float = 0.0          # + = total discharge, - = total charge (W)
        self.status: str = "idle"           # idle | charging | discharging | fast_charge | off | degraded
        self.last_tick = None
        self._unsub = None
        # entities subscribe to this to refresh
        self._listeners: list = []

    # -- lifecycle -----------------------------------------------------------
    async def async_start(self) -> None:
        self._unsub = async_track_time_interval(
            self.hass, self._async_tick, self._interval_timedelta()
        )

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
            await self._set_all_mode(MODE_THIRD_PARTY)
        else:
            self.fast_charge = False
            self.setpoint = 0.0
            await self._revert_all_to_self()
        self.status = "off" if not value else "idle"
        self._notify()
        # kick an immediate tick when turning on
        if value:
            await self._async_tick(dt_util.utcnow())

    async def async_set_fast_charge(self, value: bool) -> None:
        self.fast_charge = value
        if value:
            await self._set_all_mode(MODE_THIRD_PARTY)
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
        await self.hass.services.async_call(
            "select", "select_option",
            {"entity_id": entity_id, "option": option}, blocking=False,
        )

    async def _svc_number(self, entity_id: str, value: float) -> None:
        await self.hass.services.async_call(
            "number", "set_value",
            {"entity_id": entity_id, "value": int(round(value))}, blocking=False,
        )

    async def _set_all_mode(self, mode: str) -> None:
        for u in self._units:
            await self._svc_select(u.mode_select, mode)

    async def _revert_all_to_self(self) -> None:
        for u in self._units:
            await self._svc_number(u.target_number, 0)
            await self._svc_select(u.mode_select, MODE_SELF)

    # -- distribution --------------------------------------------------------
    @staticmethod
    def _distribute(mag: float, weights: dict, umax: dict, min_output: float) -> dict:
        """Split `mag` W across units by weight, clamped to max, with min-output flooring."""
        result = {u: 0.0 for u in weights}
        active = [u for u in weights if weights[u] > 0]
        if mag <= 0 or not active:
            return {u: 0 for u in weights}
        tw = sum(weights[u] for u in active)
        raw = {u: min(mag * weights[u] / tw, umax[u]) for u in active}
        # consolidate sub-minimum shares onto the largest-weight capable unit
        for u in list(raw):
            if 0 < raw[u] < min_output:
                others = [o for o in active if o != u and raw.get(o, 0.0) < umax[o]]
                if others:
                    recip = max(others, key=lambda o: weights[o])
                    raw[recip] = min(raw[recip] + raw[u], umax[recip])
                    raw[u] = 0.0
        result.update(raw)
        return {u: int(round(result.get(u, 0.0))) for u in weights}

    # -- the control tick ----------------------------------------------------
    async def _async_tick(self, _now) -> None:
        if not self.enabled and not self.fast_charge:
            return
        try:
            snaps = {u.name: self._unit_snapshot(u) for u in self._units}
            cfg_by_name = {u.name: u for u in self._units}

            # ---- FAST CHARGE override --------------------------------------
            if self.fast_charge:
                self.status = "fast_charge"
                all_full = True
                for name, s in snaps.items():
                    cfg = cfg_by_name[name]
                    if not s.online:
                        continue
                    if s.soc < s.charge_limit - 1:
                        all_full = False
                        await self._svc_select(cfg.flow_select, FLOW_CHARGE)
                        await self._svc_number(cfg.target_number, s.unit_max)
                    else:
                        await self._svc_number(cfg.target_number, 0)
                if all_full:
                    await self.async_set_fast_charge(False)
                self.last_tick = dt_util.utcnow()
                self._notify()
                return

            # ---- NORMAL grid-zero control ----------------------------------
            grid = self._read_float(self._grid_sensor)
            if grid is None:
                self.status = "degraded"
                self._notify()
                return

            error = grid - self._bias
            online = {n: s for n, s in snaps.items() if s.online}

            maxdis = sum(s.unit_max for s in online.values() if s.soc > s.discharge_limit)
            maxchg = sum(s.unit_max for s in online.values() if s.soc < s.charge_limit)

            if abs(error) < self._deadband:
                sp = self.setpoint
            else:
                sp = self.setpoint + self._kp * error
            sp = max(min(sp, maxdis), -maxchg)
            self.setpoint = sp

            umax = {n: s.unit_max for n, s in online.items()}

            if sp > 0:  # discharge
                flow = FLOW_DISCHARGE
                weights = {
                    n: max(s.soc - s.discharge_limit, 0.0)
                    for n, s in online.items() if s.soc > s.discharge_limit
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

            self.last_tick = dt_util.utcnow()
            self._notify()
        except Exception:  # noqa: BLE001  -- never let the loop die silently
            _LOGGER.exception("Solarbank control tick failed")
            self.status = "degraded"
            self._notify()

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
from dataclasses import dataclass

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.storage import Store
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
    DEFAULT_SOC_RESERVE,
    DEFAULT_UNIT_MAX,
    DOMAIN,
    FALLBACK_CHARGE_LIMIT,
    FALLBACK_DISCHARGE_LIMIT,
    FLOW_CHARGE,
    FLOW_DISCHARGE,
    MAX_SETPOINT_AGE,
    MODE_SELF,
    MODE_THIRD_PARTY,
    POLICY_DEADBAND,
    POLICY_DISABLED,
    POLICY_FAST_CHARGE,
    POLICY_GRID_ZERO,
    POLICY_NO_GRID_DATA,
    POLICY_PACKS_EMPTY,
    POLICY_PACKS_FULL,
    POLICY_SOC_RESERVE,
    SAVE_DELAY,
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

        # runtime state
        self.enabled: bool = False
        self.fast_charge: bool = False
        self.setpoint: float = 0.0          # + = total discharge, - = total charge (W)
        self.status: str = "idle"           # idle | charging | discharging | fast_charge | off | degraded
        self.soc_reserve: float = float(DEFAULT_SOC_RESERVE)
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
        self._unsub = async_track_time_interval(
            self.hass, self._async_tick, self._interval_timedelta()
        )

    # -- persisted state -----------------------------------------------------
    def _state_to_save(self) -> dict:
        return {
            "enabled": self.enabled,
            "setpoint": self.setpoint,
            "soc_reserve": self.soc_reserve,
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
        await self._set_all_mode(MODE_THIRD_PARTY)
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
            await self._set_all_mode(MODE_THIRD_PARTY)
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
        await self._store.async_save(self._state_to_save())
        self._notify()
        if self.enabled or self.fast_charge:
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

        if wants_discharge and maxdis <= 0:
            # would the packs have had anything to give without the reserve?
            free = any(s.soc > s.discharge_limit for s in online.values())
            return POLICY_SOC_RESERVE if free else POLICY_PACKS_EMPTY
        if wants_charge and maxchg <= 0:
            return POLICY_PACKS_FULL
        if not wants_discharge and not wants_charge and sp == 0:
            return POLICY_DEADBAND
        return POLICY_GRID_ZERO

    async def async_set_soc_reserve(self, value: float) -> None:
        """Set the reserve floor (%). Applies in every mode."""
        self.soc_reserve = max(0.0, min(100.0, float(value)))
        await self._store.async_save(self._state_to_save())
        self._notify()
        if self.enabled:
            await self._async_tick(dt_util.utcnow())

    def _record(self, name: str, flow: str, watts: int) -> None:
        """Remember what we just commanded, signed like the Setpoint sensor."""
        status = self.unit_status[name]
        status.flow = flow
        status.target = watts if flow == FLOW_DISCHARGE else -watts

    async def _set_all_mode(self, mode: str) -> None:
        for u in self._units:
            await self._svc_select(u.mode_select, mode)

    async def _revert_all_to_self(self) -> None:
        for u in self._units:
            await self._svc_number(u.target_number, 0)
            await self._svc_select(u.mode_select, MODE_SELF)
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
            return
        try:
            snaps = {u.name: self._unit_snapshot(u) for u in self._units}
            cfg_by_name = {u.name: u for u in self._units}

            # reachability is refreshed every tick; the last commanded target is
            # not cleared here, see UnitStatus
            for name, s in snaps.items():
                self.unit_status[name].online = s.online
                self.unit_status[name].soc = s.soc if s.online else None

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
                if all_full:
                    _LOGGER.debug("fast charge: all units full, switching off")
                    await self.async_set_fast_charge(False)
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

            error = grid - self._bias
            online = {n: s for n, s in snaps.items() if s.online}

            maxdis = sum(
                s.unit_max
                for s in online.values()
                if s.soc > self._discharge_floor(s)
            )
            maxchg = sum(s.unit_max for s in online.values() if s.soc < s.charge_limit)

            if abs(error) < self._deadband:
                sp = self.setpoint
            else:
                sp = self.setpoint + self._kp * error
            sp = max(min(sp, maxdis), -maxchg)
            self.setpoint = sp

            umax = {n: s.unit_max for n, s in online.items()}

            self.active_policy = self._classify(error, sp, online, maxdis, maxchg)

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
            self._save_state()
            self._notify()
        except Exception:  # noqa: BLE001  -- never let the loop die silently
            _LOGGER.exception("Battery Management control tick failed")
            self.status = "degraded"
            self._notify()

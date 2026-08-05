"""Shared fakes for the coordinator tests.

The fakes mimic only what the control loop touches: `hass.states.get()` and
`hass.services.async_call()`. Unit dicts are built from the CONF_* constants —
the same keys the config flow writes — so a rename on either side breaks a test
instead of breaking a live install.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import NamedTuple

import pytest

from custom_components.battery_management.const import (
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
)
from custom_components.battery_management.coordinator import BatteryCoordinator

GRID_SENSOR = "sensor.p1_meter_power"

# kp=1 and deadband=0 make the expected setpoint arithmetic obvious in tests;
# individual tests override what they care about.
DEFAULT_TUNABLES = {
    CONF_BIAS: 0,
    CONF_DEADBAND: 0,
    CONF_KP: 1.0,
    CONF_INTERVAL: 15,
    CONF_MIN_OUTPUT: 150,
    CONF_UNIT_MAX: 3500,
}


class FakeState:
    def __init__(self, state, attributes: dict | None = None) -> None:
        self.state = str(state)
        self.attributes = attributes or {}


class FakeStates:
    def __init__(self, states: dict[str, FakeState] | None = None) -> None:
        self._states = dict(states or {})

    def get(self, entity_id: str) -> FakeState | None:
        return self._states.get(entity_id)

    def set(self, entity_id: str, state, attributes: dict | None = None) -> None:
        self._states[entity_id] = FakeState(state, attributes)

    def remove(self, entity_id: str) -> None:
        self._states.pop(entity_id, None)


class ServiceCall(NamedTuple):
    domain: str
    service: str
    data: dict
    blocking: bool


class FakeServices:
    def __init__(self) -> None:
        self.calls: list[ServiceCall] = []

    async def async_call(
        self, domain: str, service: str, data: dict, blocking: bool = False
    ) -> None:
        self.calls.append(ServiceCall(domain, service, data, blocking))

    # -- query helpers -------------------------------------------------------
    def selects(self) -> list[ServiceCall]:
        return [c for c in self.calls if c.domain == "select"]

    def numbers(self) -> list[ServiceCall]:
        return [c for c in self.calls if c.domain == "number"]

    def options_set(self) -> dict[str, str]:
        """Last option written per select entity."""
        return {c.data["entity_id"]: c.data["option"] for c in self.selects()}

    def targets_set(self) -> dict[str, float]:
        """Last value written per number entity."""
        return {c.data["entity_id"]: c.data["value"] for c in self.numbers()}

    def clear(self) -> None:
        self.calls.clear()


class FakeConfig:
    """Just enough for Home Assistant's real Store to construct."""

    config_dir = "/tmp/battery-management-tests"

    def path(self, *parts: str) -> str:
        return "/".join((self.config_dir, *parts))


class FakeHass:
    def __init__(self, states: dict[str, FakeState] | None = None) -> None:
        self.states = FakeStates(states)
        self.services = FakeServices()
        self.data: dict = {}
        self.config = FakeConfig()


class FakeStore:
    """Stands in for HA's Store.

    The coordinator's *policy* - what it saves, when, and what it refuses to
    restore - is what these tests are about, not Home Assistant's disk I/O.
    Swapping the store keeps the suite identical whether it runs against the
    real Home Assistant or the stub.
    """

    def __init__(self, data: dict | None = None) -> None:
        self.data = data

    async def async_load(self) -> dict | None:
        return self.data

    async def async_save(self, data: dict) -> None:
        self.data = data

    def async_delay_save(self, data_func, delay: float = 0) -> None:
        self.data = data_func()


class FakeEntry:
    def __init__(
        self, data: dict, options: dict | None = None, entry_id: str = "test_entry"
    ) -> None:
        self.data = data
        self.options = options or {}
        self.entry_id = entry_id


def unit_config(name: str, prefix: str, *, with_limits: bool = True) -> dict:
    """A unit dict shaped exactly like the config flow stores it."""
    cfg = {
        CONF_UNIT_NAME: name,
        CONF_MODE_SELECT: f"select.{prefix}_operating_mode",
        CONF_FLOW_SELECT: f"select.{prefix}_grid_flow",
        CONF_TARGET_NUMBER: f"number.{prefix}_target_grid_power",
        CONF_SOC_SENSOR: f"sensor.{prefix}_soc",
    }
    if with_limits:
        # vol.Optional without a default simply omits the key when left blank,
        # so `with_limits=False` is the real "user skipped it" shape.
        cfg[CONF_CHARGE_LIMIT] = f"number.{prefix}_charging_limit"
        cfg[CONF_DISCHARGE_LIMIT] = f"number.{prefix}_discharge_limit"
    return cfg


@dataclass
class System:
    """A built coordinator plus the entity ids needed to assert against it."""

    hass: FakeHass
    entry: FakeEntry
    coordinator: BatteryCoordinator
    units: list[dict] = field(default_factory=list)

    def flow(self, index: int) -> str:
        return self.units[index][CONF_FLOW_SELECT]

    def mode(self, index: int) -> str:
        return self.units[index][CONF_MODE_SELECT]

    def target(self, index: int) -> str:
        return self.units[index][CONF_TARGET_NUMBER]

    def soc(self, index: int) -> str:
        return self.units[index][CONF_SOC_SENSOR]

    def allocation(self) -> dict[str, float]:
        """Target watts keyed by unit name."""
        written = self.hass.services.targets_set()
        return {
            u[CONF_UNIT_NAME]: written.get(u[CONF_TARGET_NUMBER])
            for u in self.units
            if u[CONF_TARGET_NUMBER] in written
        }

    def flows(self) -> list[str]:
        """Flow option written to each unit this tick."""
        written = self.hass.services.options_set()
        return [
            written[u[CONF_FLOW_SELECT]]
            for u in self.units
            if u[CONF_FLOW_SELECT] in written
        ]


@pytest.fixture
def build_system():
    """Factory for a coordinator wired to fake states.

    Defaults mirror the primary site: two Max AC units, the fuller one first.
    Pass ``soc=None`` for a unit to simulate it being offline/unavailable.
    """

    def _build(
        *,
        grid: float | None = 0,
        units: tuple = (("093", 80.0), ("052", 60.0)),
        charge_limit: float | None = 100.0,
        discharge_limit: float | None = 5.0,
        target_max: float | None = 3500,
        with_limits: bool = True,
        enabled: bool = True,
        **tunables,
    ) -> System:
        unit_cfgs = [
            unit_config(f"Batterij {i + 1}", prefix, with_limits=with_limits)
            for i, (prefix, _) in enumerate(units)
        ]
        data = {
            CONF_GRID_POWER: GRID_SENSOR,
            CONF_UNITS: unit_cfgs,
            **DEFAULT_TUNABLES,
            **tunables,
        }

        states: dict[str, FakeState] = {}
        if grid is not None:
            states[GRID_SENSOR] = FakeState(grid)
        for cfg, (_, soc) in zip(unit_cfgs, units):
            if soc is not None:
                states[cfg[CONF_SOC_SENSOR]] = FakeState(soc)
            attrs = {"max": target_max} if target_max is not None else {}
            states[cfg[CONF_TARGET_NUMBER]] = FakeState(0, attrs)
            if with_limits:
                if charge_limit is not None:
                    states[cfg[CONF_CHARGE_LIMIT]] = FakeState(charge_limit)
                if discharge_limit is not None:
                    states[cfg[CONF_DISCHARGE_LIMIT]] = FakeState(discharge_limit)

        hass = FakeHass(states)
        entry = FakeEntry(data)
        coordinator = BatteryCoordinator(hass, entry)
        coordinator._store = FakeStore()
        coordinator.enabled = enabled
        return System(hass, entry, coordinator, unit_cfgs)

    return _build

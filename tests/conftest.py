"""Shared fakes for the coordinator tests.

The fakes mimic only what the control loop touches: `hass.states.get()` and
`hass.services.async_call()`. Unit dicts are built from the CONF_* constants —
the same keys the config flow writes — so a rename on either side breaks a test
instead of breaking a live install.
"""
from __future__ import annotations

import asyncio

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
    CONF_KP_RETURN,
    CONF_TRACE,
    CONF_MIN_OUTPUT,
    CONF_MODE_SELECT,
    CONF_PHASE_SENSORS,
    CONF_SOC_SENSOR,
    CONF_TARGET_NUMBER,
    CONF_UNIT_MAX,
    CONF_UNIT_NAME,
    CONF_UNIT_PHASE,
    CONF_UNITS,
)
from custom_components.battery_management import coordinator as coordinator_module
from custom_components.battery_management.coordinator import BatteryCoordinator

GRID_SENSOR = "sensor.p1_meter_power"
# the primary site publishes these alongside the total
PHASE_SENSORS = [f"sensor.p1_meter_power_phase_{n}" for n in (1, 2, 3)]

# kp=1 and deadband=0 make the expected setpoint arithmetic obvious in tests;
# individual tests override what they care about.
DEFAULT_TUNABLES = {
    CONF_BIAS: 0,
    CONF_DEADBAND: 0,
    CONF_KP: 1.0,
    # explicit, so tests that are not about the gain keep the old
    # symmetric loop instead of inheriting Kp x KP_RETURN_FACTOR
    CONF_KP_RETURN: 1.0,
    # off unless a test asks for it: the trace writes real files, and a
    # suite that scatters CSVs through the checkout is its own bug
    CONF_TRACE: False,
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
        # background work the coordinator kicks off. Held rather than run, so a
        # test decides when the phase probe happens instead of the event loop.
        self.tasks: list = []

    def async_create_task(self, coro, *args, **kwargs):
        self.tasks.append(coro)
        return coro

    def async_add_executor_job(self, func, *args):
        """Run it here and now.

        The trace writer is deliberately synchronous and exception-proof, so
        running it inline keeps the tests deterministic - a flush that happened
        "somewhere later" is not something a test can assert on.
        """
        self.executor_jobs = getattr(self, "executor_jobs", 0) + 1
        result = func(*args)
        # Home Assistant hands back an awaitable, and one caller awaits it.
        # Returning the bare result made that caller fail only in the tests.
        future: asyncio.Future = asyncio.get_event_loop().create_future()
        future.set_result(result)
        return future


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

    def phase(self, number: int) -> str:
        return PHASE_SENSORS[number - 1]

    def set_phases(self, *watts: float) -> None:
        """Rewrite what each leg of the supply is reading."""
        for number, value in enumerate(watts, start=1):
            self.hass.states.set(self.phase(number), value)

    def read_phases(self) -> dict[int, float]:
        return {
            n: float(self.hass.states.get(self.phase(n)).state)
            for n in range(1, len(PHASE_SENSORS) + 1)
            if self.hass.states.get(self.phase(n)) is not None
        }

    def settle_phases(self, *household: float) -> None:
        """Let the meter catch up with what we commanded.

        A static fake meter is a fake that lies: it never shows our own load, so
        `other = phase + target` keeps concluding the household got quieter and
        the ceiling walks upward every tick. A real leg reads the household plus
        us within a tick or two (gotcha 2), which is what this plays out - and
        pinning that is worth more than pinning a frozen number.
        """
        legs = {n: watts for n, watts in enumerate(household, start=1)}
        for cfg in self.units:
            name = cfg[CONF_UNIT_NAME]
            phase = self.coordinator.unit_phase.get(name)
            if phase in legs:
                legs[phase] -= self.coordinator.unit_status[name].target
        self.set_phases(*(legs[n] for n in sorted(legs)))

    async def run_background(self) -> None:
        """Run whatever the coordinator handed to the event loop."""
        while self.hass.tasks:
            await self.hass.tasks.pop(0)

    def wire_house(self, on_phase: dict[str, int], *, obeys: bool = True) -> None:
        """Close the loop: put each pack's commanded power on its real leg.

        The probe only works if the meter actually responds, so a fake that
        just returns fixed numbers would test nothing. This plays the house:
        every time the coordinator waits, the legs are recomputed from the
        baseline plus whatever it has commanded - on the leg the pack is
        *really* on, which is exactly what detection has to discover.
        """
        base = self.read_phases()

        async def house(_seconds: float) -> None:
            legs = dict(base)
            if obeys:
                for cfg in self.units:
                    name = cfg[CONF_UNIT_NAME]
                    # + discharge feeds the leg, so it subtracts from the meter
                    legs[on_phase[name]] -= self.coordinator.unit_status[name].target
            self.set_phases(*(legs[n] for n in sorted(legs)))

        self.coordinator._sleep = house

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
    Pass ``soc=None`` for a unit to simulate it being offline/unavailable, and
    ``dry_run=True`` to check that nothing reaches the packs.
    """
    built: list[System] = []

    def _build(
        *,
        grid: float | None = 0,
        units: tuple = (("093", 80.0), ("052", 60.0)),
        charge_limit: float | None = 100.0,
        discharge_limit: float | None = 5.0,
        target_max: float | None = 3500,
        with_limits: bool = True,
        enabled: bool = True,
        dry_run: bool = False,
        phases: tuple | None = None,
        unit_phase: tuple | None = None,
        **tunables,
    ) -> System:
        unit_cfgs = [
            unit_config(f"Batterij {i + 1}", prefix, with_limits=with_limits)
            for i, (prefix, _) in enumerate(units)
        ]
        if unit_phase is not None:
            for cfg, leg in zip(unit_cfgs, unit_phase):
                cfg[CONF_UNIT_PHASE] = leg
        data = {
            CONF_GRID_POWER: GRID_SENSOR,
            CONF_UNITS: unit_cfgs,
            **DEFAULT_TUNABLES,
            **tunables,
        }
        if phases is not None:
            # opt-in, exactly as in the wizard: no sensors, no fuse protection
            data[CONF_PHASE_SENSORS] = PHASE_SENSORS[: len(phases)]

        states: dict[str, FakeState] = {}
        if grid is not None:
            states[GRID_SENSOR] = FakeState(grid)
        for number, watts in enumerate(phases or (), start=1):
            states[PHASE_SENSORS[number - 1]] = FakeState(watts)
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
        # live by default: these tests are about what gets commanded. Dry run
        # ships enabled, and has its own tests.
        coordinator.dry_run = dry_run
        coordinator.enabled = enabled
        system = System(hass, entry, coordinator, unit_cfgs)
        built.append(system)
        return system

    yield _build

    # a tick can hand a phase probe to the event loop; a test that did not care
    # about it would otherwise leak a coroutine and warn at collection time
    for system in built:
        while system.hass.tasks:
            system.hass.tasks.pop(0).close()


@pytest.fixture(autouse=True)
def issues(monkeypatch):
    """Stand in for Home Assistant's issue registry, for every test.

    The real one wants a live event loop and a loaded registry, which a fake
    hass cannot provide. Autouse so no test can reach the real registry by
    accident; tests that care about repair issues just ask for this dict.
    """
    raised: dict = {}

    class FakeIssueRegistry:
        IssueSeverity = coordinator_module.ir.IssueSeverity

        @staticmethod
        def async_create_issue(hass, domain, issue_id, **kwargs):
            raised[issue_id] = kwargs

        @staticmethod
        def async_delete_issue(hass, domain, issue_id):
            raised.pop(issue_id, None)

    monkeypatch.setattr(coordinator_module, "ir", FakeIssueRegistry)
    return raised

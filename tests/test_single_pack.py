"""One pack at low load, and the night-by-night measurement that judges it.

A low discharge split over two packs runs both in the least efficient part of
their range. With the switch on, the fullest pack carries up to
`SINGLE_PACK_BELOW_W` alone. Whether that pays depends on what a resting pack
still uses, so the packs' delivery is set against how far their charge fell,
each night, with the switch on and off.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from custom_components.battery_management import coordinator as coordinator_module
from custom_components.battery_management.const import (
    CONF_BATTERY_POWER_SENSOR,
    CONF_FULL_CHARGE_MINUTES,
    FLOW_DISCHARGE,
    SINGLE_PACK_BELOW_W,
)

from .conftest import GRID_SENSOR

UMAX = {"Batterij 1": 3500, "Batterij 2": 3500}


def targets(system) -> list[int]:
    return [system.coordinator.unit_status[u.name].target for u in system.coordinator._units]


# -- who carries it -----------------------------------------------------------


@pytest.fixture
def pick(build_system):
    system = build_system()
    co = system.coordinator
    co.single_pack = True
    return co


def test_a_low_demand_goes_to_the_fullest_pack(pick):
    weights = pick._one_pack_at_low_load(450, {"Batterij 1": 60.0, "Batterij 2": 75.0}, UMAX)

    assert weights == {"Batterij 1": 0.0, "Batterij 2": 75.0}


def test_off_it_changes_nothing(pick):
    pick.single_pack = False
    weights = {"Batterij 1": 60.0, "Batterij 2": 75.0}

    assert pick._one_pack_at_low_load(450, weights, UMAX) == weights


def test_above_the_line_both_share(pick):
    weights = {"Batterij 1": 60.0, "Batterij 2": 75.0}

    assert pick._one_pack_at_low_load(SINGLE_PACK_BELOW_W + 1, weights, UMAX) == weights


def test_a_demand_resting_on_the_line_does_not_flap(pick):
    """One up to the line; both above it; one again only well below it."""
    weights = {"Batterij 1": 60.0, "Batterij 2": 75.0}
    alone = lambda demand: sum(  # noqa: E731
        1 for w in pick._one_pack_at_low_load(demand, weights, UMAX).values() if w > 0
    ) == 1

    assert alone(450)
    assert alone(SINGLE_PACK_BELOW_W)
    assert not alone(SINGLE_PACK_BELOW_W + 50)
    assert not alone(SINGLE_PACK_BELOW_W - 50)
    assert alone(SINGLE_PACK_BELOW_W * 0.7)


def test_the_pack_carrying_it_keeps_it_until_the_other_is_clearly_fuller(pick):
    """A swap is a ramp down and a ramp up; a single SoC point is not worth one."""
    leader = lambda a, b: next(  # noqa: E731
        n for n, w in pick._one_pack_at_low_load(
            450, {"Batterij 1": a, "Batterij 2": b}, UMAX
        ).items() if w > 0
    )

    assert leader(70.0, 68.0) == "Batterij 1"
    assert leader(66.0, 68.0) == "Batterij 1"
    assert leader(63.0, 68.0) == "Batterij 2"


def test_a_pack_held_down_by_its_own_ceiling_does_not_carry_alone(pick):
    """The fuse can leave the fullest pack a few hundred watts; then two it is."""
    weights = {"Batterij 1": 60.0, "Batterij 2": 75.0}

    assert pick._one_pack_at_low_load(
        450, weights, {"Batterij 1": 3500, "Batterij 2": 300}
    ) == weights


def test_one_pack_allowed_out_is_one_pack(pick):
    weights = {"Batterij 1": 0.0, "Batterij 2": 75.0}

    assert pick._one_pack_at_low_load(450, weights, UMAX) == weights


async def test_the_tick_sends_it_through_one_pack(build_system):
    system = build_system(grid=450, units=(("093", 60.0), ("052", 80.0)))
    system.coordinator.single_pack = True

    await system.coordinator._async_tick(None)

    assert targets(system) == [0, 450]


async def test_without_it_the_tick_splits_as_before(build_system):
    system = build_system(grid=450, units=(("093", 60.0), ("052", 80.0)))

    await system.coordinator._async_tick(None)

    assert all(t > 0 for t in targets(system))
    assert sum(targets(system)) == pytest.approx(450, abs=2)


async def test_the_switch_survives_a_restart(build_system):
    system = build_system()
    await system.coordinator.async_set_single_pack(True)
    stored = system.coordinator._state_to_save()

    fresh = build_system()
    fresh.coordinator._store.data = stored
    await fresh.coordinator._async_restore()

    assert fresh.coordinator.single_pack is True


# -- what it is judged on -----------------------------------------------------

NIGHT = datetime(2026, 9, 30, 0, 59, tzinfo=timezone.utc)
PACKS = "sensor.battery_power"


@pytest.fixture
def clock(monkeypatch):
    now = [NIGHT]
    monkeypatch.setattr(coordinator_module.dt_util, "utcnow", lambda: now[0])
    monkeypatch.setattr(coordinator_module.time, "time", lambda: now[0].timestamp())
    return now


@pytest.fixture
def night(build_system, clock):
    """Two 14 kWh packs at 80 %, each with its own AC output sensor, the
    coordinator watching only."""

    def _build():
        system = build_system(
            enabled=False,
            units=(("093", 80.0), ("052", 80.0)),
            **{CONF_FULL_CHARGE_MINUTES: 240, CONF_BATTERY_POWER_SENSOR: PACKS},
        )
        for unit in system.coordinator._units:
            unit.power_sensor = f"sensor.{unit.name}_ac_output"
            system.coordinator.unit_status[unit.name].flow = FLOW_DISCHARGE
        return system

    return _build


async def run_night(system, clock, *, watts=225.0, points=8, until=None, each=None) -> None:
    """01:00 to 05:01, each pack delivering `watts` while its charge falls
    by `points` over the night."""
    until = until or NIGHT.replace(hour=5, minute=1)
    start = NIGHT.replace(hour=1, minute=0)
    while clock[0] < until:
        clock[0] += timedelta(minutes=1)
        done = min(max((clock[0] - start).total_seconds() / (4 * 3600), 0.0), 1.0)
        for index, unit in enumerate(system.coordinator._units):
            system.hass.states.set(system.soc(index), 80.0 - round(points * done))
            system.hass.states.set(unit.power_sensor, watts)
        system.hass.states.set(GRID_SENSOR, 0)
        system.hass.states.set(PACKS, 2 * watts)
        if each:
            each(clock[0])
        await system.coordinator._async_tick(None)


async def test_a_night_is_judged_on_delivered_against_drained(night, clock):
    """450 W for four hours is 1.8 kWh; eight points of two 14 kWh packs is
    2.24 kWh. The difference is what the packs used themselves."""
    system = night()
    system.coordinator.single_pack = True

    await run_night(system, clock)

    record = system.coordinator.pack_drain_history["2026-09-30"]
    assert record["delivered_kwh"] == pytest.approx(1.8, abs=0.02)
    assert record["drained_kwh"] == pytest.approx(2.24)
    assert record["efficiency"] == pytest.approx(1.8 / 2.24, abs=0.01)
    assert record["mode"] == "one_pack"


async def test_the_switch_compares_the_two(night, clock):
    system = night()
    co = system.coordinator
    co.pack_drain_history = {
        "2026-09-27": {"efficiency": 0.80, "mode": "both"},
        "2026-09-28": {"efficiency": 0.82, "mode": "both"},
        "2026-09-29": {"efficiency": 0.90, "mode": "one_pack"},
        "2026-09-26": {"efficiency": 0.95, "mode": "mixed"},
    }

    attributes = co.single_pack_attributes()

    assert attributes["efficiency_both_pct"] == 81.0
    assert attributes["nights_both"] == 2
    assert attributes["efficiency_one_pack_pct"] == 90.0
    assert attributes["nights_one_pack"] == 1
    assert attributes["below_w"] == SINGLE_PACK_BELOW_W


async def test_a_night_with_the_switch_flipped_counts_as_neither(night, clock):
    system = night()
    co = system.coordinator

    def flip(moment):
        co.single_pack = moment.hour >= 3

    await run_night(system, clock, each=flip)

    assert co.pack_drain_history["2026-09-30"]["mode"] == "mixed"


async def test_a_night_with_charging_in_it_measures_nothing(night, clock):
    system = night()
    co = system.coordinator

    def buy(moment):
        co.unit_status["Batterij 1"].target = -3500 if moment.hour == 2 else 0

    await run_night(system, clock, each=buy)

    assert co.pack_drain_history == {}


async def test_too_little_drained_is_rounding_not_a_measurement(night, clock):
    """Three points of two packs is 0.84 kWh: a point either way is a third."""
    system = night()

    await run_night(system, clock, watts=80.0, points=3)

    assert system.coordinator.pack_drain_history == {}


async def test_the_nights_survive_a_restart(night, clock):
    system = night()
    await run_night(system, clock)
    stored = system.coordinator._state_to_save()

    fresh = night()
    fresh.coordinator._store.data = stored
    await fresh.coordinator._async_restore()

    assert fresh.coordinator.pack_drain_history == system.coordinator.pack_drain_history


def test_the_switch_entity(build_system):
    switch = pytest.importorskip(
        "custom_components.battery_management.switch",
        reason="needs a real Home Assistant for the switch platform",
    )
    system = build_system()
    entity = switch.SinglePackSwitch(system.coordinator, system.entry)

    assert entity.is_on is False
    assert entity.extra_state_attributes["nights_both"] == 0

"""The standby load: what the house draws while everyone sleeps.

Meter plus packs, so the figure is the house's whichever of the two is
covering it; the lowest steady five minutes between 01:00 and 05:00, reported
once the night is over. The stub's local time is UTC, so the hours below are
the window's own.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from custom_components.battery_management import coordinator as coordinator_module
from custom_components.battery_management.const import (
    BASE_LOAD_HISTORY,
    CONF_BATTERY_POWER_SENSOR,
)

from .conftest import GRID_SENSOR

PACKS = "sensor.battery_power"
NIGHT = datetime(2026, 9, 30, 0, 59, tzinfo=timezone.utc)


@pytest.fixture
def clock(monkeypatch):
    now = [NIGHT]
    monkeypatch.setattr(coordinator_module.dt_util, "utcnow", lambda: now[0])
    monkeypatch.setattr(coordinator_module.time, "time", lambda: now[0].timestamp())
    return now


@pytest.fixture
def house(build_system, clock):
    def _build():
        system = build_system(enabled=False, **{CONF_BATTERY_POWER_SENSOR: PACKS})
        return system

    return _build


async def run(system, clock, until, *, house=450.0, packs=400.0, at=None) -> None:
    """Minute ticks up to `until`, the packs covering `packs` of the house.

    `at` overrides single minutes: {datetime: (house, packs)}.
    """
    at = at or {}
    while clock[0] < until:
        clock[0] += timedelta(minutes=1)
        load, cover = at.get(clock[0], (house, packs))
        system.hass.states.set(GRID_SENSOR, load - cover)
        system.hass.states.set(PACKS, cover)
        await system.coordinator._async_tick(None)


def hour(h: int, minute: int = 0, later: int = 0) -> datetime:
    """A time on the night measured, or `later` days after it."""
    return datetime(2026, 9, 30, h, minute, tzinfo=timezone.utc) + timedelta(days=later)


async def test_a_steady_night_is_reported_when_it_is_over(house, clock):
    system = house()

    await run(system, clock, hour(4, 30))
    co = system.coordinator
    assert co.base_load is None
    assert co.base_load_attributes()["tonight_lowest_w"] == 450

    await run(system, clock, hour(5, 1))

    assert co.base_load == {"night": "2026-09-30", "w": 450, "at": "01:00", "average_w": 450}
    assert co.base_load_history == {"2026-09-30": 450}
    attributes = co.base_load_attributes()
    assert attributes["per_year_kwh"] == round(450 * 8.76)
    assert attributes["window"] == "01:00-05:00"
    assert attributes["tonight_lowest_w"] is None


async def test_the_packs_covering_it_or_the_grid_is_the_same_house(house, clock):
    system = house()

    await run(system, clock, hour(3), packs=0.0)
    await run(system, clock, hour(5, 1), packs=450.0)

    assert system.coordinator.base_load["w"] == 450


async def test_the_floor_is_five_minutes_not_a_moment(house, clock):
    """A minute at 100 W is the fridge stopping, not the house's floor; ten
    minutes at 300 W is."""
    system = house()
    dips = {hour(2, 2): (100.0, 50.0)}
    dips.update({hour(3, m): (300.0, 250.0) for m in range(1, 11)})

    await run(system, clock, hour(5, 1), at=dips)

    assert system.coordinator.base_load["w"] == 300
    assert system.coordinator.base_load["at"] == "03:00"


async def test_a_single_dip_is_averaged_into_its_five_minutes(house, clock):
    system = house()

    await run(system, clock, hour(5, 1), at={hour(2, 2): (100.0, 50.0)})

    assert system.coordinator.base_load["w"] == 380


async def test_a_purchase_starting_or_stopping_is_left_out(house, clock):
    """The pack sensor trails the meter: for a tick after a purchase stops it
    still reads -7000 while the meter is already back at 450 - a house of
    -6550, or with a partial reading, a house far too small."""
    system = house()
    buying = {hour(2, m): (450.0, -7000.0) for m in range(1, 60)}
    buying[hour(3, 0)] = (450.0, -7000.0)
    # the meter has stopped, the pack sensor has not caught up
    buying[hour(3, 1)] = (-1050.0, -1500.0)

    await run(system, clock, hour(5, 1), at=buying)

    assert system.coordinator.base_load["w"] == 450


async def test_the_afternoon_does_not_count(house, clock):
    system = house()
    clock[0] = hour(12)

    await run(system, clock, hour(16), house=100.0, packs=0.0)
    await run(system, clock, hour(5, 1, later=1), house=450.0)

    assert system.coordinator.base_load_history == {"2026-10-01": 450}


async def test_a_night_too_short_to_judge_is_not_reported(house, clock):
    """Half an hour measured is not a night; the last one stands."""
    system = house()
    co = system.coordinator
    co.base_load = {"night": "2026-09-29", "w": 430, "at": "02:10", "average_w": 470}
    clock[0] = hour(4, 29)

    await run(system, clock, hour(5, 1), house=200.0, packs=0.0)

    assert co.base_load["night"] == "2026-09-29"
    assert co.base_load_history == {}


async def test_a_restart_in_the_night_keeps_the_night(house, clock):
    system = house()
    await run(system, clock, hour(3))
    stored = system.coordinator._state_to_save()

    fresh = house()
    fresh.coordinator._store.data = stored
    await fresh.coordinator._async_restore()
    await run(fresh, clock, hour(5, 1), house=500.0)

    # the first two hours at 450 were measured before the restart
    assert fresh.coordinator.base_load["w"] == 450


async def test_the_nights_survive_a_restart(house, clock):
    system = house()
    await run(system, clock, hour(5, 1))
    stored = system.coordinator._state_to_save()

    fresh = house()
    fresh.coordinator._store.data = stored
    await fresh.coordinator._async_restore()

    assert fresh.coordinator.base_load == system.coordinator.base_load
    assert fresh.coordinator.base_load_history == {"2026-09-30": 450}


async def test_a_month_of_nights_is_kept(house, clock):
    system = house()
    co = system.coordinator
    co.base_load_history = {
        (NIGHT.date() - timedelta(days=d)).isoformat(): 400 + d for d in range(1, 40)
    }

    await run(system, clock, hour(5, 1))

    assert len(co.base_load_history) == BASE_LOAD_HISTORY
    assert max(co.base_load_history) == "2026-09-30"
    # the week before is what the average is taken over
    assert co.base_load_attributes()["average_7_nights_w"] == round(
        (450 + sum(400 + d for d in range(1, 7))) / 7
    )


def test_the_sensor_reads_last_night(build_system):
    sensor = pytest.importorskip(
        "custom_components.battery_management.sensor",
        reason="needs a real Home Assistant for the sensor platform",
    )
    system = build_system()
    entity = sensor.BaseLoadSensor(system.coordinator, system.entry)

    # always there, unknown until a night has been measured
    assert entity.available is True
    assert entity.native_value is None
    assert entity.extra_state_attributes["history"] == {}

    system.coordinator.base_load = {"night": "2026-09-30", "w": 452, "at": "03:05", "average_w": 497}
    assert entity.native_value == 452
    assert entity.native_unit_of_measurement == sensor.UnitOfPower.WATT
    assert entity.extra_state_attributes["night_average_w"] == 497


async def test_a_house_reading_below_nought_is_not_the_floor(house, clock):
    """The meter and the pack sensor disagreeing, not a house giving power
    back at 03:00."""
    system = house()
    wrong = {hour(3, m): (-100.0, 400.0) for m in range(1, 11)}

    await run(system, clock, hour(5, 1), at=wrong)

    assert system.coordinator.base_load["w"] == 450


async def test_a_night_left_open_by_an_outage_is_closed_by_the_next(house, clock):
    """Home Assistant down from 04:00 to the next night: the night it had
    measured is reported, not merged into the one after it."""
    system = house()
    await run(system, clock, hour(4))
    stored = system.coordinator._state_to_save()

    fresh = house()
    fresh.coordinator._store.data = stored
    await fresh.coordinator._async_restore()
    clock[0] = hour(0, 59, later=1)
    await run(fresh, clock, hour(3, later=1), house=600.0)

    assert fresh.coordinator.base_load_history == {"2026-09-30": 450}
    assert fresh.coordinator.base_load_attributes()["tonight_lowest_w"] == 600


async def test_an_outage_is_not_filled_in_with_the_reading_after_it(house, clock):
    """An hour with no readings, then one at 100 W: that one reading must not
    stand for the whole hour."""
    system = house()
    await run(system, clock, hour(2))
    clock[0] = hour(3)

    await run(system, clock, hour(3, 1), house=100.0, packs=50.0)
    await run(system, clock, hour(5, 1))

    assert system.coordinator.base_load["w"] == 450

"""Hours that have gone keep the verdict they were given at the time.

Asked for by the owner: on a chart of today, everything before now was grey, so
by teatime the picture could not answer "which hours did the battery charge on"
- which is the question a price chart on a battery card exists to answer.

Re-ranking a past hour against today's ranking is not the way to do it, and
never will be: `cheapest_slots` only ever looks forward, so it would be drawing
decisions that were never taken. So the verdict is written down as it is taken,
while the hour is the current one, and read back afterwards.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from custom_components.battery_management import coordinator as coordinator_module
from custom_components.battery_management.const import (
    CONF_CHARGE_BELOW_SOC,
    CONF_CHEAP_HOURS,
    CONF_EXPENSIVE_HOURS,
    CONF_FULL_CHARGE_MINUTES,
    CONF_PRICE_SENSOR,
    MODE_DYNAMIC,
)
from tests.conftest import GRID_SENSOR

PRICE_SENSOR = "sensor.energy_prices"

#: 10:00 UTC, so there is a morning behind us and an evening ahead. One clearly
#: cheap hour at 10:00 and one clearly dear one at 19:00.
START = datetime(2026, 8, 5, 10, 0, tzinfo=timezone.utc)
DAY = [0.40] * 10 + [0.10] + [0.40] * 8 + [0.90] + [0.40] * 4


def price_attributes() -> dict:
    midnight = START.replace(hour=0, minute=0, second=0, microsecond=0)
    return {
        "raw_today": [
            {
                "start": (midnight + timedelta(hours=i)).isoformat(),
                "end": (midnight + timedelta(hours=i + 1)).isoformat(),
                "value": price,
            }
            for i, price in enumerate(DAY)
        ]
    }


@pytest.fixture
def site(build_system, monkeypatch):
    def _build(*, soc=(20.0, 20.0), **options):
        clock = {"now": START}
        monkeypatch.setattr(
            coordinator_module.dt_util, "utcnow", lambda: clock["now"]
        )
        system = build_system(
            grid=300,
            units=(("093", soc[0]), ("052", soc[1])),
            **{
                CONF_PRICE_SENSOR: PRICE_SENSOR,
                CONF_CHEAP_HOURS: 1,
                CONF_EXPENSIVE_HOURS: 1,
                CONF_CHARGE_BELOW_SOC: 100,
                CONF_FULL_CHARGE_MINUTES: 120,
                **options,
            },
        )
        system.hass.states.set(PRICE_SENSOR, DAY[10], price_attributes())
        system.coordinator.mode = MODE_DYNAMIC
        system.clock = clock
        return system

    return _build


def move_to(system, moment):
    """Advance the clock without letting the meter go stale (gotcha 9)."""
    system.clock["now"] = moment
    system.hass.states.set(GRID_SENSOR, 300)


def hour_at(system, hour: int) -> dict:
    wanted = START.replace(hour=hour).isoformat()
    return next(h for h in system.coordinator.plan()["hours"] if h["start"] == wanted)


async def test_a_watched_hour_keeps_its_verdict_once_it_is_over(site):
    system = site()

    await system.coordinator._async_tick(None)
    assert hour_at(system, 10)["role"] == "cheap"

    move_to(system, START + timedelta(hours=2))
    await system.coordinator._async_tick(None)

    gone = hour_at(system, 10)
    assert gone["past"] is True
    assert gone["role"] == "cheap"  # not re-ranked, remembered


async def test_an_hour_nobody_watched_stays_plain_past(site):
    """The integration was not running, so there is no verdict to report and
    inventing one from today's ranking is exactly what this must not do."""
    system = site()

    move_to(system, START + timedelta(hours=2))
    await system.coordinator._async_tick(None)

    assert hour_at(system, 9)["role"] == "past"


async def test_it_records_that_the_grid_was_actually_paid(site):
    """The stronger of the two facts: the role is what we intended, `bought`
    is the meter running."""
    system = site()

    await system.coordinator._async_tick(None)
    move_to(system, START + timedelta(hours=2))
    await system.coordinator._async_tick(None)

    assert hour_at(system, 10)["bought"] is True


async def test_an_hour_that_looked_cheap_but_was_not_bought_says_so(site):
    """Cheap is one of three conditions. Full packs buy nothing, and the chart
    must not claim otherwise about an hour it can still be asked about."""
    system = site(soc=(100.0, 100.0))

    await system.coordinator._async_tick(None)
    move_to(system, START + timedelta(hours=2))
    await system.coordinator._async_tick(None)

    assert hour_at(system, 10)["bought"] is False


async def test_the_verdict_is_recorded_while_the_coordinator_is_switched_off(site):
    """What an hour was is a fact about the hour, not about who was steering -
    and the chart is looked at during a shadow month too."""
    system = site()
    system.coordinator.enabled = False

    await system.coordinator._async_tick(None)
    move_to(system, START + timedelta(hours=2))
    await system.coordinator._async_tick(None)

    assert hour_at(system, 10)["role"] == "cheap"
    assert hour_at(system, 10)["bought"] is False  # switched off: nothing bought


async def test_the_record_survives_a_restart(site):
    """A restart at noon must not grey out the morning being asked about."""
    system = site()
    await system.coordinator._async_tick(None)
    saved = system.coordinator._state_to_save()
    assert saved["price_history"]  # it is written down at all

    revived = site()
    revived.coordinator._store.data = saved
    await revived.coordinator._async_restore()

    move_to(revived, START + timedelta(hours=2))
    assert hour_at(revived, 10)["role"] == "cheap"
    assert hour_at(revived, 10)["bought"] is True


async def test_hours_older_than_the_chart_are_dropped(site):
    """Two days is what the chart can page back to; a week of it on disk is a
    leak with nowhere to show."""
    system = site()
    system.coordinator.price_history["2026-08-01T10:00:00+00:00"] = {
        "role": "cheap",
        "bought": True,
    }

    await system.coordinator._async_tick(None)

    assert "2026-08-01T10:00:00+00:00" not in system.coordinator.price_history
    assert system.coordinator.price_history  # today's is still there

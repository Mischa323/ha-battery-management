"""Waiting for the cheapest hour instead of taking the first cheap one.

The arithmetic is pinned in `test_buy_hours.py`; this is the coordinator
actually doing it - working out how many hours it needs, sitting through a
cheap hour it does not need, and finishing an hour it has started.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from custom_components.battery_management import coordinator as coordinator_module
from custom_components.battery_management.const import (
    CONF_CHARGE_BELOW_SOC,
    CONF_CHEAP_HOURS,
    CONF_FULL_CHARGE_MINUTES,
    CONF_PRICE_SENSOR,
    MODE_DYNAMIC,
    POLICY_DYNAMIC_CHARGE,
)
from tests.conftest import GRID_SENSOR

PRICE_SENSOR = "sensor.energy_prices"

#: The shape of the day the primary site reported on 2026-08-19: four hours
#: that all clear the ranking and the margin, with the genuinely cheapest of
#: them last. Everything either side is dear enough never to qualify.
NOON = datetime(2026, 8, 5, 12, 0, tzinfo=timezone.utc)
DAY = [0.60] * 12 + [0.300, 0.290, 0.285, 0.284] + [0.60] * 8


def price_attributes(now: datetime) -> dict:
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
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
    """Two packs on a dynamic tariff, with the empty-to-full time measured.

    120 minutes is the primary site's real figure (two 7 kWh packs at 3500 W
    each), so an hour of buying is worth 50 points of state of charge.
    """

    def _build(*, at=NOON, soc=(20.0, 20.0), minutes=120.0, **options):
        clock = {"now": at}
        monkeypatch.setattr(
            coordinator_module.dt_util, "utcnow", lambda: clock["now"]
        )
        system = build_system(
            grid=300,
            units=(("093", soc[0]), ("052", soc[1])),
            **{
                CONF_PRICE_SENSOR: PRICE_SENSOR,
                CONF_CHEAP_HOURS: 4,
                CONF_CHARGE_BELOW_SOC: 100,
                CONF_FULL_CHARGE_MINUTES: minutes,
                **options,
            },
        )
        system.hass.states.set(PRICE_SENSOR, DAY[at.hour], price_attributes(at))
        system.coordinator.mode = MODE_DYNAMIC
        system.clock = clock
        return system

    return _build


def buying(system) -> bool:
    return system.coordinator.active_policy == POLICY_DYNAMIC_CHARGE


def move_to(system, moment, *, soc=None):
    """Advance the clock, and keep the inputs alive across the jump.

    A meter that has not reported since the last tick is stale, and a stale
    meter stops the loop before any of this is reached (gotcha 9) - which
    would make every test here pass for entirely the wrong reason.
    """
    system.clock["now"] = moment
    system.hass.states.set(PRICE_SENSOR, DAY[moment.hour], price_attributes(moment))
    system.hass.states.set(GRID_SENSOR, 300)
    if soc is not None:
        for index in (0, 1):
            system.hass.states.set(system.soc(index), soc)


async def test_it_waits_through_a_cheap_hour_it_does_not_need(site):
    """The reported fault, pinned.

    20 % to full is 96 minutes, so two of the four cheap hours are wanted -
    and 12:00 is the dearest of the four, not one of the two.
    """
    system = site(soc=(20.0, 20.0))

    await system.coordinator._async_tick(None)

    assert not buying(system)


async def test_it_buys_once_the_cheapest_hour_arrives(site):
    system = site(at=NOON + timedelta(hours=3), soc=(20.0, 20.0))

    await system.coordinator._async_tick(None)

    assert buying(system)
    assert system.coordinator.setpoint == -7000  # both packs, full tilt


async def test_an_empty_pack_needing_every_cheap_hour_starts_at_once(site):
    """Narrowing must not become dithering: four hours of need is four hours
    of buying, and the first of them is now."""
    system = site(soc=(0.0, 0.0), minutes=300.0)

    await system.coordinator._async_tick(None)

    assert buying(system)


async def test_an_hour_already_started_is_finished(site):
    """The need shrinks as the packs fill, so the hour we picked can stop
    qualifying half-way through it. Without the latch the packs stop
    mid-charge and the rest of a cheap hour is thrown away."""
    system = site(at=NOON + timedelta(hours=2), soc=(20.0, 20.0))

    await system.coordinator._async_tick(None)
    assert buying(system)  # two hours wanted: 14:00 and the cheaper 15:00

    # ten minutes on the packs are most of the way there, one hour would now
    # do, and 14:00 is not the cheaper of the two - but it is the one we are in
    move_to(system, NOON + timedelta(hours=2, minutes=10), soc=60.0)
    await system.coordinator._async_tick(None)

    assert buying(system)


async def test_the_latch_does_not_survive_into_the_next_hour(site):
    """It finishes the hour it started, it does not adopt the next one."""
    system = site(at=NOON + timedelta(hours=3), soc=(20.0, 20.0))
    await system.coordinator._async_tick(None)
    assert buying(system)

    move_to(system, NOON + timedelta(hours=4))  # nothing after 16:00 qualifies
    await system.coordinator._async_tick(None)

    assert not buying(system)


async def test_without_a_measured_charge_time_it_behaves_as_before(site):
    """No empty-to-full time, no way to know the need - so every cheap hour
    qualifies, exactly as it did before this existed."""
    system = site(minutes=0.0, soc=(20.0, 20.0))

    await system.coordinator._async_tick(None)

    assert buying(system)


def test_the_need_is_the_slowest_pack_not_the_sum(site):
    """They charge in parallel: two packs at 20 % want the same time as one."""
    both = site(soc=(20.0, 20.0))
    one = site(soc=(20.0, None))

    assert both.coordinator.hours_of_charge_needed() == pytest.approx(
        one.coordinator.hours_of_charge_needed()
    )


def test_the_need_is_measured_against_the_ceiling_we_would_buy_to(site):
    """Not against the pack's own charge limit: buying stops at the ceiling,
    so counting up to 100 % would book hours nothing is going to spend."""
    system = site(soc=(20.0, 20.0), **{CONF_CHARGE_BELOW_SOC: 50})

    # 30 points to go, at 50 points an hour
    assert system.coordinator.hours_of_charge_needed() == pytest.approx(0.6)


def test_a_full_pack_needs_nothing(site):
    system = site(soc=(100.0, 100.0))

    assert system.coordinator.hours_of_charge_needed() == 0.0

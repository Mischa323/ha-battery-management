"""Holding for a cheaper window, judged on what is actually to be bought.

The night of 29 to 30 September: three lone quarters bought at 7 kW, at
EUR 0.24, with the packs at 57 % - while the afternoon after the morning peak
dipped to 0.17. The hold compared the cheapest *five hours* either side of the
peak (0.249 against 0.229, two cents, under the margin), when only three
quarters were wanted, and for three quarters the difference was seven cents.
So the comparison now runs over the need, capped at `cheap_hours`.
"""
from __future__ import annotations

from datetime import timedelta

from custom_components.battery_management import coordinator as coordinator_module
from custom_components.battery_management.const import (
    CONF_CHEAP_HOURS,
    POLICY_CHEAPER_LATER,
    POLICY_DYNAMIC_CHARGE,
)
from tests.conftest import GRID_SENSOR

from .test_plan import NOW, PRICES, planned  # noqa: F401

MIDNIGHT = NOW.replace(hour=0, minute=0, second=0, microsecond=0)
ONE_AM = MIDNIGHT + timedelta(hours=1)


def night_before_a_peak() -> list[dict]:
    """01:00 at 0.239 and the rest of the night at 0.245; the morning peak at
    0.42; one hour at 0.17 at noon, and 0.26 around it."""
    slots = []
    for i in range(48):
        start = MIDNIGHT + timedelta(hours=i)
        hour = start.hour if start.day == MIDNIGHT.day else None
        if hour is None:
            price = 0.30
        elif hour == 1:
            price = 0.239
        elif hour < 6:
            price = 0.245
        elif hour < 9:
            price = 0.42
        elif hour == 12:
            price = 0.17
        else:
            price = 0.26
        slots.append({"start": start.isoformat(),
                      "end": (start + timedelta(hours=1)).isoformat(),
                      "value": price})
    return slots


def at_one_am(planned, monkeypatch, soc):
    system = planned(remaining=0.0, soc=soc, **{CONF_CHEAP_HOURS: 5})
    # after the fixture, which sets its own noon
    monkeypatch.setattr(coordinator_module.dt_util, "utcnow", lambda: ONE_AM)
    system.hass.states.set(PRICES, 0.239, {"raw_today": night_before_a_peak()})
    system.hass.states.set(GRID_SENSOR, 300)
    system.coordinator.buy_ceiling_min = 50.0
    return system


async def test_a_small_need_waits_for_the_dip_after_the_peak(planned, monkeypatch):
    """At 90 % the packs need twelve minutes: the cheapest hour before the
    peak is 0.239 and after it 0.17 - seven cents, so it waits. Over five
    hours either side it was 0.244 against 0.242, and it bought."""
    system = at_one_am(planned, monkeypatch, soc=(90.0, 90.0))

    assert system.coordinator._buy_before() == MIDNIGHT + timedelta(hours=6)
    assert system.coordinator._buy_ceiling() == (0.0, POLICY_CHEAPER_LATER)

    await system.coordinator._async_tick(None)
    assert system.coordinator.active_policy != POLICY_DYNAMIC_CHARGE


async def test_a_large_need_still_buys_before_the_peak(planned, monkeypatch):
    """Nearly empty, the packs need 1.9 hours - two, as the hours are ranked -
    and one cheap hour at noon cannot hold that: the cheapest two either side
    are 0.242 against 0.215, under the margin, so the night buys."""
    system = at_one_am(planned, monkeypatch, soc=(5.0, 5.0))

    ceiling, reason = system.coordinator._buy_ceiling()

    assert reason != POLICY_CHEAPER_LATER
    assert ceiling > 50.0


def test_the_comparison_never_exceeds_cheap_hours(planned, monkeypatch):
    """Nearly two hours wanted, ranked over one: one it is."""
    system = at_one_am(planned, monkeypatch, soc=(5.0, 5.0))
    system.coordinator._cheap_hours = 1.0

    assert system.coordinator._hours_to_compare(100.0) == 1.0


def test_with_nothing_to_buy_it_compares_one_slot(planned, monkeypatch):
    """Not five hours: a pack one point inside the band read as "nothing
    needed" and compared five hours, one point lower it compared a quarter -
    and the verdict flipped with every percent the house drew (1 October)."""
    system = at_one_am(planned, monkeypatch, soc=(100.0, 100.0))

    assert system.coordinator._hours_to_compare(100.0) == 0.25


def test_a_percent_either_side_of_the_band_compares_the_same(planned, monkeypatch):
    low = at_one_am(planned, monkeypatch, soc=(85.0, 85.0)).coordinator._hours_to_compare(88.0)
    high = at_one_am(planned, monkeypatch, soc=(87.0, 87.0)).coordinator._hours_to_compare(88.0)

    assert low == high == 0.25

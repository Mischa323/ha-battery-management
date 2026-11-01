""""Minimaal laden" is binding, and it is a level.

Asked for on 19 October: the owner would rather have the packs at their
minimum, a few cents a kWh notwithstanding, than have the margin decide that
topping up does not pay. Up to it no margin is asked - only the cheapest slots
before the coming peak, as many as it takes - and above it the margin decides
as before. On 21 October it was split from the day goal (`test_day_goal.py`):
this one is the fixed one, that one looks at prices. Aimed at the peak since 1
November, after aiming at midnight had it buying at night.
"""
from __future__ import annotations

from datetime import timedelta

import pytest

from custom_components.battery_management import coordinator as coordinator_module
from custom_components.battery_management.const import POLICY_DYNAMIC_CHARGE, TRADE_OFF

from .conftest import GRID_SENSOR
from .test_trade_fill import afternoon, prices  # noqa: F401
from .test_trade_sell import trading  # noqa: F401

#: 18 October in miniature: nothing pays - 13:00-15:00 at 0.338, 0.39 otherwise
DEAR_DAY = dict(cheap=(13, 14), cheap_at=0.338, peak=(), ordinary=0.39)


@pytest.fixture
def day(afternoon, monkeypatch):
    def _build(*, soc=30.0, floor=70.0, **today):
        system = afternoon(soc=(soc, soc), **(today or DEAR_DAY))
        co = system.coordinator
        co.trade_mode = TRADE_OFF
        co._battery_price = 8500.0
        co._battery_cycles = 10000
        co._price_margin = 0.03
        co.buy_ceiling_min = floor
        monkeypatch.setattr(co, "_solar_headroom_ceiling", lambda: 95.0)
        return system

    return _build


def bought(system):
    return [h for h in system.coordinator.plan()["hours"] if h["buy"]]


def test_a_day_where_nothing_pays_still_fills_to_the_minimum(day):
    """30 % to 70 % is 1.6 hours at full power: the two cheapest hours."""
    picked = bought(day())

    assert len(picked) == 2
    assert {h["price"] for h in picked} == {0.338}


def test_without_a_minimum_nothing_is_bought(day):
    assert bought(day(floor=0.0)) == []


def test_above_the_minimum_the_margin_decides(day):
    assert bought(day(soc=75.0)) == []


def evening(day, monkeypatch, hour, soc=20.0):
    """Today's peak 18:00-22:00 at 0.45, 22:00-24:00 at 0.40; tomorrow the
    same peak, with 12:00-14:00 at 0.30 before it."""
    tomorrow = {24 + h: 0.30 for h in (12, 13)} | {24 + h: 0.45 for h in (18, 19, 20, 21)}
    system = day(soc=soc, floor=50.0, cheap=(), peak=(18, 19, 20, 21), peak_at=0.45,
                 ordinary=0.39, overrides={22: 0.40, 23: 0.40, **tomorrow})
    moment = coordinator_module.dt_util.utcnow().replace(hour=hour, minute=30)
    monkeypatch.setattr(coordinator_module.dt_util, "utcnow", lambda: moment)
    co = system.coordinator
    return co._floor_slots(co._price_forecast(), moment), moment


def test_only_before_the_coming_peak(day):
    """22:00 at 0.10 is cheaper, but it comes after the peak the minimum is for."""
    system = day(cheap=(13,), cheap_at=0.338, peak=(18, 19, 20, 21), peak_at=0.45,
                 ordinary=0.39, overrides={22: 0.10, 23: 0.10})
    co = system.coordinator
    now = coordinator_module.dt_util.utcnow()

    slots = co._floor_slots(co._price_forecast(), now)
    assert slots
    assert all(s.end <= now.replace(hour=18) for s in slots)


def test_after_the_peak_not_tonight_but_before_tomorrow_s(day, monkeypatch):
    """1 November: aimed at midnight, it bought at 0.42-0.43 after the peak,
    for the house to draw back out overnight. After the peak the next one is
    tomorrow's, and the cheapest slots before it are tomorrow's noon."""
    slots, moment = evening(day, monkeypatch, hour=22)

    assert slots
    assert all(s.start.date() > moment.date() for s in slots)
    assert {s.price for s in slots} == {0.30}


def test_not_in_the_peak_it_is_meant_for(day, monkeypatch):
    """Inside the peak, "before the next dear slot" would be the slot it is
    in. The stretch under way is skipped: nothing at 0.45."""
    slots, moment = evening(day, monkeypatch, hour=19)

    assert slots
    assert all(s.price < 0.45 for s in slots)
    assert all(s.start.date() > moment.date() for s in slots)


def test_the_plan_says_how_full_and_when(day):
    floor = day().coordinator.plan()["floor"]

    assert floor["to"] == 70.0
    assert len(floor["hours"]) == 2


def test_no_minimum_no_block(day):
    assert day(floor=0.0).coordinator.plan()["floor"] is None


async def test_the_tick_buys_to_the_minimum_and_stops_there(day, monkeypatch):
    system = day(soc=60.0)
    co = system.coordinator
    first = co._floor_slots(co._price_forecast(), coordinator_module.dt_util.utcnow())[0]
    at = first.start + timedelta(minutes=1)
    monkeypatch.setattr(coordinator_module.dt_util, "utcnow", lambda: at)
    system.hass.states.set(GRID_SENSOR, 300)

    await co._async_tick(None)
    assert co.active_policy == POLICY_DYNAMIC_CHARGE
    assert co.tick_log[-1]["buy_why"] == "minimum"
    assert "tot het minimum van 70 %" in co.tick_log[-1]["doing"]

    system.hass.states.set(system.soc(0), 70.0)
    system.hass.states.set(system.soc(1), 70.0)
    await co._async_tick(None)
    assert co.active_policy != POLICY_DYNAMIC_CHARGE

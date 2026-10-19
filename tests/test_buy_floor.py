""""Buy at least" is binding.

Asked for on 19 October: the owner would rather have the packs at their floor
every evening, a few cents a kWh notwithstanding, than have the margin decide
that topping up does not pay. Up to the floor no margin is asked - only the
cheapest slots before the end of the day, as many as it takes - and above it
the margin decides as before.
"""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from custom_components.battery_management import coordinator as coordinator_module
from custom_components.battery_management.const import POLICY_DYNAMIC_CHARGE, TRADE_OFF

from .conftest import GRID_SENSOR
from .test_trade_fill import afternoon, prices  # noqa: F401
from .test_trade_sell import trading  # noqa: F401

#: 18 October in miniature: nothing pays - 13:00 at 0.338, 0.39 otherwise
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


def test_a_day_where_nothing_pays_still_fills_to_the_floor(day):
    """30 % to 70 % is 1.6 hours at full power: the two cheapest hours."""
    picked = bought(day())

    assert len(picked) == 2
    assert {h["price"] for h in picked} == {0.338}


def test_without_a_floor_nothing_is_bought(day):
    assert bought(day(floor=0.0)) == []


def test_above_the_floor_the_margin_decides(day):
    assert bought(day(soc=75.0)) == []


def test_only_before_the_end_of_the_day(day):
    """Tomorrow's cheap hours do not count towards today's floor."""
    system = day(cheap=(), cheap_at=0.39, peak=(), ordinary=0.39, tomorrow=0.10)

    slots = system.coordinator._floor_slots(
        system.coordinator._price_forecast(), coordinator_module.dt_util.utcnow()
    )
    assert slots
    assert all(s.start.date() == coordinator_module.dt_util.utcnow().date() for s in slots)


def test_the_plan_says_how_full_and_when(day):
    floor = day().coordinator.plan()["floor"]

    assert floor["to"] == 70.0
    assert len(floor["hours"]) == 2


def test_no_floor_no_block(day):
    assert day(floor=0.0).coordinator.plan()["floor"] is None


async def test_the_tick_buys_to_the_floor_and_stops_there(day, monkeypatch):
    system = day(soc=60.0)
    co = system.coordinator
    first = co._floor_slots(co._price_forecast(), coordinator_module.dt_util.utcnow())[0]
    at = first.start + timedelta(minutes=1)
    monkeypatch.setattr(coordinator_module.dt_util, "utcnow", lambda: at)
    system.hass.states.set(GRID_SENSOR, 300)

    await co._async_tick(None)
    assert co.active_policy == POLICY_DYNAMIC_CHARGE

    system.hass.states.set(system.soc(0), 70.0)
    system.hass.states.set(system.soc(1), 70.0)
    await co._async_tick(None)
    assert co.active_policy != POLICY_DYNAMIC_CHARGE

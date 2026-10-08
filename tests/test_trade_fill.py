"""Filling up for tonight's sale, when that pays.

Reported on 8 October: the packs bought to 70 % - "cheaper tomorrow" stops at
the floor - with the sell line at 70 % too, so the plan showed a sale with
nothing above the line to sell. With trading on, a kilowatt hour bought in a
cheap quarter before the peak is worth buying when the evening pays more for
it than it cost, after the round trip and the wear, by the owner's margin.

Prices below: 13:00-17:00 at 0.21, the 18:00-22:00 peak at 0.45 (an export
value of 0.42), 0.30 otherwise. Wear on a EUR 5000, 6000-cycle, 28 kWh bank is
~0.03, so a 0.21 kWh sold at 0.42 clears 0.42 - 0.21/0.88 - 0.03 = 0.15.
"""
from __future__ import annotations

from datetime import timedelta

import pytest

from custom_components.battery_management import coordinator as coordinator_module
from custom_components.battery_management.const import (
    CONF_CHEAP_HOURS,
    CONF_EXPENSIVE_HOURS,
    POLICY_DYNAMIC_CHARGE,
    POLICY_TRADE_FILL,
    TRADE_OFF,
    TRADE_SHADOW,
)

from .conftest import GRID_SENSOR
from .test_trade_sell import ENERGY_TAX, MARKUP_AND_VAT, NOW, PRICE_SENSOR, trading  # noqa: F401

DAY = NOW.replace(hour=0)
AFTERNOON = DAY.replace(hour=13)


def prices(*, cheap=(13, 14, 15, 16), cheap_at=0.21, peak=(18, 19, 20, 21),
           peak_at=0.45, ordinary=0.30, tomorrow=None, overrides=None) -> dict:
    """Two days of hours, with the side lists the supplier sends."""
    allin, market, untaxed = [], [], []
    for i in range(48):
        start = DAY + timedelta(hours=i)
        if i >= 24 and tomorrow is not None:
            value = tomorrow
        elif i < 24 and i in cheap:
            value = cheap_at
        elif i < 24 and i in peak:
            value = peak_at
        else:
            value = ordinary
        value = (overrides or {}).get(i, value)
        row = {"start": start.isoformat(), "end": (start + timedelta(hours=1)).isoformat()}
        allin.append({**row, "value": value})
        market.append({**row, "value": round(value - ENERGY_TAX - MARKUP_AND_VAT, 4)})
        untaxed.append({**row, "value": round(value - ENERGY_TAX, 4)})
    return {"raw_today": allin, "market_prices": market, "untaxed_prices": untaxed}


@pytest.fixture
def afternoon(trading, monkeypatch):
    """13:00, both packs at the 70 % sell line, trading on, five cheap hours."""

    def _build(*, soc=(70.0, 70.0), floor=70, **day):
        system = trading(soc=soc, **{CONF_CHEAP_HOURS: 5, CONF_EXPENSIVE_HOURS: 4})
        monkeypatch.setattr(coordinator_module.dt_util, "utcnow", lambda: AFTERNOON)
        system.hass.states.set(GRID_SENSOR, 300)
        system.hass.states.set(PRICE_SENSOR, 0.21, prices(**day))
        system.coordinator.sell_floor = floor
        return system

    return _build


def test_it_fills_up_when_the_evening_pays(afternoon):
    system = afternoon()

    assert system.coordinator.trade_fill_ceiling() == 100.0
    assert system.coordinator._buy_ceiling() == (100.0, POLICY_TRADE_FILL)


def test_not_when_the_evening_does_not_pay_enough(afternoon):
    """A 0.32 peak exports at 0.29: 0.29 - 0.21/0.88 - 0.03 is two cents,
    under the five the owner asks for."""
    system = afternoon(peak_at=0.32)

    assert system.coordinator.trade_fill_ceiling() is None
    assert system.coordinator._buy_ceiling()[1] != POLICY_TRADE_FILL


@pytest.mark.parametrize("mode", [TRADE_OFF, TRADE_SHADOW])
def test_only_with_trading_on(afternoon, mode):
    """Shadow sells nothing; a purchase for its pretend sale is real money."""
    system = afternoon()
    system.coordinator.trade_mode = mode

    assert system.coordinator.trade_fill_ceiling() is None


def test_no_further_than_the_evening_can_sell(afternoon):
    """One hour of the evening pays: 7 kWh at the packs' full power, so 25 %
    of 28 kWh on top of the line, not the whole way to 100 %."""
    system = afternoon(peak=(18,))

    assert system.coordinator.trade_fill_ceiling() == 95.0


def test_what_is_above_the_line_already_goes_first(afternoon):
    """At 80 % there are 2.8 kWh to sell anyway; the one paying hour has room
    for 4.2 more, so 15 % to buy."""
    system = afternoon(soc=(80.0, 80.0), peak=(18,))

    assert system.coordinator.trade_fill_ceiling() == 95.0


def test_no_further_than_the_cheap_hours_can_buy(afternoon):
    """Only 13:00 is cheap enough; at 0.40 the rest of the afternoon costs
    more than the evening returns."""
    system = afternoon(cheap=(13,), overrides={14: 0.40, 15: 0.40, 16: 0.40})

    assert system.coordinator.trade_fill_ceiling() == 95.0


def test_never_in_an_hour_that_is_not_cheap(afternoon):
    """The arithmetic would buy at 0.40 to sell at 0.60 - but 0.40 is not a
    cheap hour, and charging for the evening means charging in the cheap
    ones. Here the only hours left before the peak are dear."""
    system = afternoon(cheap=(), overrides={h: 0.40 for h in range(13, 18)}, peak_at=0.65)

    assert system.coordinator.trade_fill_ceiling() is None


def test_never_past_the_owner_s_ceiling(afternoon):
    system = afternoon()
    system.coordinator.buy_ceiling_max = 90

    assert system.coordinator.trade_fill_ceiling() == 90


def test_the_sun_keeps_its_room(afternoon, monkeypatch):
    """What the sun will fill is free; buying it exports the sun instead."""
    system = afternoon()
    monkeypatch.setattr(system.coordinator, "_solar_headroom_ceiling", lambda: 85.0)

    assert system.coordinator.trade_fill_ceiling() == 85.0


def test_a_cheaper_tomorrow_does_not_hold_back_tonight_s_sale(afternoon):
    """Tomorrow's prices answer for the house's energy, not for a kilowatt
    hour bought to be sold tonight."""
    system = afternoon(tomorrow=0.05)
    system.coordinator.buy_ceiling_min = 50

    assert system.coordinator._buy_ceiling() == (100.0, POLICY_TRADE_FILL)


def test_a_cheaper_window_after_the_peak_does_not_hold_it_back_either(afternoon):
    """22:00 at 0.10 is a reason to keep the house's buying for later - not
    the energy sold at 18:00, which has to be in the packs before then."""
    system = afternoon(overrides={22: 0.10, 23: 0.10})

    assert system.coordinator._buy_ceiling() == (100.0, POLICY_TRADE_FILL)


async def test_the_tick_buys_for_it(afternoon):
    system = afternoon()

    await system.coordinator._async_tick(None)

    assert system.coordinator.active_policy == POLICY_DYNAMIC_CHARGE
    assert system.coordinator.setpoint < 0


def test_the_plan_says_how_full(afternoon):
    system = afternoon()

    assert system.coordinator.plan()["trade"]["fill_to"] == 100.0


def test_full_packs_need_nothing(afternoon):
    system = afternoon(soc=(100.0, 100.0))

    assert system.coordinator.trade_fill_ceiling() is None


async def test_worked_out_once_a_tick_and_afresh_the_next(afternoon, monkeypatch):
    """Asked five times a tick, and as dear as the rest of the tick: kept for
    the tick, never past it."""
    system = afternoon()
    co = system.coordinator
    calls = []
    real = co._trade_fill_ceiling
    monkeypatch.setattr(co, "_trade_fill_ceiling", lambda: calls.append(1) or real())

    await co._async_tick(None)
    assert len(calls) == 1

    system.hass.states.set(system.soc(0), 100.0)
    system.hass.states.set(system.soc(1), 100.0)
    assert co.trade_fill_ceiling() is None

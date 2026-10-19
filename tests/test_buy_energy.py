"""The margin weighs energy, not hours.

Reported on 19 October: the packs at 5 % from 07:39, a four-hour evening peak
at about 0.46, an afternoon at about 0.30 - and nothing bought all day. Since
the round trip and the wear are counted (18 October), the margin was measured
against one reference for every slot: the weakest of the `cheap_hours`
dearest hours. With five cheap hours and a four-hour peak that is the fifth
"dear" hour - an ordinary one - and 0.30 / 0.88 plus the wear plus the margin
does not beat an ordinary hour.

What a slot of buying is worth is what its energy replaces. One quarter at
7 kW is 1.75 kWh; a house drawing 1 kW uses that up over seven quarters,
dearest first. So the cheapest quarter is set against the mean of the seven
dearest, the next against the seven after those, and buying stops where the
energy would only replace hours it does not beat.
"""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from custom_components.battery_management import coordinator as coordinator_module
from custom_components.battery_management.const import TRADE_OFF
from custom_components.battery_management.prices import _block_mean

from .conftest import GRID_SENSOR
from .test_trade_fill import AFTERNOON, DAY, PRICE_SENSOR, afternoon  # noqa: F401
from .test_trade_sell import ENERGY_TAX, MARKUP_AND_VAT, trading  # noqa: F401


def quarters(by_hour: dict, ordinary: float) -> dict:
    """Two days of quarter-hourly prices: `by_hour` {hour: price} today."""
    allin, market, untaxed = [], [], []
    for i in range(192):
        start = DAY + timedelta(minutes=15 * i)
        value = by_hour.get(i // 4, ordinary) if i < 96 else ordinary
        row = {"start": start.isoformat(), "end": (start + timedelta(minutes=15)).isoformat()}
        allin.append({**row, "value": value})
        market.append({**row, "value": round(value - ENERGY_TAX - MARKUP_AND_VAT, 4)})
        untaxed.append({**row, "value": round(value - ENERGY_TAX, 4)})
    return {"raw_today": allin, "market_prices": market, "untaxed_prices": untaxed}


#: 19 October in miniature: an afternoon at 0.30, 17:00-21:00 at 0.46, 0.38 else
OCT_19 = {13: 0.30, 14: 0.30, 15: 0.30, 16: 0.30, 17: 0.46, 18: 0.46, 19: 0.46, 20: 0.46}


@pytest.fixture
def day(afternoon, monkeypatch):
    def _build(by_hour=OCT_19, ordinary=0.38, draw=1000.0, soc=5.0):
        system = afternoon(soc=(soc, soc))
        co = system.coordinator
        system.hass.states.set(PRICE_SENSOR, 0.30, quarters(by_hour, ordinary))
        co.trade_mode = TRADE_OFF
        co._battery_price = 8500.0
        co._battery_cycles = 10000
        co._price_margin = 0.03
        co._house_draw_w = draw
        monkeypatch.setattr(co, "_solar_headroom_ceiling", lambda: 95.0)
        return system

    return _build


def bought(system):
    return [h for h in system.coordinator.plan()["hours"] if h["buy"]]


def test_an_empty_bank_before_a_dear_evening_buys(day):
    """1 kW over a 4 h peak is 4 kWh; at 1.75 kWh a quarter that is a bit over
    two quarters of buying, and a third paired partly with the peak."""
    picked = bought(day())

    assert 2 <= len(picked) <= 3
    assert all(h["price"] == 0.30 for h in picked)


def test_a_hungrier_house_buys_more(day):
    """At 2 kW the peak takes 8 kWh: four or five quarters."""
    assert len(bought(day(draw=2000.0))) > len(bought(day(draw=1000.0)))


def test_what_the_energy_would_only_replace_ordinary_hours_with_is_not_bought(day):
    """0.30 costs 0.371 once used, plus three cents: 0.401 - more than the
    0.38 of the ordinary hours past the peak. However empty the packs."""
    picked = bought(day(draw=500.0))

    assert 1 <= len(picked) <= 2


def test_a_single_spike_does_not_carry_the_afternoon(day):
    """One quarter at 0.90 among 0.30s: it pays for the energy that covers it,
    not for an afternoon of buying."""
    spike = {13: 0.24, 14: 0.24, 15: 0.24, 16: 0.24}
    system = day(by_hour=spike, ordinary=0.30)
    attributes = quarters(spike, 0.30)
    attributes["raw_today"][18 * 4]["value"] = 0.90
    system.hass.states.set(PRICE_SENSOR, 0.24, attributes)

    assert len(bought(system)) <= 1


def test_18_october_is_still_refused(day):
    """0.338 against 0.39 all evening: 0.414 once used - not worth it."""
    assert bought(day(by_hour={h: 0.338 for h in (13, 14, 15, 16)}, ordinary=0.39)) == []


def test_unmeasured_draw_keeps_the_old_single_reference(day):
    system = day(draw=None)

    assert system.coordinator._buy_spread() is None


def test_the_spread_is_pack_power_over_the_house(day):
    assert day(draw=700.0).coordinator._buy_spread() == pytest.approx(10.0)


async def test_the_tick_buys_in_the_first_of_them(day, monkeypatch):
    system = day()
    first = bought(system)[0]
    at = datetime.fromisoformat(first["start"]) + timedelta(minutes=1)
    monkeypatch.setattr(coordinator_module.dt_util, "utcnow", lambda: at)
    system.hass.states.set(GRID_SENSOR, 300)

    await system.coordinator._async_tick(None)

    assert system.coordinator.active_policy == "dynamic_charge"


# -- the block mean --------------------------------------------------------


def test_a_whole_block_is_its_mean():
    assert _block_mean([0.5, 0.4, 0.3, 0.2], 0, 2) == pytest.approx(0.45)


def test_a_fractional_block_weighs_by_overlap():
    """[0.5, 2.0): half of 0.5 and all of 0.4 -> (0.25 + 0.4) / 1.5."""
    assert _block_mean([0.5, 0.4, 0.3], 0.5, 2.0) == pytest.approx(0.65 / 1.5)


def test_a_block_past_the_end_replaces_nothing():
    assert _block_mean([0.5, 0.4], 1.0, 3.0) is None

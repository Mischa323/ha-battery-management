"""Selling in the best quarters, as many as the packs can fill.

Reported on 28 September: the plan listed the whole evening, the packs were
at their sell line after three quarters, and the owner asked for it to show
"alleen de tijden wanneer hij gaat verkopen, net als het opladen". So the
slots are chosen the way buying chooses its hours - of those that pay, the
best first, until what sits above the sell line is sold at full power - and
the tick sells only in those, so the best of the evening gets the energy
instead of the first quarter that clears the threshold.
"""
from __future__ import annotations

from datetime import timedelta

import pytest

from custom_components.battery_management import coordinator as coordinator_module
from custom_components.battery_management.const import TRADE_SHADOW

from .conftest import GRID_SENSOR
from .test_trade_sell import ENERGY_TAX, MARKUP_AND_VAT, NOW, PRICE_SENSOR, trading  # noqa: F401


def key(at):
    return at.isoformat()


def prices(peaks: dict, ordinary: float = 0.30, refill_at=NOW + timedelta(hours=9)) -> dict:
    """Two days of hours: `peaks` {datetime: price}, a 0.15 bargain to buy
    back in at `refill_at`, and `ordinary` otherwise."""
    midnight = NOW.replace(hour=0)
    allin, market, untaxed = [], [], []
    for i in range(48):
        start = midnight + timedelta(hours=i)
        value = peaks.get(start, 0.15 if start == refill_at else ordinary)
        row = {"start": start.isoformat(), "end": (start + timedelta(hours=1)).isoformat()}
        allin.append({**row, "value": value})
        market.append({**row, "value": round(value - ENERGY_TAX - MARKUP_AND_VAT, 4)})
        untaxed.append({**row, "value": round(value - ENERGY_TAX, 4)})
    return {"raw_today": allin, "market_prices": market, "untaxed_prices": untaxed}


@pytest.fixture
def evening(trading):
    def _build(peaks, *, soc=(80.0, 80.0), floor=30, ordinary=0.30,
               refill_at=NOW + timedelta(hours=9), **options):
        system = trading(soc=soc, **options)
        system.hass.states.set(
            PRICE_SENSOR, 0.5, prices(peaks, ordinary=ordinary, refill_at=refill_at)
        )
        system.coordinator.sell_floor = floor
        return system

    return _build


def planned(system):
    return set(system.coordinator.planned_sell_slots(system.coordinator._price_forecast()))


def later(system, monkeypatch, at):
    """Move the clock, and the meter with it - a reading left behind would be
    stale, and a stale meter rightly stops the packs."""
    monkeypatch.setattr(coordinator_module.dt_util, "utcnow", lambda: at)
    system.hass.states.set(GRID_SENSOR, 300)


def test_only_as_many_hours_as_the_packs_can_fill(evening):
    """Each pack 50 % (7 kWh) above the line at 3.5 kW is two hours: the 0.55
    peak and the best of the ordinary ones, not every hour that clears five
    cents."""
    system = evening({NOW: 0.55})

    assert planned(system) == {key(NOW), key(NOW + timedelta(hours=1))}
    assert len(system.coordinator.sell_forecast()) > 2


def test_the_best_hours_get_the_energy(evening):
    """Half an hour's worth, and a dearer hour at 21:00: that one, not now."""
    system = evening({NOW: 0.40, NOW + timedelta(hours=3): 0.60}, soc=(75.0, 75.0), floor=60)

    assert planned(system) == {key(NOW + timedelta(hours=3))}


async def test_the_tick_waits_for_the_better_hour(evening):
    system = evening({NOW: 0.40, NOW + timedelta(hours=3): 0.60}, soc=(75.0, 75.0), floor=60)

    await system.coordinator._async_tick(None)

    assert system.coordinator.trade_selling is False
    assert system.coordinator.last_trade_verdict["why"] == "later"
    assert system.coordinator.last_trade_verdict["margin"] >= 0.05


async def test_and_sells_when_it_comes(evening, monkeypatch):
    system = evening({NOW: 0.40, NOW + timedelta(hours=3): 0.60}, soc=(75.0, 75.0), floor=60)
    later(system, monkeypatch, NOW + timedelta(hours=3, minutes=5))

    await system.coordinator._async_tick(None)

    assert system.coordinator.trade_selling is True


def test_the_plan_shows_only_those_hours(evening):
    system = evening({NOW: 0.55})

    selling = [h["start"] for h in system.coordinator.plan()["hours"] if h["sell"]]

    assert selling == [key(NOW), key(NOW + timedelta(hours=1))]
    assert [h["start"] for h in system.coordinator.plan()["trade"]["sell_hours"]] == selling


def test_the_emptier_pack_decides(evening):
    """One pack at its line, the other 10 % above it: selling stops the moment
    it starts, so nothing is planned - the total above the line is not what
    can be sold."""
    assert planned(evening({NOW: 0.55}, soc=(70.0, 80.0), floor=70)) == set()


def test_the_fuller_pack_does_not_stretch_the_sale(evening):
    """92 % and 73 % over a 70 % line: the 73 % pack lasts 0.12 h, so one
    hour - the peak - however much the other still holds."""
    assert planned(evening({NOW: 0.55}, soc=(92.0, 73.0), floor=70)) == {key(NOW)}


def test_a_sale_the_tick_would_not_start_is_not_planned(evening):
    """8 October: both packs at 72 % over a 70 % line. The tick starts a sale
    only with more than the band above the line, so the plan must not show
    one either - it did, and the evening's sale never came."""
    assert planned(evening({NOW: 0.55}, soc=(72.0, 72.0), floor=70)) == set()


def test_not_held_for_tomorrow_evening(evening):
    """Tomorrow night pays more, but the house draws tonight's charge long
    before it comes: the choice looks no further than the next midday."""
    system = evening({NOW: 0.45, NOW + timedelta(hours=24): 0.90}, soc=(75.0, 75.0), floor=60)

    assert planned(system) == {key(NOW)}


def test_shadow_counts_what_it_has_already_sold(evening):
    system = evening({NOW: 0.55}, trade=TRADE_SHADOW)
    system.coordinator._shadow_sold_kwh = 10.0

    # 10 of 28 kWh is 36 % gone: 14 % left per pack is 0.56 h - the peak alone
    assert planned(system) == {key(NOW)}


def test_the_hour_under_way_counts_only_what_is_left_of_it(evening, monkeypatch):
    """At 18:45 the peak hour has a quarter left, so the 1.5 hours asked for
    need two more hours after it."""
    system = evening({NOW: 0.55}, soc=(67.5, 67.5), floor=30)
    later(system, monkeypatch, NOW + timedelta(minutes=45))

    assert len(planned(system)) == 3


async def test_a_sale_carries_on_across_a_quarter_boundary(evening, monkeypatch):
    """Started in one slot, running on in the next with a point to go: the
    band is for starting, not for every new slot."""
    system = evening({NOW: 0.55}, soc=(80.0, 80.0), floor=30)
    await system.coordinator._async_tick(None)
    assert system.coordinator.trade_selling is True

    later(system, monkeypatch, NOW + timedelta(hours=1, minutes=1))
    system.hass.states.set(system.soc(0), 31.0)
    system.hass.states.set(system.soc(1), 31.0)
    await system.coordinator._async_tick(None)

    assert system.coordinator.trade_selling is True


# -- a dear tomorrow ------------------------------------------------------------


def test_a_dear_tomorrow_is_no_time_to_sell_tonight(evening):
    """Asked by the owner: "houdt hij er rekening mee dat als het morgen een
    stuk duurder is, hij dan niet verkoopt". He does, because a sale is
    weighed against buying it back in the cheapest hours *after* it - and when
    those are tomorrow's, at 0.42, a 0.45 evening earns 0.42 against a refill
    of 0.48: nothing to sell."""
    system = evening({NOW: 0.45}, ordinary=0.42, refill_at=None)

    assert key(NOW) not in system.coordinator.sell_forecast()
    assert planned(system) == set()


async def test_and_the_tick_does_not_sell_either(evening):
    system = evening({NOW: 0.45}, ordinary=0.42, refill_at=None)

    await system.coordinator._async_tick(None)

    assert system.coordinator.trade_selling is False
    assert system.coordinator.last_trade_verdict["why"] == "margin_too_small"


def test_the_same_evening_sells_when_tomorrow_is_cheap(evening):
    """The same 0.45 evening, with a 0.15 hour tonight to buy it back in."""
    system = evening({NOW: 0.45}, ordinary=0.42)

    assert key(NOW) in planned(system)

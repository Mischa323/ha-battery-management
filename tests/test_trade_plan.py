"""When it will sell, in the plan - asked for by the owner: "bij plan van
vandaag erbij zetten wanneer hij gaat ontladen voor slim handelen".

The forecast is the live decision asked of every slot ahead, so what is
pinned first is that the two agree; then what the plan may and may not
promise, and that what really happened is kept.
"""
from __future__ import annotations

import time
from datetime import timedelta

import pytest

from custom_components.battery_management.const import (
    CONF_CHARGE_BELOW_SOC,
    CONF_FEED_IN_BASIS,
    CONF_FEED_IN_FIXED,
    CONF_SALDERING_UNTIL,
    CONF_TRADE_MARGIN,
    MODE_GRID_ZERO,
    TRADE_OFF,
    TRADE_SHADOW,
)
from .test_trade_sell import NOW, trading  # noqa: F401


def hour_at(plan, at):
    return next(h for h in plan["hours"] if h["start"] == at.isoformat())


def key(at):
    return at.isoformat()


def test_the_peak_is_planned_as_a_sale(trading):
    """A ten-cent threshold: the 0.55 peak clears it by far, the ordinary
    0.30 hours (7 cents) do not."""
    system = trading(**{CONF_TRADE_MARGIN: 0.10})

    plan = system.coordinator.plan()

    assert hour_at(plan, NOW)["sell"] is True
    assert hour_at(plan, NOW + timedelta(hours=1))["sell"] is False
    assert [h["start"] for h in plan["trade"]["sell_hours"]] == [key(NOW)]


async def test_the_forecast_and_the_tick_agree_on_the_current_slot(trading):
    system = trading()

    margin = system.coordinator.sell_forecast()[key(NOW)]
    await system.coordinator._async_tick(None)

    assert margin == pytest.approx(system.coordinator.last_trade_verdict["margin"], abs=1e-4)
    assert system.coordinator.trade_selling is True


def test_the_refill_is_taken_from_after_each_slot(trading):
    """After tomorrow's 03:00 bargain the cheapest refill left is an ordinary
    hour, so the hours after it no longer pay even at a five-cent threshold."""
    system = trading()

    forecast = system.coordinator.sell_forecast()

    assert key(NOW + timedelta(hours=2)) in forecast
    assert key(NOW + timedelta(hours=12)) not in forecast



def test_a_slot_is_never_its_own_refill(trading):
    """Tomorrow's 03:00 bargain, on a fixed EUR 0.40: bought back from itself
    it would clear 20 cents, but a kWh cannot be sold and rebought in the same
    slot - after it only ordinary hours remain, and 0.40 - 0.34 - 0.03 is three
    cents."""
    system = trading(
        **{CONF_FEED_IN_BASIS: "fixed", CONF_FEED_IN_FIXED: 0.40,
           CONF_SALDERING_UNTIL: "2026-01-01"},
    )

    assert key(NOW + timedelta(hours=9)) not in system.coordinator.sell_forecast()

def test_saldering_is_taken_as_it_will_be_on_the_day(trading):
    """Tomorrow's small hours pay seven cents with the tax back and nothing
    without it - so if saldering ends tomorrow, they drop out."""
    at_night = NOW + timedelta(hours=7)   # 01:00 UTC, tomorrow in any zone
    tonight = NOW + timedelta(hours=2)    # 20:00 UTC, today in any zone

    lasting = trading().coordinator.sell_forecast()
    assert key(at_night) in lasting and key(tonight) in lasting

    ending = trading(**{CONF_SALDERING_UNTIL: "2026-08-06"}).coordinator.sell_forecast()
    assert key(tonight) in ending
    assert key(at_night) not in ending


def test_nothing_is_planned_while_trading_is_off(trading):
    system = trading(trade=TRADE_OFF)

    plan = system.coordinator.plan()

    assert not any(h["sell"] for h in plan["hours"])
    assert plan["trade"]["mode"] == TRADE_OFF
    assert plan["trade"]["sell_hours"] == []


def test_nor_outside_dynamic(trading):
    system = trading()
    system.coordinator.mode = MODE_GRID_ZERO

    assert system.coordinator.sell_forecast() == {}


def test_shadow_plans_what_it_would_sell(trading):
    system = trading(trade=TRADE_SHADOW)

    assert hour_at(system.coordinator.plan(), NOW)["sell"] is True


def test_a_buy_hour_is_never_planned_as_a_sale(trading):
    """A fixed rate above the cheapest hour would make both look right; the
    tick gives the hour to buying, so the plan must not promise a sale."""
    system = trading(
        soc=(35.0, 35.0),
        peak=0.02,
        **{CONF_FEED_IN_BASIS: "fixed", CONF_FEED_IN_FIXED: 0.40, CONF_CHARGE_BELOW_SOC: 40},
    )
    system.coordinator.sell_floor = 0

    assert key(NOW) in system.coordinator.sell_forecast()
    now_hour = hour_at(system.coordinator.plan(), NOW)
    assert now_hour["buy"] is True
    assert now_hour["sell"] is False


def test_the_plan_says_how_much_there_is_to_sell(trading):
    """Two 14 kWh packs at 80 %, selling down to 30 %: 7 kWh each."""
    system = trading(soc=(80.0, 80.0))
    system.coordinator.sell_floor = 30

    trade = system.coordinator.plan()["trade"]

    assert trade["above_floor_kwh"] == pytest.approx(14.0)
    assert trade["sell_floor"] == 30
    assert trade["min_margin_eur_kwh"] == 0.05


def test_the_reserve_counts_as_the_line_when_it_is_higher(trading):
    system = trading(soc=(80.0, 80.0))
    system.coordinator.sell_floor = 30
    system.coordinator.soc_reserve = 50

    assert system.coordinator.energy_above_sell_line() == pytest.approx(8.4)


async def test_a_sale_is_remembered_on_its_slot(trading):
    system = trading(trade=TRADE_SHADOW)

    await system.coordinator._async_tick(None)

    assert hour_at(system.coordinator.plan(), NOW)["sold"] is True
    assert hour_at(system.coordinator.plan(), NOW + timedelta(hours=1))["sold"] is False


async def test_and_survives_a_restart(trading):
    system = trading(trade=TRADE_SHADOW)
    await system.coordinator._async_tick(None)
    stored = system.coordinator._state_to_save()

    fresh = trading(trade=TRADE_OFF)
    fresh.coordinator._store.data = {**stored, "saved_at": time.time()}
    await fresh.coordinator._async_restore()

    assert fresh.coordinator.price_history[key(NOW)]["sold"] is True


async def test_no_sale_leaves_the_slot_unsold(trading):
    system = trading(trade=TRADE_OFF)

    await system.coordinator._async_tick(None)

    assert hour_at(system.coordinator.plan(), NOW)["sold"] is False


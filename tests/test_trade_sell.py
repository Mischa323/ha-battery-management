"""Selling to the grid when the evening pays more than the refill costs.

The mirror image of buying: a forced setpoint, decided per slot, and only ever
in Dynamic. What is worth selling is a comparison of three prices - what a
kilowatt hour earns now, what buying it back will cost at the cheapest hours
ahead (plus the round trip), and what the cycle wears off the packs - and the
comparison itself lives in `trading.py`. These tests are about the tick: that
it acts on that verdict, stops at the owner's floor, withholds the command in
shadow, and never sells without a purchase price to weigh it against.
"""
from __future__ import annotations

import time
from datetime import date, datetime, timedelta, timezone

import pytest

from custom_components.battery_management import coordinator as coordinator_module
from custom_components.battery_management.const import (
    CONF_BATTERY_PRICE,
    CONF_CHARGE_BELOW_SOC,
    CONF_CHEAP_HOURS,
    CONF_FEED_IN_BASIS,
    CONF_FEED_IN_FIXED,
    CONF_FULL_CHARGE_MINUTES,
    CONF_PRICE_SENSOR,
    CONF_SALDERING_UNTIL,
    FLOW_DISCHARGE,
    MODE_DYNAMIC,
    MODE_GRID_ZERO,
    POLICY_DYNAMIC_CHARGE,
    POLICY_GRID_ZERO,
    POLICY_TRADE_SELL,
    TRADE_OFF,
    TRADE_ON,
    TRADE_SHADOW,
)

PRICE_SENSOR = "sensor.energy_prices"

#: 18:00, the evening peak; the refill is tomorrow at 03:00
NOW = datetime(2026, 8, 5, 18, 0, tzinfo=timezone.utc)
REFILL_AT = datetime(2026, 8, 6, 3, 0, tzinfo=timezone.utc)

#: the parts of an all-in price, roughly as Frank Energie publishes them
ENERGY_TAX = 0.11
MARKUP_AND_VAT = 0.14 - ENERGY_TAX


def prices(peak: float, refill: float = 0.15, ordinary: float = 0.30) -> dict:
    """Two days of all-in prices, with the side lists the supplier sends too."""
    midnight = NOW.replace(hour=0)
    allin, market, untaxed = [], [], []
    for i in range(48):
        start = midnight + timedelta(hours=i)
        value = peak if start == NOW else refill if start == REFILL_AT else ordinary
        row = {"start": start.isoformat(), "end": (start + timedelta(hours=1)).isoformat()}
        allin.append({**row, "value": value})
        market.append({**row, "value": round(value - ENERGY_TAX - MARKUP_AND_VAT, 4)})
        untaxed.append({**row, "value": round(value - ENERGY_TAX, 4)})
    return {"raw_today": allin, "market_prices": market, "untaxed_prices": untaxed}


@pytest.fixture
def trading(build_system, monkeypatch):
    """Two 14 kWh packs in Dynamic, bought for EUR 5000, selling switched on."""

    def _build(
        *,
        soc=(80.0, 80.0),
        peak=0.55,
        trade=TRADE_ON,
        side_lists=True,
        **options,
    ):
        monkeypatch.setattr(coordinator_module.dt_util, "utcnow", lambda: NOW)
        system = build_system(
            grid=300,
            units=(("093", soc[0]), ("052", soc[1])),
            **{
                CONF_PRICE_SENSOR: PRICE_SENSOR,
                CONF_CHEAP_HOURS: 1,
                # 3.5 kW for four hours: 14 kWh a pack, 28 together
                CONF_FULL_CHARGE_MINUTES: 240,
                CONF_BATTERY_PRICE: 5000,
                **options,
            },
        )
        attributes = prices(peak)
        if not side_lists:
            attributes = {"raw_today": attributes["raw_today"]}
        system.hass.states.set(PRICE_SENSOR, peak, attributes)
        system.coordinator.mode = MODE_DYNAMIC
        system.coordinator.trade_mode = trade
        return system

    return _build


async def test_sells_everything_it_may_at_a_peak_worth_selling(trading):
    """0.52 earned (market 0.41 + tax back under saldering) against 0.17 to
    buy back and 0.03 of wear: both packs, flat out, whatever the house uses."""
    system = trading()

    await system.coordinator._async_tick(None)

    assert system.coordinator.active_policy == POLICY_TRADE_SELL
    assert system.coordinator.setpoint == 7000
    assert system.flows() == [FLOW_DISCHARGE, FLOW_DISCHARGE]
    verdict = system.coordinator.last_trade_verdict
    assert verdict["why"] is None
    assert verdict["value"] == pytest.approx(0.52)
    assert verdict["refill"] == pytest.approx(0.15)
    assert verdict["margin"] == pytest.approx(0.52 - 0.15 / 0.88 - 5000 / 6000 / 28)


async def test_shadow_decides_the_same_but_commands_nothing(trading):
    system = trading(trade=TRADE_SHADOW)

    await system.coordinator._async_tick(None)

    assert system.coordinator.trade_would_sell is True
    assert system.coordinator.trade_selling is False
    # grid-zero underneath, exactly as if trading were off
    assert system.coordinator.active_policy == POLICY_GRID_ZERO
    assert system.coordinator.setpoint == 300


async def test_off_does_not_even_weigh_it(trading):
    system = trading(trade=TRADE_OFF)

    await system.coordinator._async_tick(None)

    assert system.coordinator.last_trade_verdict is None
    assert system.coordinator.trade_would_sell is False
    assert system.coordinator.setpoint == 300


async def test_only_in_dynamic(trading):
    """The other modes are promises about the house; selling breaks them."""
    system = trading()
    system.coordinator.mode = MODE_GRID_ZERO

    await system.coordinator._async_tick(None)

    assert system.coordinator.trade_selling is False
    assert system.coordinator.setpoint == 300


async def test_never_without_a_purchase_price(trading):
    """The purchase price is the switch: no price, no cost of a cycle, and a
    sale that looks free is exactly the one that wears the packs out."""
    system = trading(**{CONF_BATTERY_PRICE: 0})

    await system.coordinator._async_tick(None)

    assert system.coordinator.last_trade_verdict["why"] == "no_battery_price"
    assert system.coordinator.trade_selling is False
    assert system.coordinator.setpoint == 300


async def test_never_without_a_capacity_to_spread_it_over(trading):
    system = trading(**{CONF_FULL_CHARGE_MINUTES: 0})

    await system.coordinator._async_tick(None)

    assert system.coordinator.last_trade_verdict["why"] == "no_capacity"
    assert system.coordinator.trade_selling is False


async def test_a_modest_peak_pays_under_saldering(trading):
    """0.38 all-in: market 0.24, and 0.11 of tax comes back while netting
    lasts - 0.35 against 0.20 of refill and wear."""
    system = trading(peak=0.38)

    await system.coordinator._async_tick(None)

    assert system.coordinator.last_trade_verdict["saldering"] is True
    assert system.coordinator.trade_selling is True


async def test_and_the_same_peak_does_not_once_saldering_ends(trading):
    """0.24 against the same 0.20: four cents, under the five asked for."""
    system = trading(peak=0.38, **{CONF_SALDERING_UNTIL: "2026-01-01"})

    await system.coordinator._async_tick(None)

    verdict = system.coordinator.last_trade_verdict
    assert verdict["saldering"] is False
    assert verdict["value"] == pytest.approx(0.24)
    assert verdict["why"] == "margin_too_small"
    assert system.coordinator.trade_selling is False


async def test_saldering_ends_on_the_day_it_is_set_to(trading, monkeypatch):
    system = trading(**{CONF_SALDERING_UNTIL: NOW.date().isoformat()})

    assert system.coordinator.saldering_active() is False
    monkeypatch.setattr(
        coordinator_module.dt_util, "utcnow", lambda: NOW - timedelta(days=1)
    )
    assert system.coordinator.saldering_active() is True


async def test_a_third_party_price_sensor_sells_only_on_a_fixed_rate(trading):
    """One number and no parts: the market price is unknown, so only a
    contract that pays a fixed rate can be weighed at all."""
    system = trading(side_lists=False)

    await system.coordinator._async_tick(None)
    assert system.coordinator.last_trade_verdict["why"] == "no_export_value"

    fixed = trading(
        side_lists=False,
        **{CONF_FEED_IN_BASIS: "fixed", CONF_FEED_IN_FIXED: 0.40,
           CONF_SALDERING_UNTIL: "2026-01-01"},
    )
    await fixed.coordinator._async_tick(None)
    assert fixed.coordinator.trade_selling is True


async def test_the_refill_is_bought_later_not_now(trading):
    """A sold kWh cannot be bought back in the slot it was sold in, so the
    current slot is no candidate for the refill even when it is the cheapest."""
    system = trading(peak=0.12)

    assert system.coordinator.refill_price() == pytest.approx(0.15)


async def test_never_sells_in_an_hour_it_is_buying_in(trading):
    """A fixed feed-in rate above the cheapest hour would make both look
    right at once. Buying wins: it is what the cheap hour is for, and a pack
    told to do both would do neither."""
    system = trading(
        soc=(35.0, 35.0),
        peak=0.02,
        **{CONF_FEED_IN_BASIS: "fixed", CONF_FEED_IN_FIXED: 0.40,
           CONF_CHARGE_BELOW_SOC: 40},
    )
    system.coordinator.sell_floor = 0

    await system.coordinator._async_tick(None)

    assert system.coordinator.active_policy == POLICY_DYNAMIC_CHARGE
    assert system.coordinator.setpoint == -7000
    assert system.coordinator.trade_selling is False


async def test_does_not_start_just_above_the_sell_floor(trading):
    """Room to spare before it starts, as with buying: a pack resting on its
    line reads either side of it and would otherwise flap."""
    system = trading(soc=(51.0, 80.0))
    system.coordinator.sell_floor = 50

    await system.coordinator._async_tick(None)

    assert system.coordinator.trade_selling is False


async def test_one_pack_at_its_floor_stops_both(trading):
    system = trading(soc=(30.0, 80.0))
    system.coordinator.sell_floor = 30

    await system.coordinator._async_tick(None)

    assert system.coordinator.trade_selling is False


async def test_runs_down_to_the_floor_then_stays_off_for_the_slot(trading):
    system = trading(soc=(60.0, 60.0))
    system.coordinator.sell_floor = 50

    await system.coordinator._async_tick(None)
    assert system.coordinator.trade_selling is True

    # inside the band now, but it started: it carries on to the line itself
    system.hass.states.set(system.soc(0), 51.0)
    system.hass.states.set(system.soc(1), 51.0)
    await system.coordinator._async_tick(None)
    assert system.coordinator.trade_selling is True

    system.hass.states.set(system.soc(0), 50.0)
    await system.coordinator._async_tick(None)
    assert system.coordinator.trade_selling is False

    # a pack bouncing back up a percent does not restart it in the same slot
    system.hass.states.set(system.soc(0), 53.0)
    await system.coordinator._async_tick(None)
    assert system.coordinator.trade_selling is False


async def test_the_reserve_is_a_floor_for_selling_too(trading):
    """The sell floor is the owner's line; the reserve is the house's, and
    the higher of the two wins."""
    system = trading(soc=(45.0, 45.0))
    system.coordinator.sell_floor = 0
    system.coordinator.soc_reserve = 44

    await system.coordinator._async_tick(None)

    assert system.coordinator.trade_selling is False


async def test_trade_state_survives_a_restart(trading):
    system = trading()
    system.coordinator.trade_mode = TRADE_SHADOW
    system.coordinator.sell_floor = 42.0
    stored = system.coordinator._state_to_save()

    fresh = trading(trade=TRADE_OFF)
    fresh.coordinator._store.data = {**stored, "saved_at": time.time()}
    await fresh.coordinator._async_restore()

    assert fresh.coordinator.trade_mode == TRADE_SHADOW
    assert fresh.coordinator.sell_floor == 42.0


async def test_an_unknown_trade_mode_is_refused(trading):
    system = trading()

    with pytest.raises(ValueError):
        await system.coordinator.async_set_trade_mode("yolo")


async def test_the_sell_floor_is_clamped(trading):
    system = trading()

    await system.coordinator.async_set_sell_floor(140)
    assert system.coordinator.sell_floor == 100.0
    await system.coordinator.async_set_sell_floor(-5)
    assert system.coordinator.sell_floor == 0.0


def test_the_saldering_default_is_the_announced_end():
    assert coordinator_module._parse_day("rubbish", "2027-01-01") == date(2027, 1, 1)
    assert coordinator_module._parse_day(None, "2027-01-01") == date(2027, 1, 1)
    assert coordinator_module._parse_day("2026-07-01", "2027-01-01") == date(2026, 7, 1)

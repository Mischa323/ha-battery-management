"""The arithmetic behind selling to the grid, and behind "what did they save".

The numbers are the plan's, from 21 September: an evening at EUR 0.46 all-in
(market 0.27), a refill at noon for EUR 0.18, and the two regimes either side
of saldering ending.
"""
from __future__ import annotations

import pytest

from custom_components.battery_management.trading import (
    FEED_IN_FIXED,
    FEED_IN_MARKET,
    FEED_IN_MARKET_VAT,
    ROUND_TRIP,
    FeedIn,
    export_value,
    grid_cost,
    sell_margin,
    wear_per_kwh,
)

MARKET = 0.27
TAX = 0.11


def test_under_saldering_the_energy_tax_is_earned_back():
    value = export_value(FeedIn(FEED_IN_MARKET_VAT), MARKET, TAX, saldering=True)

    assert value == pytest.approx(MARKET * 1.21 + TAX)


def test_after_saldering_only_what_the_supplier_pays():
    value = export_value(FeedIn(FEED_IN_MARKET_VAT), MARKET, TAX, saldering=False)

    assert value == pytest.approx(MARKET * 1.21)


def test_the_bare_market_price_is_a_basis_of_its_own():
    assert export_value(FeedIn(FEED_IN_MARKET), MARKET, TAX, False) == MARKET


def test_a_feed_in_fee_is_a_negative_correction():
    value = export_value(FeedIn(FEED_IN_MARKET, correction=-0.02), MARKET, TAX, False)

    assert value == pytest.approx(0.25)


def test_a_fixed_rate_needs_no_market_price():
    """Some contracts pay a flat rate. Then a missing market price is no reason
    to refuse - but saldering still adds the tax, which is per slot."""
    fixed = FeedIn(FEED_IN_FIXED, fixed=0.07)

    assert export_value(fixed, None, None, saldering=False) == 0.07
    assert export_value(fixed, None, TAX, saldering=True) == pytest.approx(0.18)


def test_no_market_price_is_no_value_rather_than_a_guess():
    assert export_value(FeedIn(FEED_IN_MARKET), None, TAX, False) is None


def test_under_saldering_an_unknown_tax_is_no_value_either():
    """Leaving the tax out would undersell; putting in a typical one would be
    a guess dressed as a price."""
    assert export_value(FeedIn(FEED_IN_MARKET), MARKET, None, True) is None


def test_wear_is_the_price_over_everything_the_packs_will_ever_deliver():
    # the plan's example: EUR 5000 over 6000 cycles of 28 kWh
    assert wear_per_kwh(5000, 6000, 28) == pytest.approx(0.0298, abs=1e-4)


@pytest.mark.parametrize("price,cycles,capacity", [
    (0, 6000, 28), (5000, 0, 28), (5000, 6000, None), (5000, 6000, 0),
])
def test_wear_is_unknown_rather_than_guessed(price, cycles, capacity):
    assert wear_per_kwh(price, cycles, capacity) is None


def test_the_plan_s_evening_sells_under_saldering():
    # market with VAT plus the tax earned back: all-in less the supplier's markup
    value = export_value(FeedIn(FEED_IN_MARKET_VAT), MARKET, TAX, saldering=True)

    assert value == pytest.approx(0.437, abs=0.001)
    assert sell_margin(value, 0.18, 0.05) == pytest.approx(0.182, abs=0.001)


def test_and_does_not_after_it():
    value = export_value(FeedIn(FEED_IN_MARKET), MARKET, TAX, saldering=False)

    assert sell_margin(value, 0.18, 0.05) < 0.05


def test_the_refill_pays_for_the_round_trip():
    """Buying back one kilowatt hour takes more than one from the meter."""
    assert sell_margin(0.30, 0.20, 0.0) == pytest.approx(0.30 - 0.20 / ROUND_TRIP)


def test_drawn_is_paid_at_the_price_and_fed_back_earns_the_value():
    assert grid_cost(1000, 0.30, 0.10, 1.0) == pytest.approx(0.30)
    assert grid_cost(-1000, 0.30, 0.10, 1.0) == pytest.approx(-0.10)
    assert grid_cost(0, 0.30, 0.10, 1.0) == 0.0

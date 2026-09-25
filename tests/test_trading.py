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


# -- payback ------------------------------------------------------------------

from custom_components.battery_management.trading import (  # noqa: E402
    payback_remaining,
    simple_payback,
    yearly,
)


def test_a_rate_is_taken_over_the_hours_counted():
    assert yearly(1.0, 24.0) == pytest.approx(365.0)
    assert yearly(1.0, 0.0) is None


def test_simple_payback_is_price_over_the_yearly_saving():
    assert simple_payback(5000, 500) == pytest.approx(10.0)


def test_a_saving_that_never_comes_never_pays_back():
    assert simple_payback(5000, 0) is None
    assert simple_payback(5000, -20) is None
    assert simple_payback(5000, None) is None
    assert simple_payback(0, 500) is None


def test_payback_uses_saldering_while_it_lasts_then_the_rate_after():
    """EUR 5000, 1000 saved; 200 a year for the quarter year saldering has
    left repays 50, and the other 3950 at 600 a year takes 6.58 more."""
    years = payback_remaining(5000, 1000, 200, 600, 0.25)
    assert years == pytest.approx(0.25 + 3950 / 600)


def test_payback_can_finish_inside_saldering():
    assert payback_remaining(5000, 4900, 800, 600, 0.5) == pytest.approx(100 / 800)


def test_payback_after_saldering_has_ended():
    assert payback_remaining(5000, 1000, 200, 500, 0.0) == pytest.approx(8.0)
    assert payback_remaining(5000, 1000, 200, 500, -1.0) == pytest.approx(8.0)


def test_repaid_is_nought_to_go():
    assert payback_remaining(5000, 5200, 200, 500, 0.3) == 0.0


def test_a_loss_during_saldering_just_passes_the_time():
    assert payback_remaining(5000, 0, -50, 500, 0.5) == pytest.approx(0.5 + 10.0)


def test_never_repaid_after_saldering_is_none():
    assert payback_remaining(5000, 0, 200, 0, 0.25) is None
    assert payback_remaining(0, 0, 200, 500, 0.25) is None


# -- refilling from the sun ----------------------------------------------------

from custom_components.battery_management.trading import (  # noqa: E402
    blended_refill,
    solar_refill_share,
)


def test_the_sun_fills_the_room_there_already_was_first():
    """5 kWh empty anyway, 3 kWh sold, 12 kWh of sun: all of the sale back."""
    assert solar_refill_share(12.0, 5.0, 3.0) == 1.0


def test_a_night_that_empties_the_packs_leaves_the_sale_to_the_grid():
    """10 kWh of room by sunrise and 10 kWh of sun: none of it reaches the
    slice the sale emptied."""
    assert solar_refill_share(10.0, 10.0, 3.0) == 0.0


def test_part_of_the_sale_can_come_back():
    assert solar_refill_share(11.5, 10.0, 3.0) == pytest.approx(0.5)


def test_no_sun_no_share():
    assert solar_refill_share(None, 0.0, 3.0) == 0.0
    assert solar_refill_share(0.0, 0.0, 3.0) == 0.0
    assert solar_refill_share(10.0, 0.0, 0.0) == 0.0


def test_the_refill_is_blended_by_that_share():
    assert blended_refill(0.20, 0.14, 0.5) == pytest.approx(0.17)
    assert blended_refill(0.20, 0.14, 0.0) == 0.20
    assert blended_refill(0.20, 0.14, 1.0) == 0.14
    assert blended_refill(None, 0.14, 1.0) == 0.14
    assert blended_refill(None, 0.14, 0.5) is None
    assert blended_refill(0.20, None, 0.7) == 0.20

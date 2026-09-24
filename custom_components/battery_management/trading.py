"""What a kilowatt hour is worth going out, and what it costs to put back.

Free of Home Assistant imports, like `prices.py`, so every rule here is tested
on its own.

Two questions share this arithmetic:

* **Sell now?** A kilowatt hour sent to the grid earns its export value, and
  the pack then has to be refilled - at the cheapest price still ahead, through
  a round trip that loses about an eighth, and at the cost of the wear on the
  cells. Worth doing only when what it earns clears all three by a margin.
* **What have the packs saved?** Price the meter as it was, and as it would
  have been with no packs at all, and take the difference.

Both hinge on the export value, and the export value hinges on *saldering*:
until it ends, the energy tax on a kilowatt hour fed back is netted against
one drawn, so export is worth nearly the all-in price. After it, only what the
supplier pays. The date is a setting, so the rules change by themselves on the
day and nobody has to remember to flip anything.
"""
from __future__ import annotations

from dataclasses import dataclass

#: what a kilowatt hour loses on its way into a pack and back out
ROUND_TRIP = 0.88
#: Dutch VAT, for a feed-in compensation paid "market price including VAT"
VAT = 0.21

FEED_IN_MARKET = "market"
FEED_IN_MARKET_VAT = "market_vat"
FEED_IN_FIXED = "fixed"
FEED_IN_BASES = [FEED_IN_MARKET, FEED_IN_MARKET_VAT, FEED_IN_FIXED]


@dataclass(frozen=True)
class FeedIn:
    """How the supplier pays for a kilowatt hour fed back, as the owner set it.

    A dynamic contract pays by the quarter, so this is a rule rather than a
    number: a basis, and a correction on top - negative for a feed-in fee.
    """

    basis: str = FEED_IN_MARKET
    fixed: float = 0.0
    correction: float = 0.0


def export_value(
    feed_in: FeedIn,
    market: float | None,
    energy_tax: float | None,
    saldering: bool,
) -> float | None:
    """What one kilowatt hour fed back earns in this slot, in EUR.

    `energy_tax` is added while saldering holds: the tax on a kilowatt hour
    fed back is netted against one drawn, so it is earned back. None when the
    basis needs a market price and there is none - never a guess.
    """
    if feed_in.basis == FEED_IN_FIXED:
        base = feed_in.fixed
    elif market is None:
        return None
    elif feed_in.basis == FEED_IN_MARKET_VAT:
        base = market * (1 + VAT)
    else:
        base = market
    value = base + feed_in.correction
    if saldering:
        if energy_tax is None:
            return None
        value += energy_tax
    return value


def wear_per_kwh(price: float, cycles: float, capacity_kwh: float | None) -> float | None:
    """What one kilowatt hour through the packs costs in cells, in EUR.

    The purchase price spread over every kilowatt hour the packs will ever
    deliver: cycles times capacity. None when any of it is unknown - a
    guessed wear would either sell the packs to death or never sell at all.
    """
    if price <= 0 or cycles <= 0 or not capacity_kwh or capacity_kwh <= 0:
        return None
    return price / (cycles * capacity_kwh)


def sell_margin(value: float, refill_price: float, wear: float) -> float:
    """Euros made per kilowatt hour sold now and bought back later.

    Refilling one kilowatt hour takes 1 / ROUND_TRIP from the meter, so the
    refill is dearer than its price. The wear is paid once per kilowatt hour
    through the packs.
    """
    return value - refill_price / ROUND_TRIP - wear


def grid_cost(grid_w: float, price: float, value: float, hours: float) -> float:
    """What the meter cost over `hours` at a steady `grid_w`, in EUR.

    Positive watts are drawn and paid at the all-in price; negative watts are
    fed back and earn the export value.
    """
    kwh = grid_w / 1000.0 * hours
    return kwh * price if kwh >= 0 else kwh * value


#: hours in a year, for turning a measured rate into a yearly one
HOURS_PER_YEAR = 8760.0


def yearly(amount: float, counted_hours: float) -> float | None:
    """What `amount`, earned over `counted_hours`, comes to over a year.

    Over the hours actually counted, not the calendar since counting began:
    a week with HA down for a day would otherwise read a seventh low.
    """
    if counted_hours <= 0:
        return None
    return amount / counted_hours * HOURS_PER_YEAR


def simple_payback(price: float, per_year: float | None) -> float | None:
    """Years for `per_year` to repay `price`; None when it never will."""
    if price <= 0 or per_year is None or per_year <= 0:
        return None
    return price / per_year


def payback_remaining(
    price: float,
    saved: float,
    per_year_with: float | None,
    per_year_after: float | None,
    saldering_years_left: float,
) -> float | None:
    """Years from now until the packs have paid for themselves, footing and all.

    Saldering first, at its rate, for as long as it lasts; then the rate
    without it for whatever is left. That is the number that will actually
    come true, where `simple_payback` on either footing alone answers "what
    if it were like this for ever". None when what is left is never repaid.
    """
    if price <= 0:
        return None
    remaining = price - saved
    if remaining <= 0:
        return 0.0
    left = max(saldering_years_left, 0.0)
    if left > 0 and per_year_with is not None and per_year_with > 0:
        if per_year_with * left >= remaining:
            return remaining / per_year_with
        remaining -= per_year_with * left
    if per_year_after is None or per_year_after <= 0:
        return None
    return left + remaining / per_year_after

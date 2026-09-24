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

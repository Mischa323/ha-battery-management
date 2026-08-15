"""Fetching prices ourselves, for suppliers that publish them openly.

Pointing at somebody else's price sensor stays the default and always will:
that is what lets a site change supplier without touching this integration, and
it is why `prices.py` recognises *shapes* rather than integrations. But at a
site with no price integration installed, "first install another custom
integration" is a real obstacle - and this is meant to be maintained centrally
across a handful of family and friends' houses.

So this is the other half: for suppliers whose prices are public, ask them
directly. The result is handed to `parse_forecast` in one of the shapes it
already understands, so nothing downstream knows the difference.

The network call is deliberately separated from the arithmetic. Everything
below `parse_*` is pure and tested without touching the internet.
"""
from __future__ import annotations

from datetime import date, timedelta

#: key -> label, offered in the wizard. One entry today, but the seam is the
#: point: adding EnergyZero later must not disturb anything already configured.
SUPPLIER_FRANK = "frank_energie"
SUPPLIERS: dict[str, str] = {SUPPLIER_FRANK: "Frank Energie"}

#: where the choice between "ask them ourselves" and "read a sensor" is stored
SOURCE_NONE = "none"
SOURCE_ENTITY = "entity"

FRANK_ENDPOINT = "https://frank-graphql-prod.graphcdn.app/"
FRANK_QUERY = """
query MarketPrices($startDate: Date!, $endDate: Date!) {
  marketPricesElectricity(startDate: $startDate, endDate: $endDate) {
    from
    till
    marketPrice
    marketPriceTax
    sourcingMarkupPrice
    energyTaxPrice
  }
}
"""

#: what an all-in price is made of. `marketPrice` is required - a slot without
#: one is not a price. The rest default to 0, which yields the bare exchange
#: price: still correctly *ranked*, just not what you actually pay.
_FRANK_REQUIRED = "marketPrice"
_FRANK_ADDERS = ("marketPriceTax", "sourcingMarkupPrice", "energyTaxPrice")


def frank_request(today: date) -> tuple[str, dict]:
    """The endpoint and JSON body asking for today's and tomorrow's prices.

    Tomorrow is published during the afternoon and is simply absent before
    then, which needs no special handling: `cheapest_slots` ranks over a
    rolling 24 h window from now, so a short forecast is a short window rather
    than a wrong one.
    """
    return FRANK_ENDPOINT, {
        "operationName": "MarketPrices",
        "query": FRANK_QUERY,
        "variables": {
            "startDate": today.isoformat(),
            "endDate": (today + timedelta(days=2)).isoformat(),
        },
    }


def parse_frank(payload: dict) -> dict:
    """Turn Frank Energie's answer into attributes `parse_forecast` can read.

    The all-in price is used, not the bare exchange price. It ranks identically
    - tax and markup are a fixed adder and VAT a fixed multiplier, so the
    transform is monotonic and the cheap-to-expensive order cannot change - but
    it is the number actually paid, which is the one worth putting on a
    dashboard. It is also the thing an exchange feed could never give us.

    An unrecognised or empty answer yields `{}`, which downstream means "no
    forecast" and disables cheap-hour charging. Never a guessed price.
    """
    if not isinstance(payload, dict):
        return {}
    rows = (payload.get("data") or {}).get("marketPricesElectricity")
    if not isinstance(rows, list):
        return {}

    prices = []
    market_prices = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        market = row.get(_FRANK_REQUIRED)
        start = row.get("from")
        if not isinstance(market, (int, float)) or not start:
            continue
        total = float(market)
        for key in _FRANK_ADDERS:
            value = row.get(key)
            if isinstance(value, (int, float)):
                total += float(value)
        slot = {"from": start, "price": round(total, 6)}
        bare = {"from": start, "price": round(float(market), 6)}
        if row.get("till"):
            slot["till"] = bare["till"] = row["till"]
        prices.append(slot)
        market_prices.append(bare)

    # `prices` is the key an ordinary price sensor would publish, so the
    # shape-based parser handles it with no special case anywhere else.
    #
    # `market_prices` rides alongside and is deliberately NOT one of the keys
    # that parser looks at: it is the exchange component on its own, which is
    # what export is settled against. Paying tax on power you sold back would
    # be a strange arrangement, so the all-in price is the wrong number there -
    # and a wrong number on an energy dashboard looks exactly like a right one.
    return {"prices": prices, "market_prices": market_prices} if prices else {}


#: key -> (build the request, read the answer)
FETCHERS = {SUPPLIER_FRANK: (frank_request, parse_frank)}

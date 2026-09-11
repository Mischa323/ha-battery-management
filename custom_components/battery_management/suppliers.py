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

#: The market settles in 15-minute blocks and Frank publishes that way, so ask
#: for them: 96 slots a day instead of 24. `price_resolution` still decides
#: what is done with them - folding a quarter-hourly feed into hours is a
#: readability choice this integration can make, but a feed that never had the
#: quarters in it cannot be unfolded, and the peaks inside the hour are exactly
#: where a cheap quarter hides.
FRANK_RESOLUTION = "PT15M"

#: `marketPrices` takes one day at a time, unlike the date range the hourly
#: `marketPricesElectricity` accepted. So it is asked twice - once per day -
#: rather than once with the two days aliased into a single document.
#:
#: That is not a style preference. A day Frank has not published yet is a
#: GraphQL *error*, not a null, and the field is non-nullable: the error
#: propagates and takes the whole `data` with it. Both days in one document
#: therefore means no prices at all for the half of every day before tomorrow
#: is published - which is worse than the hourly feed this replaced. Two
#: requests cost one extra round trip an hour and let a missing tomorrow fail
#: on its own.
FRANK_QUERY = """
query MarketPrices($date: String!, $resolution: PriceResolution!) {
  marketPrices(date: $date, resolution: $resolution) {
    electricityPrices {
      from
      till
      marketPrice
      marketPriceTax
      sourcingMarkupPrice
      energyTaxPrice
    }
  }
}
"""

#: what an all-in price is made of. `marketPrice` is required - a slot without
#: one is not a price. The rest default to 0, which yields the bare exchange
#: price: still correctly *ranked*, just not what you actually pay.
_FRANK_REQUIRED = "marketPrice"
_FRANK_ADDERS = ("marketPriceTax", "sourcingMarkupPrice", "energyTaxPrice")


def frank_requests(today: date) -> list[tuple[str, dict]]:
    """One request per day: today, then tomorrow.

    Tomorrow is published during the afternoon and errors before then, which
    needs no special handling beyond keeping it in its own request:
    `cheapest_slots` ranks over a rolling 24 h window from now, so a forecast
    that stops at midnight is a short window rather than a wrong one.
    """
    return [_frank_day(today), _frank_day(today + timedelta(days=1))]


def _frank_day(day: date) -> tuple[str, dict]:
    return FRANK_ENDPOINT, {
        "operationName": "MarketPrices",
        "query": FRANK_QUERY,
        "variables": {
            "date": day.isoformat(),
            "resolution": FRANK_RESOLUTION,
        },
    }


def _frank_rows(payloads: list) -> list:
    """Every electricity row across the answers, in the order they arrived.

    A payload that is missing, errored or malformed contributes nothing rather
    than failing the others: an afternoon request has a tomorrow and a morning
    one does not, and both are perfectly ordinary.

    Slots are deduplicated on their start. Two different days cannot overlap,
    so this normally does nothing - but a slot counted twice would be ranked
    twice, quietly weighting one quarter-hour against the rest, and that is
    not a failure anyone would notice by reading a dashboard.
    """
    rows: list = []
    seen: set = set()
    for payload in payloads:
        if not isinstance(payload, dict):
            continue
        data = payload.get("data")
        if not isinstance(data, dict):
            continue
        day = data.get("marketPrices")
        if not isinstance(day, dict):
            continue
        values = day.get("electricityPrices")
        if not isinstance(values, list):
            continue
        for row in values:
            if not isinstance(row, dict):
                continue
            start = row.get("from")
            if start and start in seen:
                continue
            if start:
                seen.add(start)
            rows.append(row)
    return rows


def parse_frank(payloads: list) -> dict:
    """Turn Frank Energie's answers into attributes `parse_forecast` can read.

    The all-in price is used, not the bare exchange price. It ranks identically
    - tax and markup are a fixed adder and VAT a fixed multiplier, so the
    transform is monotonic and the cheap-to-expensive order cannot change - but
    it is the number actually paid, which is the one worth putting on a
    dashboard. It is also the thing an exchange feed could never give us.

    An unrecognised or empty answer yields `{}`, which downstream means "no
    forecast" and disables cheap-hour charging. Never a guessed price.
    """
    if not isinstance(payloads, list):
        return {}
    rows = _frank_rows(payloads)

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


#: key -> (build the requests, read the answers). Plural on both sides: a
#: supplier that needs several calls to describe one forecast is the normal
#: case, not the exception.
FETCHERS = {SUPPLIER_FRANK: (frank_requests, parse_frank)}

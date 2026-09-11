"""Reading Frank Energie's answer, without touching the network.

The request builder and the response parser are pure, so the awkward cases get
pinned here rather than discovered on a Tuesday when prices stop arriving.
"""
from __future__ import annotations

from datetime import date, datetime, timezone

from custom_components.battery_management.prices import parse_forecast
from custom_components.battery_management.suppliers import (
    FRANK_ENDPOINT,
    SUPPLIERS,
    frank_request,
    parse_frank,
)


def slot(start: str, price: float, **extra) -> dict:
    return {
        "from": start,
        "till": start.replace("T0", "T1"),
        "marketPrice": price,
        **extra,
    }


def answer(*rows: dict, tomorrow: list | None = None) -> dict:
    """What the endpoint returns: one aliased field per day."""
    return {
        "data": {
            "today": {"electricityPrices": list(rows)},
            "tomorrow": None if tomorrow is None else {"electricityPrices": tomorrow},
        }
    }


def test_the_request_asks_for_both_days_by_the_quarter():
    """The market settles per quarter and so does the question we ask."""
    url, body = frank_request(date(2026, 9, 1))

    assert url == FRANK_ENDPOINT
    assert body["operationName"] == "MarketPrices"
    assert body["variables"] == {
        "today": "2026-09-01",
        "tomorrow": "2026-09-02",
        "resolution": "PT15M",
    }


def test_it_adds_tax_and_markup_to_the_market_price():
    """The all-in price is what you actually pay, and it ranks identically."""
    payload = answer(
        {
            "from": "2026-09-01T00:00:00.000Z",
            "till": "2026-09-01T00:15:00.000Z",
            "marketPrice": 0.10,
            "marketPriceTax": 0.021,
            "sourcingMarkupPrice": 0.02,
            "energyTaxPrice": 0.13,
        }
    )

    assert parse_frank(payload)["prices"][0]["price"] == 0.271


def test_a_negative_market_price_still_adds_up():
    payload = answer(
        {
            "from": "2026-09-01T13:00:00.000Z",
            "marketPrice": -0.05,
            "marketPriceTax": -0.0105,
            "sourcingMarkupPrice": 0.02,
            "energyTaxPrice": 0.13,
        }
    )

    assert parse_frank(payload)["prices"][0]["price"] == 0.0895


def test_missing_adders_leave_the_bare_exchange_price():
    """Still ranked correctly, just not what lands on the bill."""
    payload = answer(slot("2026-09-01T02:00:00Z", 0.08))

    assert parse_frank(payload)["prices"][0]["price"] == 0.08


def test_a_slot_without_a_market_price_is_dropped():
    payload = answer(
        {"from": "2026-09-01T02:00:00Z", "energyTaxPrice": 0.13},
        slot("2026-09-01T03:00:00Z", 0.08),
    )

    assert len(parse_frank(payload)["prices"]) == 1


def test_an_error_response_is_no_forecast_rather_than_a_guess():
    assert parse_frank({"errors": [{"message": "boom"}]}) == {}
    assert parse_frank(answer()) == {}
    assert parse_frank({"data": None}) == {}
    assert parse_frank({}) == {}
    assert parse_frank("not json") == {}


def test_the_result_is_a_shape_the_existing_parser_already_reads():
    """The whole point of the seam: nothing downstream knows the difference."""
    payload = answer(
        {
            "from": "2026-09-01T10:00:00.000Z",
            "till": "2026-09-01T11:00:00.000Z",
            "marketPrice": 0.10,
            "energyTaxPrice": 0.13,
        },
        {
            "from": "2026-09-01T11:00:00.000Z",
            "till": "2026-09-01T12:00:00.000Z",
            "marketPrice": 0.05,
            "energyTaxPrice": 0.13,
        },
    )

    slots = parse_forecast(
        parse_frank(payload), datetime(2026, 9, 1, 9, tzinfo=timezone.utc)
    )

    assert [s.price for s in slots] == [0.23, 0.18]
    assert slots[0].start == datetime(2026, 9, 1, 10, tzinfo=timezone.utc)
    assert slots[0].end == datetime(2026, 9, 1, 11, tzinfo=timezone.utc)


def test_a_days_worth_of_quarters_survives_the_parse():
    """96 slots is the point of the exercise: the cheap quarter inside an
    otherwise ordinary hour is exactly what an hourly feed averages away."""
    quarters = [
        {
            "from": f"2026-09-01T{i // 4:02d}:{15 * (i % 4):02d}:00.000Z",
            "till": f"2026-09-01T{(i + 1) // 4:02d}:{15 * ((i + 1) % 4):02d}:00.000Z",
            "marketPrice": 0.10,
            "energyTaxPrice": 0.13,
        }
        for i in range(96)
    ]

    slots = parse_forecast(
        parse_frank(answer(*quarters)),
        datetime(2026, 9, 1, tzinfo=timezone.utc),
    )

    assert len(slots) == 96
    assert (slots[0].end - slots[0].start).total_seconds() == 900


def test_a_tomorrow_that_is_not_published_yet_leaves_today_alone():
    """Prices arrive in the afternoon, so half the day every request has a
    null tomorrow beside a perfectly good today."""
    payload = answer(slot("2026-09-01T02:00:00Z", 0.08))
    payload["data"]["tomorrow"] = None

    assert len(parse_frank(payload)["prices"]) == 1


def test_both_days_are_read_when_tomorrow_has_arrived():
    payload = answer(
        slot("2026-09-01T02:00:00Z", 0.08),
        tomorrow=[slot("2026-09-02T02:00:00Z", 0.09)],
    )

    assert len(parse_frank(payload)["prices"]) == 2


def test_frank_energie_is_offered_by_name():
    assert SUPPLIERS["frank_energie"] == "Frank Energie"

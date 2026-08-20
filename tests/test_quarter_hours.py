"""Quarter-hourly prices, and the choice to read them by the hour instead.

The Dutch market settles in 15-minute blocks, and Frank Energie lets you pick.
Nothing in the ranking assumed hours - `cheap_hours` is converted into a number
of slots from whatever the feed publishes - but 96 bars on a card is a lot of
bars, and somebody looking at a dashboard may just want the shape of the day.

So the feed is taken as published and the *reading* of it is a setting. Both
the chart and the decisions use the same series either way: they must never be
able to disagree about what "cheap" meant.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from custom_components.battery_management.const import (
    CONF_CHEAP_HOURS,
    CONF_PRICE_RESOLUTION,
    CONF_PRICE_SENSOR,
    RESOLUTION_HOURLY,
    RESOLUTION_PUBLISHED,
)
from custom_components.battery_management.prices import (
    cheapest_slots,
    parse_forecast,
    to_hourly,
)

NOW = datetime(2026, 9, 1, 0, 0, tzinfo=timezone.utc)
PRICES = "sensor.energy_prices"


def series(minutes: int, price=lambda i: 0.10 + 0.001 * i) -> list[dict]:
    count = 24 * 60 // minutes
    return [
        {
            "from": (NOW + timedelta(minutes=minutes * i)).isoformat(),
            "till": (NOW + timedelta(minutes=minutes * (i + 1))).isoformat(),
            "price": price(i),
        }
        for i in range(count)
    ]


# -- the feed as published -----------------------------------------------------


def test_quarter_hourly_slots_parse_as_quarter_hours():
    slots = parse_forecast({"prices": series(15)}, NOW)

    assert len(slots) == 96
    assert slots[0].end - slots[0].start == timedelta(minutes=15)


def test_cheap_hours_still_means_hours_whatever_the_granularity():
    """The setting is in hours; the feed decides how many slots that is."""
    quarters = parse_forecast({"prices": series(15)}, NOW)
    hours = parse_forecast({"prices": series(60)}, NOW)

    assert len(cheapest_slots(quarters, NOW, 3, 24)) == 12   # 12 quarters
    assert len(cheapest_slots(hours, NOW, 3, 24)) == 3


# -- folding it to hours -------------------------------------------------------


def test_folding_gives_one_slot_per_hour():
    folded = to_hourly(parse_forecast({"prices": series(15)}, NOW))

    assert len(folded) == 24
    assert folded[0].end - folded[0].start == timedelta(hours=1)


def test_the_hourly_price_is_the_average_of_its_quarters():
    quarters = parse_forecast(
        {"prices": series(15, price=lambda i: [0.10, 0.20, 0.30, 0.40][i % 4])}, NOW
    )

    assert to_hourly(quarters)[0].price == 0.25


def test_folding_an_already_hourly_feed_changes_nothing():
    hourly = parse_forecast({"prices": series(60)}, NOW)

    assert to_hourly(hourly) == hourly


def test_folding_keeps_the_order():
    folded = to_hourly(parse_forecast({"prices": series(15)}, NOW))

    assert [s.start for s in folded] == sorted(s.start for s in folded)


def test_an_incomplete_last_hour_keeps_its_real_end():
    """Two quarters published for the final hour must not claim a whole one."""
    rows = series(15)[:6]
    folded = to_hourly(parse_forecast({"prices": rows}, NOW))

    assert len(folded) == 2
    assert folded[1].end - folded[1].start == timedelta(minutes=30)


def test_nothing_to_fold_is_not_a_crash():
    assert to_hourly([]) == []


# -- and the setting that chooses ----------------------------------------------


def live_series(minutes: int) -> list[dict]:
    """The same series, anchored to today's midnight - one whole calendar day.

    It used to start at the current hour, on the reasoning that the plan only
    looks 24 h ahead. That is still true of the *decisions*, but the chart's
    band is ranked per calendar day now, and a series anchored to now falls
    across two half-days. Each half then has half the spread, which the price
    margin quite correctly refuses to call cheap - so the fixture measured an
    artefact of its own anchoring rather than anything a real feed does.
    Suppliers publish whole days.
    """
    start = datetime.now(timezone.utc).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    count = 24 * 60 // minutes
    return [
        {
            "from": (start + timedelta(minutes=minutes * i)).isoformat(),
            "till": (start + timedelta(minutes=minutes * (i + 1))).isoformat(),
            "price": 0.10 + 0.001 * i,
        }
        for i in range(count)
    ]


def build(build_system, resolution: str, minutes: int):
    system = build_system(
        grid=0,
        **{
            CONF_PRICE_SENSOR: PRICES,
            CONF_PRICE_RESOLUTION: resolution,
            CONF_CHEAP_HOURS: 3,
        },
    )
    system.hass.states.set(PRICES, 0.2, {"prices": live_series(minutes)})
    return system


async def test_as_published_leaves_the_quarters_alone(build_system):
    system = build(build_system, RESOLUTION_PUBLISHED, 15)

    assert len(system.coordinator._price_forecast()) == 96


async def test_hourly_folds_them(build_system):
    system = build(build_system, RESOLUTION_HOURLY, 15)

    assert len(system.coordinator._price_forecast()) == 24


async def test_the_chart_and_the_decisions_see_the_same_series(build_system):
    """They must never be able to disagree about what "cheap" meant."""
    system = build(build_system, RESOLUTION_HOURLY, 15)

    plan = system.coordinator.plan()
    cheap = [h for h in plan["hours"] if h["role"] == "cheap"]

    assert len(plan["hours"]) == 24
    assert len(cheap) == 3          # three whole hours, not twelve quarters


async def test_the_default_is_whatever_the_supplier_publishes(build_system):
    system = build_system(grid=0, **{CONF_PRICE_SENSOR: PRICES})
    system.hass.states.set(PRICES, 0.2, {"prices": live_series(15)})

    assert len(system.coordinator._price_forecast()) == 96

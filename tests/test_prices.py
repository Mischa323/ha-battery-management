"""Reading a forecast out of whatever price sensor a site happens to have.

Each Dutch supplier's integration publishes upcoming prices in its own shape, so
these tests are mostly about recognising shapes rather than integrations - a
site should be able to switch supplier by pointing at a different sensor.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from custom_components.battery_management.prices import (
    cheapest_slots,
    is_cheap_now,
    parse_forecast,
    slot_at,
)

UTC = timezone.utc
NOON = datetime(2026, 8, 5, 12, 0, tzinfo=UTC)
MIDNIGHT = datetime(2026, 8, 5, 0, 0, tzinfo=UTC)


def hourly(prices, day=MIDNIGHT):
    """[{start, end, value}] - the Nord Pool 'raw_today' shape."""
    return [
        {
            "start": (day + timedelta(hours=i)).isoformat(),
            "end": (day + timedelta(hours=i + 1)).isoformat(),
            "value": price,
        }
        for i, price in enumerate(prices)
    ]


# -- shapes ------------------------------------------------------------------


def test_reads_a_list_of_dicts_with_explicit_ends():
    slots = parse_forecast({"raw_today": hourly([0.10, 0.20, 0.05])}, NOON)

    assert [s.price for s in slots] == [0.10, 0.20, 0.05]
    assert slots[0].start == MIDNIGHT
    assert slots[0].end == MIDNIGHT + timedelta(hours=1)


def test_infers_missing_end_times_from_the_next_slot():
    """EnergyZero and friends publish a start and a price, no end."""
    entries = [
        {"datetime": (MIDNIGHT + timedelta(hours=i)).isoformat(), "price": p}
        for i, p in enumerate([0.10, 0.20, 0.05])
    ]

    slots = parse_forecast({"prices": entries}, NOON)

    assert len(slots) == 3
    assert slots[0].end == slots[1].start
    # the last one borrows the length of the one before it
    assert slots[2].end - slots[2].start == timedelta(hours=1)


def test_reads_a_bare_list_of_24_numbers_as_hourly():
    """The classic Nord Pool / Tibber 'today' attribute."""
    slots = parse_forecast({"today": [0.1] * 24}, NOON)

    assert len(slots) == 24
    assert slots[0].start == MIDNIGHT
    assert slots[0].end - slots[0].start == timedelta(hours=1)


def test_reads_96_numbers_as_quarter_hourly():
    """NL is moving to quarter-hourly prices."""
    slots = parse_forecast({"today": [0.1] * 96}, NOON)

    assert len(slots) == 96
    assert slots[0].end - slots[0].start == timedelta(minutes=15)


def test_tomorrow_lands_on_tomorrow():
    slots = parse_forecast({"today": [0.1] * 24, "tomorrow": [0.2] * 24}, NOON)

    assert len(slots) == 48
    assert slots[-1].start.date() == (MIDNIGHT + timedelta(days=1)).date()


def test_the_same_slot_under_two_attributes_is_not_counted_twice():
    attributes = {"raw_today": hourly([0.10, 0.20]), "prices": hourly([0.10, 0.20])}

    assert len(parse_forecast(attributes, NOON)) == 2


def test_handles_a_zulu_timestamp():
    entries = [{"start": "2026-08-05T00:00:00Z", "price": 0.1}]

    assert parse_forecast({"prices": entries}, NOON)[0].start == MIDNIGHT


def test_gives_up_cleanly_on_an_unrecognised_sensor():
    """Empty means cheap-hour charging switches off - never a guessed price."""
    assert parse_forecast({}, NOON) == []
    assert parse_forecast({"state_class": "measurement"}, NOON) == []
    assert parse_forecast({"prices": [{"nonsense": 1}]}, NOON) == []
    assert parse_forecast({"prices": "not a list"}, NOON) == []


def test_skips_unparseable_entries_but_keeps_the_rest():
    entries = hourly([0.10, 0.20])
    entries.append({"start": "not a time", "price": 0.01})

    assert len(parse_forecast({"prices": entries}, NOON)) == 2


# -- picking the cheap hours -------------------------------------------------


def test_finds_the_slot_we_are_in():
    slots = parse_forecast({"raw_today": hourly([0.1] * 24)}, NOON)

    assert slot_at(slots, NOON).start == NOON


def test_picks_the_cheapest_hours_ahead():
    prices = [0.30] * 24
    prices[13] = 0.05  # cheapest
    prices[14] = 0.08
    slots = parse_forecast({"raw_today": hourly(prices)}, NOON)

    chosen = cheapest_slots(slots, NOON, cheap_hours=2)

    assert [s.start.hour for s in chosen] == [13, 14]


def test_never_looks_backwards():
    """An hour that was cheap this morning is no use now."""
    prices = [0.30] * 24
    prices[3] = 0.01
    slots = parse_forecast({"raw_today": hourly(prices)}, NOON)

    chosen = cheapest_slots(slots, NOON, cheap_hours=1)

    assert all(s.start >= NOON for s in chosen)


def test_ranks_over_a_rolling_window_not_over_everything_known():
    """Ranking across 48 h could leave the packs flat all evening today."""
    today = [0.30] * 24
    tomorrow = [0.05] * 24  # tomorrow is cheaper across the board
    slots = parse_forecast(
        {
            "raw_today": hourly(today),
            "raw_tomorrow": hourly(tomorrow, MIDNIGHT + timedelta(days=1)),
        },
        NOON,
    )

    chosen = cheapest_slots(slots, NOON, cheap_hours=3, window_hours=8)

    assert all(s.start < NOON + timedelta(hours=8) for s in chosen)


def test_quarter_hourly_prices_still_mean_hours():
    slots = parse_forecast({"today": [0.1] * 96}, NOON)

    # two hours of cheap slots is eight quarters, not two
    assert len(cheapest_slots(slots, NOON, cheap_hours=2)) == 8


def test_is_cheap_now_is_true_only_inside_a_chosen_slot():
    prices = [0.30] * 24
    prices[12] = 0.05  # now
    prices[20] = 0.06  # the next-cheapest, so 13:00 is never the bargain
    slots = parse_forecast({"raw_today": hourly(prices)}, NOON)

    assert is_cheap_now(slots, NOON, cheap_hours=1) is True
    assert is_cheap_now(slots, NOON + timedelta(hours=1), cheap_hours=1) is False


def test_identical_prices_mean_the_earliest_slot_wins():
    """With nothing to choose between them, charging now is as good as later."""
    slots = parse_forecast({"raw_today": hourly([0.25] * 24)}, NOON)

    assert is_cheap_now(slots, NOON, cheap_hours=1) is True


def test_no_forecast_means_never_cheap():
    assert is_cheap_now([], NOON, cheap_hours=4) is False


def test_zero_cheap_hours_disables_it():
    slots = parse_forecast({"raw_today": hourly([0.1] * 24)}, NOON)

    assert cheapest_slots(slots, NOON, cheap_hours=0) == []
    assert is_cheap_now(slots, NOON, cheap_hours=0) is False

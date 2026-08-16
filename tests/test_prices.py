"""Reading a forecast out of whatever price sensor a site happens to have.

Each Dutch supplier's integration publishes upcoming prices in its own shape, so
these tests are mostly about recognising shapes rather than integrations - a
site should be able to switch supplier by pointing at a different sensor.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from custom_components.battery_management.prices import (
    Slot,
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


# -- the missing reference point ---------------------------------------------


def _day(prices: list[float]) -> list[Slot]:
    start = datetime(2026, 8, 16, tzinfo=timezone.utc)
    return [
        Slot(start + timedelta(hours=h), start + timedelta(hours=h + 1), p)
        for h, p in enumerate(prices)
    ]


# the primary site's own day, read off the card: cheap midday, dear evening
REAL_DAY = [
    0.28, 0.26, 0.25, 0.24, 0.24, 0.25, 0.27, 0.28, 0.27, 0.24, 0.21, 0.19,
    0.17, 0.162, 0.17, 0.20, 0.24, 0.305, 0.32, 0.34, 0.36, 0.37, 0.379, 0.33,
]


def test_the_dearest_hour_of_the_day_is_never_called_cheap():
    """The fault, reported from the primary site.

    "The cheapest three of what is left" always finds three, however dear they
    are. By 22:00, with only today published, the only hours left were the two
    most expensive of the day - and the dashboard offered to charge on them.
    """
    slots = _day(REAL_DAY)
    at_ten_pm = slots[22].start + timedelta(minutes=1)

    without = cheapest_slots(slots, at_ten_pm, 3.0)
    with_margin = cheapest_slots(slots, at_ten_pm, 3.0, 24.0, 0.05)

    assert any(s.price == 0.379 for s in without), "the old fault has moved"
    assert with_margin == [], with_margin


def test_a_genuine_bargain_still_qualifies():
    """The margin must not simply switch buying off."""
    slots = _day(REAL_DAY)
    at_one = slots[13].start + timedelta(minutes=1)

    chosen = cheapest_slots(slots, at_one, 3.0, 24.0, 0.05)

    assert [round(s.price, 3) for s in chosen] == [0.162, 0.17, 0.20]


def test_a_flat_day_is_worth_nothing_whichever_hour_you_pick():
    """No spread, no saving - and the round trip still costs."""
    slots = _day([0.25] * 24)
    now = slots[0].start + timedelta(minutes=1)

    assert cheapest_slots(slots, now, 3.0) != []          # a rank always answers
    assert cheapest_slots(slots, now, 3.0, 24.0, 0.05) == []


def test_the_margin_is_measured_against_what_the_charge_replaces():
    """Not against the peak, and not against the average.

    The reference is the *cheapest of the dear hours* - the weakest hour the
    stored energy would actually displace - so qualifying means the trade pays
    even in its worst case. Here one 0.90 spike would justify almost anything
    if the peak were the yardstick; against the 0.30 that three hours of charge
    would really be replacing, only the genuine bargain survives.
    """
    slots = _day([0.24, 0.26] + [0.30] * 21 + [0.90])
    now = slots[0].start + timedelta(minutes=1)

    chosen = cheapest_slots(slots, now, 3.0, 24.0, 0.05)

    assert [round(s.price, 2) for s in chosen] == [0.24]


def test_zero_margin_restores_the_plain_ranking():
    slots = _day(REAL_DAY)
    now = slots[22].start + timedelta(minutes=1)

    assert cheapest_slots(slots, now, 3.0, 24.0, 0.0) == cheapest_slots(
        slots, now, 3.0
    )

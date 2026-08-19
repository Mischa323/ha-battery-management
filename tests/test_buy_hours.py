"""Spending the cheap hours in the right order.

`cheapest_slots` answers "which hours are cheap enough to buy on". It does not
answer "which of them will we actually use", and for a long time nothing did:
the coordinator bought on whichever cheap hour came round first, which is the
dearest of the set by construction.

Reported from the primary site on 2026-08-19. Dynamic mode was switched on at
12:20 with `cheap_hours` at 4; the packs went from 4 % to 50 % in the hour that
followed and were done. The day's cheapest hour was 14:00 at 0.284, and it was
never used - 12:00 was also in the cheapest four, and it happened to be first.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from custom_components.battery_management.prices import (
    Slot,
    cheapest_slots,
    slots_to_buy,
)

UTC = timezone.utc
NOON = datetime(2026, 8, 5, 12, 0, tzinfo=UTC)


def series(prices, start=NOON, minutes=60):
    """Consecutive slots of `minutes` each, starting at `start`."""
    return [
        Slot(
            start + timedelta(minutes=minutes * i),
            start + timedelta(minutes=minutes * (i + 1)),
            price,
        )
        for i, price in enumerate(prices)
    ]


def hours_of(slots):
    return [slot.start.hour for slot in slots]


# the shape of the reported day: four hours that all clear the ranking, with
# the cheapest of them last
FOUR_CHEAP = [0.30, 0.29, 0.285, 0.284] + [0.60] * 20


def test_without_a_measured_need_every_cheap_hour_still_qualifies():
    """The old behaviour, and the right one when the need is unknowable.

    Without the empty-to-full time there is no way to say how many hours are
    needed, and guessing one would decide which hours get bought.
    """
    slots = series(FOUR_CHEAP)

    assert slots_to_buy(slots, NOON, 4.0, needed_hours=None) == cheapest_slots(
        slots, NOON, 4.0
    )
    assert hours_of(slots_to_buy(slots, NOON, 4.0, needed_hours=None)) == [
        12, 13, 14, 15
    ]


def test_one_hour_of_need_spends_the_cheapest_hour_not_the_first():
    """The reported fault, pinned."""
    slots = series(FOUR_CHEAP)

    chosen = slots_to_buy(slots, NOON, 4.0, needed_hours=1.0)

    assert hours_of(chosen) == [15]  # 0.284, the cheapest of the four
    assert hours_of(cheapest_slots(slots, NOON, 4.0))[0] == 12  # what it used to pick


def test_two_hours_of_need_take_the_two_cheapest():
    slots = series(FOUR_CHEAP)

    assert hours_of(slots_to_buy(slots, NOON, 4.0, needed_hours=2.0)) == [14, 15]


def test_a_part_hour_of_need_still_occupies_a_whole_hour():
    """Rounded up: the packs draw what they draw, and half an hour of need
    cannot be met by half an hour of a slot that is priced by the hour."""
    slots = series(FOUR_CHEAP)

    assert hours_of(slots_to_buy(slots, NOON, 4.0, needed_hours=0.1)) == [15]
    assert hours_of(slots_to_buy(slots, NOON, 4.0, needed_hours=1.1)) == [14, 15]


def test_no_need_buys_nothing():
    """A full pack is not a reason to paint hours green on a dashboard."""
    slots = series(FOUR_CHEAP)

    assert slots_to_buy(slots, NOON, 4.0, needed_hours=0.0) == []


def test_more_need_than_cheap_hours_takes_all_of_them_and_no_more():
    """The set never grows past what cleared the ranking and the margin - an
    empty pack is not a reason to buy at any price."""
    slots = series(FOUR_CHEAP)

    chosen = slots_to_buy(slots, NOON, 4.0, needed_hours=9.0)

    assert hours_of(chosen) == [12, 13, 14, 15]


def test_the_margin_still_applies_before_the_need_is_considered():
    """Narrowing happens inside the cheap set, so a flat day still buys
    nothing however empty the packs are."""
    flat = series([0.30] * 24)

    assert slots_to_buy(flat, NOON, 4.0, 24.0, 0.05, needed_hours=4.0) == []


def test_quarter_hourly_need_is_counted_in_slots_not_hours():
    """A 96-slot feed: one hour of need is four slots, not one."""
    prices = [0.30, 0.30, 0.29, 0.29, 0.28, 0.28, 0.27, 0.27] + [0.60] * 88
    slots = series(prices, minutes=15)

    chosen = slots_to_buy(slots, NOON, 2.0, needed_hours=1.0)

    assert len(chosen) == 4
    assert [s.price for s in chosen] == [0.28, 0.28, 0.27, 0.27]


def test_the_chosen_hours_come_back_in_time_order():
    """Ranked by price, returned by clock: a caller drawing them must not have
    to re-sort, and one asking "is now among them" must not care."""
    slots = series(FOUR_CHEAP)

    chosen = slots_to_buy(slots, NOON, 4.0, needed_hours=3.0)

    assert chosen == sorted(chosen, key=lambda slot: slot.start)


def test_an_empty_forecast_is_not_a_reason_to_buy():
    assert slots_to_buy([], NOON, 4.0, needed_hours=2.0) == []

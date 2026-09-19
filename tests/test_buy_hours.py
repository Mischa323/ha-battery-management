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
    cheapest_on_day,
    cheapest_slots,
    pick_cheapest,
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


# -- when two hours cost exactly the same -------------------------------------
#
# Asked by the owner on 2026-09-19, looking at a day with 0.129 at 05:00 and
# 0.129 again at 11:00, six such hours in all against a budget of four. A rank
# has to break that tie somehow, and "somehow" is the word worth removing: an
# order nobody chose is an order that can change under a refactor without a
# single test noticing.
#
# The rule is the earliest of the tied slots. Energy in the packs sooner covers
# more of whatever the day turns out to hold, and the buy ceiling - low in the
# morning, when most of the sun is still to come - is what stops that filling
# them before the sun can.


def test_slots_that_cost_the_same_are_taken_earliest_first():
    prices = [0.20, 0.10, 0.30, 0.10, 0.40, 0.10]

    assert hours_of(cheapest_slots(series(prices), NOON, cheap_hours=2)) == [13, 15]


def test_the_earlier_block_is_filled_before_the_later_one_is_touched():
    """The owner's day: two blocks at an identical price, the budget smaller
    than the two together. The first is spent whole, the second takes the
    remainder - rather than four hours spread evenly across both."""
    # three hours at 0.129, three dear, three more at 0.129, then dear
    prices = [0.129] * 3 + [0.25] * 3 + [0.129] * 3 + [0.42] * 15

    picked = hours_of(cheapest_slots(series(prices), NOON, cheap_hours=4))

    assert picked == [12, 13, 14, 18]


def test_the_need_takes_the_earliest_of_the_tied_hours():
    """And once the need is known it is the front of that block, not a sample
    from across it."""
    prices = [0.129] * 3 + [0.25] * 3 + [0.129] * 3 + [0.42] * 15

    picked = slots_to_buy(series(prices), NOON, cheap_hours=4, needed_hours=2)

    assert hours_of(picked) == [12, 13]


def test_a_missed_block_is_not_lost_because_the_window_rolls():
    """The point that makes earliest-first safe rather than greedy: if the
    packs were not empty enough at 05:00, the 11:00 block is still there when
    the window has moved past the first one."""
    prices = [0.129] * 3 + [0.25] * 3 + [0.129] * 3 + [0.42] * 15
    slots = series(prices)
    later = NOON + timedelta(hours=6)  # the first block has gone

    picked = slots_to_buy(slots, later, cheap_hours=4, needed_hours=2)

    assert hours_of(picked) == [18, 19]


def test_the_tie_is_broken_on_time_and_not_on_the_order_they_arrived():
    """The rule has to be the slots' own doing, not the caller's.

    `slots_in_window` happens to hand them over in time order, so a rank on
    price alone would look identical through that path and the tie-break would
    be resting on a coincidence. `pick_cheapest` is also called directly - by
    the chart - so it is pinned here on its own, with the slots shuffled.
    """
    prices = [0.20, 0.10, 0.30, 0.10, 0.40, 0.10]
    shuffled = list(reversed(series(prices)))

    assert hours_of(pick_cheapest(shuffled, cheap_hours=2)) == [13, 15]


def test_a_flat_day_is_simply_taken_from_the_front():
    """Every hour identical: nothing distinguishes them but when they are."""
    picked = cheapest_slots(series([0.2] * 24), NOON, cheap_hours=3)

    assert hours_of(picked) == [12, 13, 14]


# -- drawing a tie, without spending one --------------------------------------
#
# Asked for straight after the tie rule above: with six hours at 0.129 against
# a budget of four, the chart painted four green and two grey at an identical
# price. Nothing told them apart but where the count ran out, and the band is
# worded as a statement about prices - "bij de goedkoopste uren van vandaag" -
# so splitting a tie makes it say something the prices do not.
#
# The band may therefore run wider than the configured hours. What must not is
# the buying: those hours are what the owner agreed to spend.


def test_the_drawn_band_does_not_split_a_tie():
    prices = [0.129] * 3 + [0.25] * 3 + [0.129] * 3 + [0.42] * 15
    day = series(prices)

    drawn = cheapest_on_day(day, NOON, NOON + timedelta(hours=24), cheap_hours=4)

    # all six, not the four the budget would have stopped at
    assert hours_of(drawn) == [12, 13, 14, 18, 19, 20]


def test_but_the_budget_is_still_the_budget_when_buying():
    """The same day, the same tie, through the deciding path: four hours were
    agreed to and four is what comes back."""
    prices = [0.129] * 3 + [0.25] * 3 + [0.129] * 3 + [0.42] * 15

    assert len(cheapest_slots(series(prices), NOON, cheap_hours=4)) == 4


def test_a_band_with_no_tie_at_its_edge_is_unchanged():
    """Distinct prices: the band is exactly the hours configured, as before."""
    prices = [0.10, 0.12, 0.14, 0.16, 0.50] + [0.60] * 19
    day = series(prices)

    drawn = cheapest_on_day(day, NOON, NOON + timedelta(hours=24), cheap_hours=3)

    assert hours_of(drawn) == [12, 13, 14]


def test_a_tie_below_the_edge_does_not_drag_dearer_hours_in():
    """Only the price the count stopped on is kept whole - an hour costing more
    is still out, however close."""
    prices = [0.10, 0.10, 0.10, 0.11, 0.11] + [0.60] * 19
    day = series(prices)

    drawn = cheapest_on_day(day, NOON, NOON + timedelta(hours=24), cheap_hours=2)

    assert hours_of(drawn) == [12, 13, 14]


def test_a_flat_day_is_still_painted_nothing():
    """Every hour ties with every other, so keeping ties would paint the lot.
    The margin is what stops it, and this is the case that proves it still does.
    """
    day = series([0.25] * 24)

    drawn = cheapest_on_day(
        day, NOON, NOON + timedelta(hours=24), cheap_hours=4, min_margin=0.05
    )

    assert drawn == []

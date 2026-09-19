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

import pytest

from custom_components.battery_management.prices import (
    Slot,
    cheaper_next_day,
    cheapest_on_day,
    cheapest_slots,
    next_dear_start,
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


# -- not ranking across the peak ----------------------------------------------
#
# Reported on 2026-09-19. The packs sat at 5 % for four and a quarter hours,
# charged for 75 minutes, and stopped at 31 % on a quarter boundary with the
# price unchanged, the ceiling at 79 % and the market price at -0.0012. No dear
# quarter had happened yet that day, so the peak was still ahead.
#
# `slots_to_buy` ranked over the whole 24-hour window. Tomorrow's cheaper
# quarters took the budget - and tomorrow arrives *after* tonight's peak, so
# that energy cannot serve it. The two were never substitutes and should never
# have been ranked against each other.


def peak_day():
    """Cheap now, a peak this evening, and a cheaper day after it."""
    return series(
        [0.13] * 4      # 12:00-16:00, cheap, and before the peak
        + [0.45] * 4    # 16:00-20:00, the peak
        + [0.20] * 4    # 20:00-24:00
        + [0.08] * 12   # tomorrow, cheaper than anything today
    )


def test_it_does_not_defer_past_the_peak():
    """The reported fault. Unbounded, the budget goes to tomorrow's 0.08 and
    nothing is bought before a peak the packs have to cover."""
    slots = peak_day()

    unbounded = slots_to_buy(slots, NOON, cheap_hours=5, needed_hours=2)
    assert hours_of(unbounded) == [0, 1]  # tomorrow, after the peak

    cutoff = next_dear_start(slots, NOON, expensive_hours=4)
    bounded = slots_to_buy(slots, NOON, cheap_hours=5, needed_hours=2, until=cutoff)
    assert hours_of(bounded) == [12, 13]  # today, before it


def test_the_peak_boundary_is_where_the_dear_hours_start():
    cutoff = next_dear_start(peak_day(), NOON, expensive_hours=4)

    assert cutoff == NOON + timedelta(hours=4)


def test_no_peak_ahead_leaves_the_window_alone():
    """A flat day has nothing to be caught short of."""
    assert next_dear_start(series([0.20] * 24), NOON, expensive_hours=0) is None


def test_the_margin_is_still_measured_against_the_peak_it_saves_for():
    """The interaction that would have made this worse than the fault.

    Cutting the window short removes the dear hours - which are exactly what
    the margin compares against. Measured against what is left, the cheap hours
    are only being compared with each other, every one fails the margin, and
    the packs buy nothing at all before the peak.
    """
    slots = peak_day()
    cutoff = next_dear_start(slots, NOON, expensive_hours=4)

    picked = slots_to_buy(
        slots, NOON, cheap_hours=5, min_margin=0.05, needed_hours=2, until=cutoff
    )

    # 0.13 + 0.05 clears the 0.45 peak it is bought for
    assert hours_of(picked) == [12, 13]


def test_a_cheap_stretch_that_beats_nothing_still_fails_the_margin():
    """The bound must not turn the margin off, only point it at the right set."""
    flat = series([0.20] * 4 + [0.21] * 20)
    cutoff = next_dear_start(flat, NOON, expensive_hours=4)

    picked = slots_to_buy(
        flat, NOON, cheap_hours=5, min_margin=0.05, needed_hours=2, until=cutoff
    )

    assert picked == []


# -- is the next day cheaper --------------------------------------------------


def test_a_cheaper_tomorrow_reads_positive():
    slots = series([0.30] * 12 + [0.10] * 12)
    step = cheaper_next_day(slots, NOON, NOON + timedelta(hours=12), cheap_hours=4)

    assert step == pytest.approx(0.20)


def test_a_dearer_tomorrow_reads_negative():
    slots = series([0.10] * 12 + [0.30] * 12)
    step = cheaper_next_day(slots, NOON, NOON + timedelta(hours=12), cheap_hours=4)

    assert step == pytest.approx(-0.20)


def test_a_sliver_of_tomorrow_is_not_a_day():
    """Seen from 02:00 the window holds two hours of tomorrow, and the cheap
    night they fall in would read as a bargain every single night."""
    slots = series([0.30] * 22 + [0.10] * 2)
    step = cheaper_next_day(slots, NOON, NOON + timedelta(hours=22), cheap_hours=4)

    assert step is None


def test_nothing_to_compare_on_one_side_is_not_a_verdict():
    slots = series([0.30] * 24)
    assert cheaper_next_day(slots, NOON, NOON + timedelta(hours=48), 4) is None

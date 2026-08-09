"""The arithmetic behind the fuse protection, on its own.

`phases.py` is pure, so this is where the awkward cases get pinned down without
a coordinator, a config entry or a fake house in the way.
"""
from __future__ import annotations

from custom_components.battery_management.phases import (
    attribute_phase,
    effective_limit_w,
    other_load,
    room,
    unit_ceilings,
)

# 25 A x 230 V, 10 % kept back
LIMIT = effective_limit_w(25, 230, 10)


def test_the_margin_comes_off_the_fuse():
    assert LIMIT == 5175.0
    assert effective_limit_w(25, 230, 0) == 5750.0


def test_a_silly_margin_cannot_produce_a_negative_limit():
    assert effective_limit_w(25, 230, 150) == 0.0


# -- what the leg would read without us --------------------------------------


def test_our_own_discharge_is_added_back():
    """The meter is low *because* we are pushing into it."""
    # leg reads 200 W while we discharge 1500 W into it
    assert other_load({1: 200.0}, {1: 1500.0}) == {1: 1700.0}


def test_our_own_charging_is_taken_back_off():
    # leg reads 3000 W while we charge at 2500 W of that
    assert other_load({1: 3000.0}, {1: -2500.0}) == {1: 500.0}


def test_a_leg_we_are_not_on_is_left_alone():
    assert other_load({1: 400.0, 2: 900.0}, {1: 1000.0}) == {1: 1400.0, 2: 900.0}


# -- room ---------------------------------------------------------------------


def test_a_quiet_leg_has_room_in_both_directions():
    discharge, charge = room({1: 0.0}, LIMIT)
    assert discharge[1] == LIMIT
    assert charge[1] == LIMIT


def test_a_loaded_leg_has_little_charging_room_and_plenty_to_discharge_into():
    discharge, charge = room({1: 4500.0}, LIMIT)
    assert charge[1] == 675.0            # 4500 + 675 = the fuse
    assert discharge[1] == 9675.0        # down to 5175 W of export


def test_export_counts_too():
    """A fuse carries net current; a leg full of sun is as close to it."""
    discharge, charge = room({1: -5000.0}, LIMIT)
    assert discharge[1] == 175.0         # almost nothing left to push out
    assert charge[1] == 10175.0


def test_a_leg_already_over_its_fuse_offers_nothing():
    discharge, charge = room({1: 6000.0}, LIMIT)
    assert charge[1] == 0.0
    assert discharge[1] > 0              # helping is still allowed


# -- turning room into per-unit ceilings --------------------------------------

MAXES = {"a": 3500.0, "b": 3500.0}


def test_each_unit_gets_its_own_legs_room():
    ceilings = unit_ceilings(
        ["a", "b"], {"a": 1, "b": 2}, {1: 700.0, 2: 5000.0}, MAXES
    )
    assert ceilings == {"a": 700.0, "b": 3500.0}   # b is capped by its rating


def test_units_sharing_a_leg_split_it():
    ceilings = unit_ceilings(
        ["a", "b"], {"a": 1, "b": 1}, {1: 2000.0, 2: 5000.0}, MAXES
    )
    assert ceilings == {"a": 1000.0, "b": 1000.0}


def test_an_unplaced_unit_is_treated_as_being_on_the_worst_leg():
    """Guessing wrong here drops a fuse, so it does not guess."""
    ceilings = unit_ceilings(["a"], {"a": None}, {1: 400.0, 2: 5000.0}, MAXES)
    assert ceilings == {"a": 400.0}


def test_an_unplaced_unit_only_gets_what_the_placed_ones_left():
    """It might be sitting on the same leg as the one already claiming it."""
    ceilings = unit_ceilings(
        ["a", "b"], {"a": 1, "b": None}, {1: 3000.0, 2: 5000.0}, MAXES
    )
    assert ceilings["a"] == 3000.0
    assert ceilings["b"] == 0.0


def test_a_quiet_house_never_notices_the_protection():
    """The point: this only bites when it was going to matter anyway."""
    ceilings = unit_ceilings(
        ["a", "b"], {"a": None, "b": None}, {1: 4800.0, 2: 4800.0}, MAXES
    )
    assert ceilings == {"a": 2400.0, "b": 2400.0}


def test_a_leg_that_dropped_out_of_the_readings_is_not_assumed_empty():
    """Unreadable is not zero - the unit falls back to the legs we can see."""
    ceilings = unit_ceilings(["a"], {"a": 3}, {1: 900.0, 2: 5000.0}, MAXES)
    assert ceilings == {"a": 900.0}    # placed on a leg we cannot read -> worst case


# -- attribution ---------------------------------------------------------------


def test_recognises_the_leg_that_moved():
    phase, detail = attribute_phase(
        {1: 300.0, 2: 400.0, 3: 500.0},
        {1: 320.0, 2: 3400.0, 3: 480.0},
        3000.0,
        charging=True,
    )
    assert phase == 2
    assert detail["reason"] == "ok"


def test_a_discharge_probe_looks_the_other_way():
    phase, _ = attribute_phase(
        {1: 300.0, 2: 3400.0}, {1: 310.0, 2: 400.0}, 3000.0, charging=False
    )
    assert phase == 2


def test_refuses_when_the_pack_never_moved():
    """No answer beats a wrong one - the caller simply tries again later."""
    phase, detail = attribute_phase(
        {1: 300.0, 2: 400.0}, {1: 305.0, 2: 402.0}, 3000.0, charging=True
    )
    assert phase is None
    assert detail["reason"] == "too_small"


def test_refuses_when_two_legs_moved_together():
    """Something else switched on halfway through; do not attribute it to us."""
    phase, detail = attribute_phase(
        {1: 0.0, 2: 0.0}, {1: 2000.0, 2: 1800.0}, 3000.0, charging=True
    )
    assert phase is None
    assert detail["reason"] == "not_distinct"


def test_a_load_switching_off_elsewhere_does_not_veto_a_clear_winner():
    phase, _ = attribute_phase(
        {1: 0.0, 2: 900.0}, {1: 3000.0, 2: 0.0}, 3000.0, charging=True
    )
    assert phase == 1


def test_records_what_it_saw_even_when_it_gives_up():
    """A probe that refused has to be explainable afterwards."""
    _, detail = attribute_phase({1: 0.0}, {1: 10.0}, 3000.0, charging=True)
    assert detail["deltas"] == {1: 10.0}
    assert detail["probe_w"] == 3000.0
    assert detail["charging"] is True


def test_no_readings_at_all_is_not_a_crash():
    phase, detail = attribute_phase({}, {}, 3000.0, charging=True)
    assert phase is None
    assert detail["reason"] == "no_readings"

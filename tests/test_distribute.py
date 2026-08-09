"""Tests for the pure SoC-weighted split in `BatteryCoordinator._distribute`."""
from __future__ import annotations

from custom_components.battery_management.coordinator import BatteryCoordinator

MIN_OUTPUT = 150.0
UNIT_MAX = 3500.0


def distribute(mag, weights, umax=None, min_output=MIN_OUTPUT):
    """Default every unit to a full Max AC ceiling unless a test overrides it."""
    return BatteryCoordinator._distribute(
        mag, weights, umax or {u: UNIT_MAX for u in weights}, min_output
    )


def test_splits_proportionally_to_weight():
    assert distribute(3000, {"a": 1.0, "b": 2.0}) == {"a": 1000, "b": 2000}


def test_conserves_the_requested_total():
    # 75/55 is the real weighting for SoC 80% and 60% against a 5% floor
    result = distribute(500, {"a": 75.0, "b": 55.0})
    assert sum(result.values()) == 500
    assert result["a"] > result["b"]  # the fuller unit does more


def test_clamps_each_unit_to_its_own_maximum():
    result = distribute(1000, {"a": 1.0, "b": 1.0}, umax={"a": 300.0, "b": 3500.0})
    assert result["a"] == 300


def test_redistributes_what_a_ceiling_refused():
    # This used to be pinned the other way round, on the reasoning that dropping
    # the remainder kept the pair from overshooting. It cannot overshoot: only
    # what is left of the request is ever handed on, and the tick has already
    # clamped the request to the sum of the ceilings. What the old behaviour did
    # produce was a steady-state error - a shortfall the integrator cannot
    # correct, because it is already sitting on its own bound.
    assert distribute(7000, {"a": 3.0, "b": 1.0}) == {"a": 3500, "b": 3500}
    assert distribute(1000, {"a": 1.0, "b": 1.0}, umax={"a": 300.0, "b": 3500.0}) == {
        "a": 300,
        "b": 700,
    }


def test_redistribution_still_never_exceeds_the_request():
    result = distribute(1000, {"a": 3.0, "b": 1.0}, umax={"a": 200.0, "b": 3500.0})
    assert sum(result.values()) == 1000
    assert result["a"] == 200


def test_a_request_beyond_every_ceiling_simply_maxes_out():
    result = distribute(99000, {"a": 1.0, "b": 1.0}, umax={"a": 300.0, "b": 900.0})
    assert result == {"a": 300, "b": 900}


def test_consolidates_a_sub_minimum_share_onto_the_larger_unit():
    result = distribute(200, {"a": 1.0, "b": 9.0})
    assert result == {"a": 0, "b": 200}


def test_total_below_min_output_lands_on_exactly_one_unit():
    # Nothing can clear min_output here, so the whole (small) load is
    # consolidated rather than split into two idling shares.
    result = distribute(100, {"a": 75.0, "b": 55.0})
    assert sorted(result.values()) == [0, 100]


def test_skips_units_with_zero_weight():
    # weight 0 == offline, or at its SoC limit
    assert distribute(1000, {"a": 0.0, "b": 1.0}) == {"a": 0, "b": 1000}


def test_returns_all_zero_when_nothing_is_requested():
    assert distribute(0, {"a": 1.0, "b": 1.0}) == {"a": 0, "b": 0}
    assert distribute(-500, {"a": 1.0, "b": 1.0}) == {"a": 0, "b": 0}


def test_returns_all_zero_when_every_unit_is_ineligible():
    assert distribute(1000, {"a": 0.0, "b": 0.0}) == {"a": 0, "b": 0}


def test_supports_more_than_two_units():
    umax = {"a": 3500.0, "b": 3500.0, "c": 3500.0}
    result = distribute(3000, {"a": 1.0, "b": 1.0, "c": 1.0}, umax=umax)
    assert result == {"a": 1000, "b": 1000, "c": 1000}


def test_consolidation_always_picks_the_highest_weight_unit():
    """The fullest pack (discharge) / emptiest (charge) is last one standing."""
    # 80 % and 60 % SoC against a 5 % floor
    assert distribute(200, {"a": 75.0, "b": 55.0}) == {"a": 200, "b": 0}
    # and the other way round, to prove it follows weight and not order
    assert distribute(200, {"a": 55.0, "b": 75.0}) == {"a": 0, "b": 200}


def test_units_join_in_weight_order_as_the_setpoint_ramps():
    """Regression: the load used to ping-pong between packs on every tick.

    Sweeping the magnitude upward, a unit that has become active must never go
    back to idle - otherwise the packs micro-cycle exactly as the min-output
    floor is meant to prevent.
    """
    weights = {"a": 75.0, "b": 55.0}
    seen_active: set[str] = set()

    for mag in range(0, 2000, 10):
        result = distribute(mag, weights)
        active = {u for u, watts in result.items() if watts > 0}
        assert not (seen_active - active), (
            f"unit dropped out again at {mag} W: {seen_active} -> {active}"
        )
        seen_active |= active

    # and the heavier unit is the one that starts alone
    assert distribute(200, weights)["a"] > 0
    assert distribute(200, weights)["b"] == 0


def test_drops_the_lowest_weight_unit_first_with_three():
    # 900 W over three equal-ish units: 300/300/300 clears the floor
    assert distribute(900, {"a": 1.0, "b": 1.0, "c": 1.0}) == {
        "a": 300,
        "b": 300,
        "c": 300,
    }
    # 300 W would be 100 each, so the two lightest are dropped in turn
    assert distribute(300, {"a": 3.0, "b": 2.0, "c": 1.0}) == {
        "a": 300,
        "b": 0,
        "c": 0,
    }


def test_ties_resolve_deterministically():
    weights = {"b": 50.0, "a": 50.0}
    assert distribute(200, weights) == distribute(200, dict(reversed(list(weights.items()))))

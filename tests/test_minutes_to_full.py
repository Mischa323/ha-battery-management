"""Estimating how long a fast charge would take.

The integration does the arithmetic because it knows the state of charge and the
limits; an automation decides *when* to press the button. Same split as the
schedule blueprints.
"""
from __future__ import annotations

from custom_components.battery_management.const import CONF_FULL_CHARGE_MINUTES

# 240 minutes empty -> full, i.e. 1 % per 2.4 minutes
MEASURED = {CONF_FULL_CHARGE_MINUTES: 240}


async def test_unavailable_until_the_duration_has_been_measured(build_system):
    """Guessing would be worse: the whole point is arriving full on time."""
    system = build_system(units=(("093", 50.0), ("052", 50.0)))

    assert system.coordinator.minutes_to_full() is None


async def test_estimates_from_the_state_of_charge(build_system):
    system = build_system(units=(("093", 50.0), ("052", 50.0)), **MEASURED)

    # 50 % missing of a 240 minute charge
    assert system.coordinator.minutes_to_full() == 120


async def test_uses_the_slowest_pack_not_the_sum(build_system):
    """They charge in parallel, so the emptiest one sets the finish time."""
    system = build_system(units=(("093", 80.0), ("052", 20.0)), **MEASURED)

    assert system.coordinator.minutes_to_full() == 192  # 80 % of 240, not 240


async def test_is_zero_when_everything_is_already_full(build_system):
    system = build_system(units=(("093", 100.0), ("052", 100.0)), **MEASURED)

    assert system.coordinator.minutes_to_full() == 0


async def test_respects_a_charge_limit_below_100(build_system):
    system = build_system(
        units=(("093", 50.0), ("052", 50.0)), charge_limit=80.0, **MEASURED
    )

    # only 30 % to go, not 50 %
    assert system.coordinator.minutes_to_full() == 72


async def test_ignores_an_offline_pack(build_system):
    system = build_system(units=(("093", 50.0), ("052", None)), **MEASURED)

    assert system.coordinator.minutes_to_full() == 120


async def test_unavailable_when_no_pack_is_reachable(build_system):
    system = build_system(units=(("093", None), ("052", None)), **MEASURED)

    assert system.coordinator.minutes_to_full() is None


async def test_available_while_the_coordinator_is_switched_off(build_system):
    """You want to plan ahead without turning the coordinator on first."""
    system = build_system(units=(("093", 40.0), ("052", 40.0)), enabled=False, **MEASURED)

    assert system.coordinator.minutes_to_full() == 144


# -- the same question at the rate actually being commanded ------------------


async def test_at_current_rate_is_slower_than_at_full_power(build_system):
    """The owner's observation: "79 minutes" is enthusiastic next to a pack
    trickling in from the sun. The headline figure assumes a fast charge."""
    system = build_system(grid=-1600, units=(("093", 50.0), ("052", 50.0)), **MEASURED)

    await system.coordinator._async_tick(None)

    assert system.coordinator.minutes_to_full() == 120           # at 2 x 3500 W
    at_rate = system.coordinator.minutes_to_full_at_current_rate()
    assert at_rate > 400                                          # at 2 x 800 W


async def test_at_current_rate_is_unknown_while_discharging(build_system):
    """"Never" is the honest answer, and a sensor cannot say it."""
    system = build_system(grid=500, **MEASURED)

    await system.coordinator._async_tick(None)

    assert system.coordinator.minutes_to_full_at_current_rate() is None


async def test_at_current_rate_needs_the_measurement_too(build_system):
    system = build_system(grid=-1600)

    await system.coordinator._async_tick(None)

    assert system.coordinator.minutes_to_full_at_current_rate() is None


async def test_the_slowest_pack_sets_the_pace_here_as_well(build_system):
    system = build_system(grid=-2000, units=(("093", 90.0), ("052", 20.0)), **MEASURED)

    await system.coordinator._async_tick(None)

    # the emptier pack gets the larger share, but still has much further to go
    assert system.coordinator.minutes_to_full_at_current_rate() > (
        system.coordinator.minutes_to_full()
    )

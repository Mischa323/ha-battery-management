"""A floor under the total, not only under each pack's share.

`_distribute` consolidates small shares onto one unit, which stops two packs
prodding at thirty watts each. But once one unit was left it received whatever
was asked, so a setpoint of 63 W went out in full - below what a Max AC can
actually hold. The pack's own integration says so: "the input power of 63 W is
below the optimal operating range, control accuracy may deviate". Reported from
the primary site with the floor set to 100 W, which is what showed that this
guarded the share and never the sum.

The setpoint itself is deliberately untouched. It is the integrator state, so
clamping it here would wind it back down and make the floor impossible to climb
over: a persistent small import has to keep accumulating until it clears the
floor honestly.
"""
from __future__ import annotations

import pytest

from .conftest import GRID_SENSOR
from custom_components.battery_management.const import (
    MIN_OUTPUT_RELEASE,
    POLICY_DEADBAND,
    POLICY_MIN_OUTPUT,
)

FLOOR = 100


def targets(system) -> dict:
    return system.hass.services.targets_set()


async def settle(system, grid: float) -> None:
    """Move the meter and run one tick.

    Deliberately driven through the meter rather than by assigning the
    setpoint: the setpoint is the integrator, so a tick adds the fresh error
    to whatever was set and the assignment does not survive it. Writing these
    the other way round is what made the first version of this file assert
    against a number the coordinator had already moved past.
    """
    system.hass.states.set(GRID_SENSOR, grid)
    await system.coordinator._async_tick(None)


async def test_a_setpoint_below_the_floor_commands_nothing(build_system):
    """The reported failure: 63 W asked of a pack that cannot hold it."""
    system = build_system(grid=63, min_output=FLOOR)

    await settle(system, 63)

    assert set(targets(system).values()) == {0}
    assert system.coordinator.status == "idle"


async def test_the_setpoint_is_not_wound_back(build_system):
    """Withhold the command, never the accumulation.

    The setpoint is the integrator. Clamping it here would erase the pressure
    that a persistent small import is building, and the packs could then never
    climb over the floor at all - they would sit at zero while the meter
    quietly imported all day.
    """
    system = build_system(grid=63, min_output=FLOOR)

    await settle(system, 63)

    assert abs(system.coordinator.setpoint) > 0


async def test_it_engages_once_the_setpoint_clears_the_floor(build_system):
    system = build_system(grid=400, min_output=FLOOR)

    await settle(system, FLOOR + 50)

    assert sum(targets(system).values()) > 0


async def test_it_holds_on_through_the_hysteresis_band(build_system):
    """Engaging and releasing at one number is how you get chatter.

    A setpoint resting near the floor would switch the packs on and off every
    tick, which is the micro-cycling the floor exists to prevent.
    """
    system = build_system(grid=400, min_output=FLOOR)
    await settle(system, FLOOR + 50)
    assert sum(targets(system).values()) > 0
    engaged = system.coordinator.setpoint

    # inside the band: below the engage point, above the release point
    await settle(system, FLOOR * 0.9 - engaged)

    assert FLOOR * MIN_OUTPUT_RELEASE < system.coordinator.setpoint < FLOOR
    assert sum(targets(system).values()) > 0


async def test_it_releases_below_the_hysteresis_band(build_system):
    system = build_system(grid=400, min_output=FLOOR)
    await settle(system, FLOOR + 50)
    engaged = system.coordinator.setpoint

    await settle(system, FLOOR * MIN_OUTPUT_RELEASE - 5 - engaged)

    assert system.coordinator.setpoint < FLOOR * MIN_OUTPUT_RELEASE
    assert set(targets(system).values()) == {0}


async def test_it_starts_idle_after_a_restart(build_system):
    """A restart must not open with a command the packs cannot hold.

    The setpoint is restored when it is fresh enough, so without this a
    restart at 63 W would go straight back out at 63 W.
    """
    system = build_system(grid=63, min_output=FLOOR)

    await settle(system, FLOOR * 0.9)

    assert set(targets(system).values()) == {0}


async def test_a_floor_of_zero_switches_it_off(build_system):
    """Nothing is mandatory. Zero means command whatever was asked."""
    system = build_system(grid=63, min_output=0)

    await settle(system, 63)

    assert sum(targets(system).values()) > 0


async def test_fast_charge_is_never_floored(build_system):
    """The one place that commands full rating outright."""
    system = build_system(grid=0, min_output=FLOOR)
    await system.coordinator.async_set_fast_charge(True)

    await system.coordinator._async_tick(None)

    assert sum(targets(system).values()) > 0


async def test_it_says_why_it_is_idle(build_system):
    """A flat line at zero must not be a mystery."""
    system = build_system(grid=63, min_output=FLOOR)

    await settle(system, 63)

    assert system.coordinator.active_policy == POLICY_MIN_OUTPUT


async def test_a_better_reason_wins(build_system):
    """The floor is a consequence, not always the cause.

    "The error is too small to act on" already explains why the setpoint is
    where it is; saying "below the minimum output" instead would answer a
    question nobody asked.
    """
    system = build_system(grid=10, min_output=FLOOR, deadband=50)

    await settle(system, 10)

    assert system.coordinator.active_policy == POLICY_DEADBAND

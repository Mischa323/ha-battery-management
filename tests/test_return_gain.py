"""Coming back from a command is not the same risk as going out on one.

Export at this site has exactly one cause: a pack still discharging into a
load that has already gone away. Building the command up fast is what
oscillates (gotcha 3) because every step bets on a pack that answers 10-30 s
later - but winding it *down* cannot run away, since the far end of "less" is
a pack sitting at 0 W. So the two directions get their own gain.
"""
from __future__ import annotations

import pytest

from tests.conftest import GRID_SENSOR, FakeEntry

from custom_components.battery_management.coordinator import (
    BatteryCoordinator,
)

from custom_components.battery_management.const import (
    CONF_BIAS,
    CONF_DEADBAND,
    CONF_KP,
    CONF_KP_RETURN,
    DEFAULT_KP,
    KP_RETURN_FACTOR,
)

pytestmark = pytest.mark.asyncio


async def test_winding_back_down_is_faster_than_going_out(build_system):
    """Same size of error, opposite directions, different step."""
    system = build_system(grid=0, **{CONF_KP: 0.25, CONF_KP_RETURN: 0.5})
    coordinator = system.coordinator

    coordinator.setpoint = 1000.0
    system.hass.states.set(GRID_SENSOR, 1000)                 # still importing: ask for more
    await coordinator._async_tick(None)
    went_out = coordinator.setpoint - 1000.0

    coordinator.setpoint = 1000.0
    system.hass.states.set(GRID_SENSOR, -1000)                # exporting: take it back
    await coordinator._async_tick(None)
    came_back = 1000.0 - coordinator.setpoint

    assert went_out > 0 and came_back > 0
    assert came_back == pytest.approx(2 * went_out, rel=0.05)


async def test_the_same_holds_while_charging(build_system):
    """The rule is about the command shrinking, not about the sign of it."""
    system = build_system(grid=0, **{CONF_KP: 0.25, CONF_KP_RETURN: 0.5})
    coordinator = system.coordinator

    coordinator.setpoint = -1000.0
    system.hass.states.set(GRID_SENSOR, -1000)               # exporting: charge harder
    await coordinator._async_tick(None)
    went_out = -1000.0 - coordinator.setpoint

    coordinator.setpoint = -1000.0
    system.hass.states.set(GRID_SENSOR, 1000)               # importing: stop charging
    await coordinator._async_tick(None)
    came_back = coordinator.setpoint - (-1000.0)

    assert went_out > 0 and came_back > 0
    assert came_back == pytest.approx(2 * went_out, rel=0.05)


async def test_from_rest_it_uses_the_ordinary_gain(build_system):
    """At a setpoint of 0 there is nothing to come back from, either way."""
    system = build_system(grid=0, **{CONF_KP: 0.25, CONF_KP_RETURN: 0.5})
    coordinator = system.coordinator

    for grid in (2000, -2000):
        coordinator.setpoint = 0.0
        system.hass.states.set(GRID_SENSOR, grid)
        await coordinator._async_tick(None)
        error = grid - coordinator._bias
        assert coordinator.setpoint == pytest.approx(0.25 * error, rel=0.01)


async def test_equal_gains_restore_the_old_symmetric_loop(build_system):
    """The escape hatch, for anyone who wants the previous behaviour back."""
    system = build_system(grid=0, **{CONF_KP: 0.3, CONF_KP_RETURN: 0.3})
    coordinator = system.coordinator

    coordinator.setpoint = 1000.0
    system.hass.states.set(GRID_SENSOR, 1000)
    await coordinator._async_tick(None)
    went_out = coordinator.setpoint - 1000.0

    coordinator.setpoint = 1000.0
    system.hass.states.set(GRID_SENSOR, -800)
    await coordinator._async_tick(None)
    came_back = 1000.0 - coordinator.setpoint

    assert went_out == pytest.approx(0.3 * (1000 - coordinator._bias), rel=0.01)
    assert came_back == pytest.approx(0.3 * (800 + coordinator._bias), rel=0.01)


async def test_it_ships_asymmetric_by_default(build_system):
    """Nobody has to find the setting for the export fix to apply.

    The test fixture pins both gains so that tests about other things stay
    symmetric, so this one takes the key back out - which is the state every
    entry created before this option existed is actually in.
    """
    system = build_system(grid=0, **{CONF_KP: DEFAULT_KP})
    data = dict(system.entry.data)
    data.pop(CONF_KP_RETURN)

    coordinator = BatteryCoordinator(system.hass, FakeEntry(data))

    assert coordinator._kp == DEFAULT_KP
    assert coordinator._kp_return == DEFAULT_KP * KP_RETURN_FACTOR
    assert coordinator._kp_return > coordinator._kp


async def test_raising_kp_keeps_the_asymmetry(build_system):
    """Why it is a factor and not a number.

    A fixed 0.5 would have quietly become symmetric the moment anyone tuned
    Kp up to 0.5, removing the export fix without a word about it.
    """
    system = build_system(grid=0, **{CONF_KP: 0.5})
    data = dict(system.entry.data)
    data.pop(CONF_KP_RETURN)

    coordinator = BatteryCoordinator(system.hass, FakeEntry(data))

    assert coordinator._kp_return == 1.0


async def test_it_cannot_overshoot_through_zero(build_system):
    """A fast return must not turn a discharge into a charge in one step.

    The clamp is what guarantees it, but this is the failure worth pinning:
    "stop discharging" answered with "start charging hard" would be a new way
    to export, which is the very thing the faster return exists to prevent.
    """
    system = build_system(
        grid=0, **{CONF_KP: 0.25, CONF_KP_RETURN: 1.0, CONF_DEADBAND: 50,
                   CONF_BIAS: 0}
    )
    coordinator = system.coordinator
    coordinator.setpoint = 200.0
    system.hass.states.set(GRID_SENSOR, -3000)              # a huge export while barely discharging

    await coordinator._async_tick(None)

    # it may well end up charging - there is a 3 kW surplus - but not by more
    # than that surplus, and the deliverable range is what bounds it
    assert coordinator.setpoint >= -3000

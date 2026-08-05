"""One mode at a time, each a bound on the setpoint.

Grid-zero is not one mode among others but the floor every mode falls back on,
so a pack keeps responding to the house and the sun inside whatever you pick.
"""
from __future__ import annotations

import time

import pytest

from custom_components.battery_management.const import (
    FLOW_CHARGE,
    FLOW_DISCHARGE,
    MODE_CHARGE_ONLY,
    MODE_DISCHARGE_ONLY,
    MODE_GRID_ZERO,
    MODE_PAUSE,
    POLICY_GRID_ZERO,
    POLICY_MODE_CHARGE_ONLY,
    POLICY_MODE_DISCHARGE_ONLY,
    POLICY_MODE_PAUSE,
)


async def test_defaults_to_following_the_meter(build_system):
    system = build_system(grid=500)
    assert system.coordinator.mode == MODE_GRID_ZERO

    await system.coordinator._async_tick(None)

    assert system.allocation() == {"Batterij 1": 288, "Batterij 2": 212}
    assert system.coordinator.active_policy == POLICY_GRID_ZERO


# -- charge only -------------------------------------------------------------


async def test_charge_only_refuses_to_discharge(build_system):
    system = build_system(grid=500)
    system.coordinator.mode = MODE_CHARGE_ONLY

    await system.coordinator._async_tick(None)

    assert system.coordinator.setpoint == 0
    assert system.allocation() == {"Batterij 1": 0, "Batterij 2": 0}
    assert system.coordinator.active_policy == POLICY_MODE_CHARGE_ONLY


async def test_charge_only_still_charges_on_surplus(build_system):
    """The floor keeps running: this is not 'do nothing'."""
    system = build_system(grid=-1000)
    system.coordinator.mode = MODE_CHARGE_ONLY

    await system.coordinator._async_tick(None)

    assert system.coordinator.setpoint == -1000
    assert system.flows() == [FLOW_CHARGE, FLOW_CHARGE]
    assert system.coordinator.active_policy == POLICY_GRID_ZERO


# -- discharge only ----------------------------------------------------------


async def test_discharge_only_refuses_to_charge(build_system):
    system = build_system(grid=-1000)
    system.coordinator.mode = MODE_DISCHARGE_ONLY

    await system.coordinator._async_tick(None)

    assert system.coordinator.setpoint == 0
    assert system.coordinator.active_policy == POLICY_MODE_DISCHARGE_ONLY


async def test_discharge_only_still_discharges_on_deficit(build_system):
    system = build_system(grid=500)
    system.coordinator.mode = MODE_DISCHARGE_ONLY

    await system.coordinator._async_tick(None)

    assert system.allocation() == {"Batterij 1": 288, "Batterij 2": 212}
    assert system.flows() == [FLOW_DISCHARGE, FLOW_DISCHARGE]


# -- pause -------------------------------------------------------------------


@pytest.mark.parametrize("grid", [-2000, -500, 0, 500, 2000])
async def test_pause_holds_at_zero_whatever_the_meter_says(build_system, grid):
    system = build_system(grid=grid)
    system.coordinator.mode = MODE_PAUSE

    await system.coordinator._async_tick(None)

    assert system.coordinator.setpoint == 0
    assert system.allocation() == {"Batterij 1": 0, "Batterij 2": 0}
    assert system.coordinator.active_policy == POLICY_MODE_PAUSE


async def test_pause_keeps_the_units_under_control(build_system):
    """Not the kill-switch: they stay in third-party control, holding at 0."""
    system = build_system(grid=500)
    system.coordinator.mode = MODE_PAUSE

    await system.coordinator._async_tick(None)

    modes = system.hass.services.options_set()
    assert system.mode(0) not in modes  # never handed back to self-consumption


# -- the bound interacts correctly with the integrator -----------------------


async def test_a_mode_bound_does_not_let_the_integrator_wind_up(build_system):
    """The bound reuses the anti-windup clamp, so pressure cannot build.

    The fixture runs Kp 1.0 and bias 0, so one tick of a 200 W error is worth
    200 W of setpoint. Ten ticks of stored-up error would be 2000 W - if
    releasing the mode produced that, the packs would slam into a load that was
    never there.
    """
    system = build_system(grid=200)
    system.coordinator.mode = MODE_CHARGE_ONLY

    for _ in range(10):
        await system.coordinator._async_tick(None)
    assert system.coordinator.setpoint == 0

    system.coordinator.mode = MODE_GRID_ZERO
    await system.coordinator._async_tick(None)

    assert system.coordinator.setpoint == 200


async def test_switching_mode_is_rejected_when_unknown(build_system):
    system = build_system(grid=500)

    with pytest.raises(ValueError):
        await system.coordinator.async_set_mode("turbo")


async def test_mode_survives_a_restart(build_system):
    system = build_system(grid=500, enabled=False)
    system.coordinator._store.data = {
        "enabled": False,
        "setpoint": 0.0,
        "mode": MODE_PAUSE,
        "saved_at": time.time(),
    }

    await system.coordinator._async_restore()

    assert system.coordinator.mode == MODE_PAUSE


async def test_an_unknown_stored_mode_falls_back_to_the_default(build_system):
    system = build_system(grid=500, enabled=False)
    system.coordinator._store.data = {
        "enabled": False,
        "setpoint": 0.0,
        "mode": "removed_in_a_later_version",
        "saved_at": time.time(),
    }

    await system.coordinator._async_restore()

    assert system.coordinator.mode == MODE_GRID_ZERO

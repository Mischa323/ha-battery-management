"""The SoC reserve, and the active-policy sensor that explains it.

The reserve is expressed as a raise of each unit's own discharge limit, so the
existing SoC weighting tapers towards it rather than the pack running full tilt
and then stopping dead.
"""
from __future__ import annotations

import time

from custom_components.battery_management.const import (
    CONF_DEADBAND,
    FLOW_CHARGE,
    POLICY_DEADBAND,
    POLICY_DISABLED,
    POLICY_FAST_CHARGE,
    POLICY_GRID_ZERO,
    POLICY_NO_GRID_DATA,
    POLICY_PACKS_EMPTY,
    POLICY_PACKS_FULL,
    POLICY_SOC_RESERVE,
)


async def test_off_by_default_so_nothing_changes(build_system):
    system = build_system(grid=500)
    assert system.coordinator.soc_reserve == 0

    await system.coordinator._async_tick(None)

    assert system.allocation() == {"Batterij 1": 288, "Batterij 2": 212}
    assert system.coordinator.active_policy == POLICY_GRID_ZERO


async def test_reserve_blocks_discharge_once_every_pack_reaches_it(build_system):
    system = build_system(grid=500, units=(("093", 30.0), ("052", 25.0)))
    system.coordinator.soc_reserve = 30.0

    await system.coordinator._async_tick(None)

    assert system.coordinator.setpoint == 0
    assert system.allocation() == {"Batterij 1": 0, "Batterij 2": 0}
    assert system.coordinator.active_policy == POLICY_SOC_RESERVE


async def test_a_pack_above_the_reserve_still_works(build_system):
    """Per unit, not on an average: the fuller pack carries the load alone."""
    system = build_system(grid=500, units=(("093", 80.0), ("052", 25.0)))
    system.coordinator.soc_reserve = 30.0

    await system.coordinator._async_tick(None)

    assert system.allocation() == {"Batterij 1": 500, "Batterij 2": 0}
    assert system.coordinator.active_policy == POLICY_GRID_ZERO


async def test_weighting_tapers_towards_the_reserve(build_system):
    """Weights are headroom above the reserve, not above the hardware limit.

    1000 W keeps both shares clear of min_output, so this measures the weighting
    itself rather than the consolidation rule.
    """
    without = build_system(grid=1000, units=(("093", 80.0), ("052", 60.0)))
    await without.coordinator._async_tick(None)
    # headroom above the 5 % hardware limit: 75 and 55
    assert without.allocation() == {"Batterij 1": 577, "Batterij 2": 423}

    with_reserve = build_system(grid=1000, units=(("093", 80.0), ("052", 60.0)))
    with_reserve.coordinator.soc_reserve = 50.0
    await with_reserve.coordinator._async_tick(None)
    # headroom above the reserve: 30 and 10, so the nearly-spent pack backs off
    assert with_reserve.allocation() == {"Batterij 1": 750, "Batterij 2": 250}


async def test_reserve_never_blocks_charging(build_system):
    system = build_system(grid=-1000, units=(("093", 20.0), ("052", 20.0)))
    system.coordinator.soc_reserve = 50.0

    await system.coordinator._async_tick(None)

    assert system.coordinator.setpoint == -1000
    assert system.flows() == [FLOW_CHARGE, FLOW_CHARGE]


async def test_reserve_is_clamped_to_a_sane_percentage(build_system):
    system = build_system(grid=500)

    await system.coordinator.async_set_soc_reserve(500)
    assert system.coordinator.soc_reserve == 100

    await system.coordinator.async_set_soc_reserve(-20)
    assert system.coordinator.soc_reserve == 0


async def test_reserve_survives_a_restart_even_while_switched_off(build_system):
    """It is a user setting, not runtime state."""
    system = build_system(grid=500, enabled=False)
    system.coordinator._store.data = {
        "enabled": False,
        "setpoint": 0.0,
        "soc_reserve": 35.0,
        "saved_at": time.time(),
    }

    await system.coordinator._async_restore()

    assert system.coordinator.soc_reserve == 35
    assert system.coordinator.enabled is False


async def test_reserve_is_not_dropped_when_the_setpoint_is_too_old(build_system):
    system = build_system(grid=500, enabled=False)
    system.coordinator._store.data = {
        "enabled": True,
        "setpoint": 3000.0,
        "soc_reserve": 40.0,
        "saved_at": time.time() - 86400,
    }

    await system.coordinator._async_restore()

    assert system.coordinator.setpoint == 0  # stale, dropped
    assert system.coordinator.soc_reserve == 40  # a setting, kept


# -- the active-policy sensor ------------------------------------------------


async def test_policy_reports_empty_packs_apart_from_the_reserve(build_system):
    """Genuinely empty is a different answer than 'the reserve says no'."""
    system = build_system(grid=500, units=(("093", 5.0), ("052", 5.0)))

    await system.coordinator._async_tick(None)

    assert system.coordinator.active_policy == POLICY_PACKS_EMPTY


async def test_policy_reports_full_packs(build_system):
    system = build_system(grid=-1000, units=(("093", 100.0), ("052", 100.0)))

    await system.coordinator._async_tick(None)

    assert system.coordinator.active_policy == POLICY_PACKS_FULL


async def test_policy_reports_the_deadband(build_system):
    system = build_system(grid=50, **{CONF_DEADBAND: 100})

    await system.coordinator._async_tick(None)

    assert system.coordinator.active_policy == POLICY_DEADBAND


async def test_policy_reports_a_missing_grid_reading(build_system):
    system = build_system(grid="unavailable")

    await system.coordinator._async_tick(None)

    assert system.coordinator.active_policy == POLICY_NO_GRID_DATA


async def test_policy_reports_fast_charge(build_system):
    system = build_system(units=(("093", 50.0), ("052", 50.0)), enabled=False)
    system.coordinator.fast_charge = True

    await system.coordinator._async_tick(None)

    assert system.coordinator.active_policy == POLICY_FAST_CHARGE


async def test_policy_reports_being_switched_off(build_system):
    system = build_system(grid=500, enabled=False)

    await system.coordinator._async_tick(None)

    assert system.coordinator.active_policy == POLICY_DISABLED

"""Tests for one full control tick.

These lock in the behaviours listed under "Gotchas" in CLAUDE.md: the setpoint
is the integrator's own state (never derived from the laggy per-unit power
sensors), both units always move in the same direction, the wind-up clamp holds,
and the unreliable `target_grid_power` max attribute is capped.
"""
from __future__ import annotations

import pytest

from custom_components.battery_management.const import (
    CONF_DEADBAND,
    CONF_FAST_CHARGE_HOLD,
    CONF_KP,
    FLOW_CHARGE,
    FLOW_DISCHARGE,
    MODE_SELF,
)


async def test_discharge_splits_soc_weighted(build_system):
    system = build_system(grid=500)

    await system.coordinator._async_tick(None)

    assert system.coordinator.setpoint == 500
    assert system.coordinator.status == "discharging"
    # weights are 80-5=75 and 60-5=55
    assert system.allocation() == {"Batterij 1": 288, "Batterij 2": 212}
    assert system.flows() == [FLOW_DISCHARGE, FLOW_DISCHARGE]


async def test_charge_gives_the_emptier_unit_the_larger_share(build_system):
    system = build_system(grid=-1000)

    await system.coordinator._async_tick(None)

    assert system.coordinator.setpoint == -1000
    assert system.coordinator.status == "charging"
    # weights are 100-80=20 and 100-60=40, so the emptier unit takes more
    assert system.allocation() == {"Batterij 1": 333, "Batterij 2": 667}
    assert system.flows() == [FLOW_CHARGE, FLOW_CHARGE]


@pytest.mark.parametrize("grid", [-5000, -1000, -200, 0, 200, 1000, 5000])
async def test_units_never_move_in_opposite_directions(build_system, grid):
    """The structural guarantee against cross-charging."""
    system = build_system(grid=grid)

    await system.coordinator._async_tick(None)

    flows = system.flows()
    assert len(flows) == 2
    assert len(set(flows)) == 1


async def test_setpoint_is_held_inside_the_deadband(build_system):
    system = build_system(grid=50, **{CONF_DEADBAND: 100})
    system.coordinator.setpoint = 400.0

    await system.coordinator._async_tick(None)

    # held, not reset and not integrated further
    assert system.coordinator.setpoint == 400


async def test_setpoint_integrates_across_ticks(build_system):
    """The setpoint is the integrator state, not a reading of the units."""
    system = build_system(grid=2000, **{CONF_KP: 0.25})

    await system.coordinator._async_tick(None)
    assert system.coordinator.setpoint == 500

    await system.coordinator._async_tick(None)
    assert system.coordinator.setpoint == 1000


async def test_discharge_setpoint_is_clamped_to_total_capacity(build_system):
    system = build_system(grid=100_000)

    await system.coordinator._async_tick(None)

    assert system.coordinator.setpoint == 7000  # 2 x 3500, not 100 kW
    assert max(system.allocation().values()) <= 3500


async def test_charge_setpoint_is_clamped_to_total_capacity(build_system):
    system = build_system(grid=-100_000)

    await system.coordinator._async_tick(None)

    assert system.coordinator.setpoint == -7000


async def test_bogus_target_max_attribute_is_capped_at_unit_max(build_system):
    """Unit 093 has been seen reporting a max of 10000 for a 3500 W unit."""
    system = build_system(grid=100_000, target_max=10_000)

    await system.coordinator._async_tick(None)

    assert system.coordinator.setpoint == 7000
    assert max(system.allocation().values()) == 3500


@pytest.mark.parametrize("grid", ["unavailable", "unknown"])
async def test_unreadable_grid_sensor_degrades_without_writing(build_system, grid):
    system = build_system(grid=grid)

    await system.coordinator._async_tick(None)

    assert system.coordinator.status == "degraded"
    assert system.hass.services.calls == []


async def test_missing_grid_sensor_degrades_without_writing(build_system):
    system = build_system(grid=None)

    await system.coordinator._async_tick(None)

    assert system.coordinator.status == "degraded"
    assert system.hass.services.calls == []


async def test_offline_unit_is_skipped_entirely(build_system):
    system = build_system(grid=500, units=(("093", 80.0), ("052", None)))

    await system.coordinator._async_tick(None)

    assert system.allocation() == {"Batterij 1": 500}
    written = set(system.hass.services.options_set()) | set(
        system.hass.services.targets_set()
    )
    assert system.flow(1) not in written
    assert system.target(1) not in written


async def test_unit_at_its_discharge_limit_is_idled_not_reversed(build_system):
    system = build_system(grid=500, units=(("093", 80.0), ("052", 5.0)))

    await system.coordinator._async_tick(None)

    assert system.allocation() == {"Batterij 1": 500, "Batterij 2": 0}
    # still commanded in the same direction, just with a zero target
    assert system.flows() == [FLOW_DISCHARGE, FLOW_DISCHARGE]


async def test_missing_limit_entities_fall_back_to_safe_defaults(build_system):
    """The two SoC-limit pickers are optional in the config flow."""
    system = build_system(grid=500, with_limits=False)

    await system.coordinator._async_tick(None)

    # same result as with explicit 100 / 5 limit entities
    assert system.allocation() == {"Batterij 1": 288, "Batterij 2": 212}


async def test_tick_is_a_no_op_while_disabled(build_system):
    system = build_system(grid=500, enabled=False)

    await system.coordinator._async_tick(None)

    assert system.hass.services.calls == []
    assert system.coordinator.setpoint == 0


async def test_fast_charge_drives_both_units_to_maximum(build_system):
    system = build_system(units=(("093", 50.0), ("052", 90.0)), enabled=False)
    system.coordinator.fast_charge = True

    await system.coordinator._async_tick(None)

    assert system.coordinator.status == "fast_charge"
    assert system.flows() == [FLOW_CHARGE, FLOW_CHARGE]
    assert system.allocation() == {"Batterij 1": 3500, "Batterij 2": 3500}


async def test_fast_charge_holds_the_packs_full_by_default(build_system):
    """You pressed this before a storm; do not hand the charge straight back."""
    system = build_system(units=(("093", 100.0), ("052", 100.0)), enabled=False)
    system.coordinator.fast_charge = True

    await system.coordinator._async_tick(None)

    assert system.coordinator.fast_charge is True
    assert system.coordinator.fast_charge_holding is True
    assert system.coordinator.status == "hold"
    assert system.allocation() == {"Batterij 1": 0, "Batterij 2": 0}


async def test_fast_charge_can_still_release_itself_when_configured(build_system):
    system = build_system(
        units=(("093", 100.0), ("052", 100.0)),
        enabled=False,
        **{CONF_FAST_CHARGE_HOLD: False},
    )
    system.coordinator.fast_charge = True

    await system.coordinator._async_tick(None)

    assert system.coordinator.fast_charge is False
    assert system.allocation() == {"Batterij 1": 0, "Batterij 2": 0}


async def test_stopping_reverts_every_unit_to_self_consumption(build_system):
    system = build_system(grid=500)

    await system.coordinator.async_stop(revert=True)

    modes = system.hass.services.options_set()
    assert modes[system.mode(0)] == MODE_SELF
    assert modes[system.mode(1)] == MODE_SELF
    assert system.allocation() == {"Batterij 1": 0, "Batterij 2": 0}


async def test_a_failing_service_call_degrades_instead_of_raising(build_system):
    system = build_system(grid=500)

    async def boom(*args, **kwargs):
        raise RuntimeError("modbus is down")

    system.hass.services.async_call = boom

    await system.coordinator._async_tick(None)  # must not raise

    assert system.coordinator.status == "degraded"

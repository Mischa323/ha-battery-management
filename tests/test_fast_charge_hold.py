"""Charge-then-hold.

Fast charge exists to prepare for something. Switching itself off at full and
letting the mode discharge the packs again defeats the point, so once they are
full the switch stays on and keeps them there until the user releases it.
"""
from __future__ import annotations

from custom_components.battery_management.const import (
    CONF_FAST_CHARGE_HOLD,
    FLOW_CHARGE,
    POLICY_FAST_CHARGE,
    POLICY_FAST_CHARGE_HOLD,
)


async def test_still_charges_while_the_packs_are_not_full(build_system):
    system = build_system(units=(("093", 50.0), ("052", 90.0)), enabled=False)
    system.coordinator.fast_charge = True

    await system.coordinator._async_tick(None)

    assert system.coordinator.fast_charge_holding is False
    assert system.coordinator.active_policy == POLICY_FAST_CHARGE
    assert system.allocation() == {"Batterij 1": 3500, "Batterij 2": 3500}


async def test_holds_once_full_and_reports_why(build_system):
    system = build_system(units=(("093", 100.0), ("052", 100.0)), enabled=False)
    system.coordinator.fast_charge = True

    await system.coordinator._async_tick(None)

    assert system.coordinator.fast_charge is True
    assert system.coordinator.fast_charge_holding is True
    assert system.coordinator.active_policy == POLICY_FAST_CHARGE_HOLD
    assert system.allocation() == {"Batterij 1": 0, "Batterij 2": 0}


async def test_holding_survives_further_ticks(build_system):
    system = build_system(units=(("093", 100.0), ("052", 100.0)), enabled=False)
    system.coordinator.fast_charge = True

    for _ in range(3):
        await system.coordinator._async_tick(None)

    assert system.coordinator.fast_charge_holding is True
    assert system.allocation() == {"Batterij 1": 0, "Batterij 2": 0}


async def test_tops_up_again_when_a_pack_drifts_down(build_system):
    """Keeping them full is the job, not reaching full once."""
    system = build_system(units=(("093", 100.0), ("052", 100.0)), enabled=False)
    system.coordinator.fast_charge = True
    await system.coordinator._async_tick(None)
    assert system.coordinator.fast_charge_holding is True

    system.hass.states.set(system.soc(1), 92)
    system.hass.services.clear()
    await system.coordinator._async_tick(None)

    assert system.coordinator.fast_charge_holding is False
    assert system.allocation()["Batterij 2"] == 3500
    assert system.hass.services.options_set()[system.flow(1)] == FLOW_CHARGE


async def test_releasing_the_switch_ends_the_hold(build_system):
    system = build_system(units=(("093", 100.0), ("052", 100.0)), enabled=False)
    system.coordinator.fast_charge = True
    await system.coordinator._async_tick(None)
    assert system.coordinator.fast_charge_holding is True

    await system.coordinator.async_set_fast_charge(False)

    assert system.coordinator.fast_charge is False
    assert system.coordinator.fast_charge_holding is False


async def test_the_old_auto_release_is_still_available(build_system):
    """Changing behaviour under an existing switch needs a way back."""
    system = build_system(
        units=(("093", 100.0), ("052", 100.0)),
        enabled=False,
        **{CONF_FAST_CHARGE_HOLD: False},
    )
    system.coordinator.fast_charge = True

    await system.coordinator._async_tick(None)

    assert system.coordinator.fast_charge is False
    assert system.coordinator.fast_charge_holding is False


async def test_holding_is_not_resumed_after_a_restart(build_system):
    """Same reasoning as fast charge itself: never resume unattended."""
    system = build_system(grid=500, enabled=False)
    system.coordinator._store.data = {
        "enabled": True,
        "fast_charge": True,
        "fast_charge_holding": True,
        "setpoint": 0.0,
        "saved_at": 0,
    }

    await system.coordinator._async_restore()

    assert system.coordinator.fast_charge is False
    assert system.coordinator.fast_charge_holding is False

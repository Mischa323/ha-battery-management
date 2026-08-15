"""Not bouncing off the bottom of the pack.

The discharge floor is one threshold, so it is both where discharging stops and
where it may start again. Observed at the primary site: the sun lifts a pack
from 5 % to 6 % and it is discharged straight back to 5 %, over and over. The
bottom of a pack is the worst place to cycle it.

So a pack that has been emptied waits until it has charged a few points back
before it is let out again - a latch, not a raised floor. Coming down from full
it still empties all the way to its own limit.
"""
from __future__ import annotations

from custom_components.battery_management.const import (
    CONF_DISCHARGE_RECOVERY,
    POLICY_PACKS_EMPTY,
    POLICY_RECOVERING,
    POLICY_SOC_RESERVE,
)

# 5 % floor, so a pack has to reach 10 % before it may discharge again
RECOVER = {CONF_DISCHARGE_RECOVERY: 5}


async def test_a_pack_the_sun_lifted_off_the_floor_is_not_dumped_again(build_system):
    """The reported behaviour, and the reason this exists."""
    system = build_system(grid=800, units=(("093", 5.0), ("052", 5.0)), **RECOVER)
    await system.coordinator._async_tick(None)          # both hit the floor

    system.hass.states.set(system.soc(0), 6.0)          # the sun lifts one
    system.hass.services.clear()
    await system.coordinator._async_tick(None)

    assert system.allocation() == {"Batterij 1": 0, "Batterij 2": 0}
    assert system.coordinator.active_policy == POLICY_RECOVERING


async def test_it_is_let_out_once_it_has_charged_far_enough(build_system):
    system = build_system(grid=800, units=(("093", 5.0), ("052", 5.0)), **RECOVER)
    await system.coordinator._async_tick(None)

    system.hass.states.set(system.soc(0), 10.0)
    await system.coordinator._async_tick(None)

    assert system.allocation()["Batterij 1"] > 0


async def test_it_stays_held_at_one_point_short(build_system):
    system = build_system(grid=800, units=(("093", 5.0), ("052", 5.0)), **RECOVER)
    await system.coordinator._async_tick(None)

    system.hass.states.set(system.soc(0), 9.0)
    system.hass.services.clear()
    await system.coordinator._async_tick(None)

    assert system.allocation()["Batterij 1"] == 0


async def test_a_pack_coming_down_from_full_still_empties_completely(build_system):
    """A latch, not a raised floor - otherwise it would cost you those points."""
    system = build_system(grid=800, units=(("093", 40.0), ("052", 40.0)), **RECOVER)
    await system.coordinator._async_tick(None)

    for soc in (20.0, 9.0, 7.0, 6.0):
        system.hass.states.set(system.soc(0), soc)
        system.hass.states.set(system.soc(1), soc)
        system.hass.services.clear()
        await system.coordinator._async_tick(None)
        assert system.allocation()["Batterij 1"] > 0, f"stopped early at {soc} %"


async def test_charging_is_never_blocked_by_it(build_system):
    """It is the way back out that waits, not the way in."""
    system = build_system(grid=-2000, units=(("093", 5.0), ("052", 5.0)), **RECOVER)

    await system.coordinator._async_tick(None)

    assert all(s.target < 0 for s in system.coordinator.unit_status.values())


async def test_the_other_pack_carries_on_alone(build_system):
    """One pack recovering must not stop the house being supplied."""
    system = build_system(grid=800, units=(("093", 5.0), ("052", 60.0)), **RECOVER)

    await system.coordinator._async_tick(None)

    assert system.coordinator.recovering["Batterij 1"] is True
    assert system.allocation()["Batterij 2"] > 0


async def test_it_follows_the_soc_reserve_rather_than_a_fixed_percentage(build_system):
    """Counted above the floor, so a reserve of 40 % means 45 %, not 10 %."""
    system = build_system(grid=800, units=(("093", 40.0), ("052", 40.0)), **RECOVER)
    system.coordinator.soc_reserve = 40.0
    await system.coordinator._async_tick(None)
    assert system.coordinator.recovering["Batterij 1"] is True

    system.hass.states.set(system.soc(0), 44.0)
    system.hass.services.clear()
    await system.coordinator._async_tick(None)
    assert system.allocation()["Batterij 1"] == 0

    system.hass.states.set(system.soc(0), 45.0)
    await system.coordinator._async_tick(None)
    assert system.allocation()["Batterij 1"] > 0


async def test_zero_switches_it_off_entirely(build_system):
    """Nothing here is mandatory."""
    system = build_system(
        grid=800, units=(("093", 5.0), ("052", 5.0)), **{CONF_DISCHARGE_RECOVERY: 0}
    )
    await system.coordinator._async_tick(None)

    system.hass.states.set(system.soc(0), 6.0)
    await system.coordinator._async_tick(None)

    assert system.allocation()["Batterij 1"] > 0


async def test_it_survives_a_restart(build_system):
    """Coming back at 6 % and resuming the dump is the whole failure."""
    system = build_system(grid=800, units=(("093", 5.0), ("052", 5.0)), **RECOVER)
    await system.coordinator._async_tick(None)
    stored = system.coordinator._store.data
    assert stored["recovering"]["Batterij 1"] is True

    revived = build_system(grid=800, units=(("093", 6.0), ("052", 6.0)), **RECOVER)
    revived.coordinator._store.data = stored
    await revived.coordinator._async_restore()
    await revived.coordinator._async_tick(None)

    assert revived.allocation() == {"Batterij 1": 0, "Batterij 2": 0}


async def test_an_empty_pack_still_says_it_is_empty(build_system):
    """Three different silences; "recovering" must not swallow the other two."""
    system = build_system(grid=800, units=(("093", 5.0), ("052", 5.0)), **RECOVER)

    await system.coordinator._async_tick(None)

    assert system.coordinator.active_policy == POLICY_PACKS_EMPTY


async def test_the_reserve_still_has_its_own_answer(build_system):
    system = build_system(grid=800, units=(("093", 60.0), ("052", 60.0)), **RECOVER)
    system.coordinator.soc_reserve = 80.0

    await system.coordinator._async_tick(None)

    assert system.coordinator.active_policy == POLICY_SOC_RESERVE

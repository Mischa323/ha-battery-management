"""Per-unit observability: what the online / target entities read back."""
from __future__ import annotations

from custom_components.battery_management.const import FLOW_CHARGE, FLOW_DISCHARGE


def test_starts_offline_with_nothing_commanded(build_system):
    status = build_system().coordinator.unit_status

    assert set(status) == {"Batterij 1", "Batterij 2"}
    assert all(not s.online and s.target == 0 and s.flow is None for s in status.values())


async def test_discharge_is_recorded_as_a_positive_target(build_system):
    system = build_system(grid=500)

    await system.coordinator._async_tick(None)

    status = system.coordinator.unit_status["Batterij 1"]
    assert status.online is True
    assert status.soc == 80
    assert status.flow == FLOW_DISCHARGE
    assert status.target == 288  # same sign convention as the setpoint sensor


async def test_charge_is_recorded_as_a_negative_target(build_system):
    system = build_system(grid=-1000)

    await system.coordinator._async_tick(None)

    status = system.coordinator.unit_status["Batterij 2"]
    assert status.flow == FLOW_CHARGE
    assert status.target == -667


async def test_offline_unit_keeps_its_last_command(build_system):
    """Third-Party Control has no watchdog: the unit is still running it."""
    system = build_system(grid=500)
    await system.coordinator._async_tick(None)
    assert system.coordinator.unit_status["Batterij 2"].target == 212

    system.hass.states.remove(system.soc(1))
    await system.coordinator._async_tick(None)

    status = system.coordinator.unit_status["Batterij 2"]
    assert status.online is False
    assert status.soc is None
    assert status.target == 212  # not cleared - the pack has not stopped


async def test_fast_charge_is_recorded_at_full_negative_power(build_system):
    system = build_system(units=(("093", 50.0), ("052", 90.0)), enabled=False)
    system.coordinator.fast_charge = True

    await system.coordinator._async_tick(None)

    assert system.coordinator.unit_status["Batterij 1"].target == -3500
    assert system.coordinator.unit_status["Batterij 2"].flow == FLOW_CHARGE


async def test_reverting_clears_every_recorded_target(build_system):
    system = build_system(grid=500)
    await system.coordinator._async_tick(None)

    await system.coordinator.async_stop(revert=True)

    for status in system.coordinator.unit_status.values():
        assert status.target == 0
        assert status.flow is None


async def test_reachability_stays_current_while_switched_off(build_system):
    """"Disconnected" must mean unreachable, not merely unchecked.

    With the coordinator off the tick returns early, so the per-unit sensors
    used to sit at their initial "disconnected" — alarming on a device page
    where nothing is actually wrong.
    """
    system = build_system(grid=500, enabled=False)

    await system.coordinator._async_tick(None)

    assert system.coordinator.unit_status["Batterij 1"].online is True
    assert system.coordinator.unit_status["Batterij 1"].soc == 80
    assert system.hass.services.calls == []  # still commands nothing


async def test_an_unreachable_pack_still_reads_as_offline_while_switched_off(
    build_system,
):
    system = build_system(grid=500, units=(("093", 80.0), ("052", None)), enabled=False)

    await system.coordinator._async_tick(None)

    assert system.coordinator.unit_status["Batterij 1"].online is True
    assert system.coordinator.unit_status["Batterij 2"].online is False

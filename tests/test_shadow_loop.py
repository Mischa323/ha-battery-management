"""Closing the control loop on reconstructed data during a shadow run.

In dry run the site's existing automations regulate the meter, so a naive
shadow sees a near-zero error and parks at zero — it looks calm because someone
else did the work, and comparing that with the old system compares the old
system with itself.

    net demand = grid + battery      (what the meter would read with no battery)
    our meter  = net demand - our own setpoint

PV cancels out of that, so no solar sensor is involved. What is needed is the
other controller's current power, read back from the very entities we would
have written to.
"""
from __future__ import annotations

from custom_components.battery_management.const import (
    CONF_BATTERY_POWER_SENSOR,
    CONF_SHADOW_SIMULATE,
    FLOW_CHARGE,
    FLOW_DISCHARGE,
)


def other_controller(system, *, watts: float, flow: str = FLOW_DISCHARGE):
    """Pretend the site's own automations are driving both packs."""
    for index in (0, 1):
        system.hass.states.set(system.target(index), watts / 2)
        system.hass.states.set(system.flow(index), flow)


async def test_a_naive_shadow_would_see_nothing_to_do(build_system):
    """The failure this exists to prevent, pinned so it cannot come back."""
    system = build_system(grid=20, dry_run=True, **{CONF_SHADOW_SIMULATE: False})
    other_controller(system, watts=900)

    await system.coordinator._async_tick(None)

    # meter already at ~0 because someone else is regulating it
    assert system.coordinator.setpoint == 20


async def test_the_simulated_loop_sees_the_real_demand(build_system):
    """Same instant, but reconstructed: 900 W of house load is visible again."""
    system = build_system(grid=20, dry_run=True)
    other_controller(system, watts=900)

    await system.coordinator._async_tick(None)

    # net demand = 20 + 900 = 920, our setpoint was 0, so we see 920
    assert system.coordinator.setpoint == 920


async def test_it_regulates_its_own_virtual_meter_over_time(build_system):
    """The point: a full closed loop on the household's real demand."""
    system = build_system(grid=20, dry_run=True)
    other_controller(system, watts=900)

    setpoints = []
    for _ in range(4):
        await system.coordinator._async_tick(None)
        setpoints.append(system.coordinator.setpoint)

    # it converges on the real 920 W demand instead of drifting
    assert setpoints[0] == 920
    assert all(s == 920 for s in setpoints)
    assert system.hass.services.calls == []  # still commanding nothing


async def test_a_charging_neighbour_is_read_back_with_the_right_sign(build_system):
    system = build_system(grid=-1500, dry_run=True)
    other_controller(system, watts=1000, flow=FLOW_CHARGE)

    await system.coordinator._async_tick(None)

    # net demand = -1500 + (-1000) = -2500 of surplus
    assert system.coordinator.setpoint == -2500


async def test_a_measured_sensor_wins_over_the_readback(build_system):
    """Commands are only as accurate as the packs are obedient."""
    system = build_system(
        grid=20, dry_run=True, **{CONF_BATTERY_POWER_SENSOR: "sensor.battery_power"}
    )
    other_controller(system, watts=900)  # what was commanded
    system.hass.states.set("sensor.battery_power", 600)  # what actually happened

    await system.coordinator._async_tick(None)

    assert system.coordinator.setpoint == 620


async def test_falls_back_to_the_real_meter_when_nothing_can_be_observed(build_system):
    """An honest open loop beats a wrong reconstruction."""
    system = build_system(grid=500, dry_run=True)
    for index in (0, 1):
        system.hass.states.remove(system.target(index))

    await system.coordinator._async_tick(None)

    assert system.coordinator.setpoint == 500


async def test_the_reconstruction_is_recorded_for_checking_afterwards(build_system):
    system = build_system(grid=20, dry_run=True)
    other_controller(system, watts=900)

    await system.coordinator._async_tick(None)

    row = system.coordinator.tick_log[-1]
    assert row["observed_grid_w"] == 20     # what the meter really said
    assert row["other_controller_w"] == 900  # what the other system was doing
    assert row["grid_w"] == 920              # what we regulated against


async def test_simulation_never_applies_to_a_live_run(build_system):
    """When we are in charge the meter already reflects us."""
    system = build_system(grid=500)
    other_controller(system, watts=900)

    await system.coordinator._async_tick(None)

    assert system.coordinator.setpoint == 500
    assert system.coordinator.tick_log[-1]["other_controller_w"] is None


# -- what a shadow run is checked with ---------------------------------------


async def test_all_three_meter_readings_are_published(build_system):
    """Real reading, what was regulated against, and the other controller.

    Comparing the first two is how the reconstruction is checked; without them
    it can only be seen in a diagnostics download.
    """
    system = build_system(grid=20, dry_run=True)
    other_controller(system, watts=900)

    await system.coordinator._async_tick(None)

    assert system.coordinator.last_grid_observed == 20
    assert system.coordinator.last_grid_used == 920
    assert system.coordinator.last_other_power == 900


async def test_a_live_run_regulates_against_the_real_reading(build_system):
    system = build_system(grid=500)

    await system.coordinator._async_tick(None)

    assert system.coordinator.last_grid_observed == 500
    assert system.coordinator.last_grid_used == 500
    assert system.coordinator.last_other_power is None


async def test_the_reading_is_published_before_anything_is_switched_on(
    build_system,
):
    """Check the meter is read correctly without committing to anything."""
    system = build_system(grid=740, enabled=False)

    await system.coordinator._async_tick(None)

    assert system.coordinator.last_grid_observed == 740


async def test_an_unreadable_meter_shows_as_nothing_rather_than_stale(
    build_system,
):
    system = build_system(grid=500)
    await system.coordinator._async_tick(None)
    assert system.coordinator.last_grid_observed == 500

    system.hass.states.set("sensor.p1_meter_power", "unavailable")
    await system.coordinator._async_tick(None)

    assert system.coordinator.last_grid_observed is None

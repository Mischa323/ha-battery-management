"""Executing somebody else's plan.

EMHASS already does model-predictive optimisation with price, PV and load
forecasts; building a worse one would be a waste. What it does not do is
coordinate two packs without cross-charging, which is what this integration is
for. So: the plan comes in through `set_setpoint`, and the split, the SoC
limits, never-opposite-directions and the safe revert all still apply beneath it.
"""
from __future__ import annotations

from datetime import timedelta

import pytest

from custom_components.battery_management import coordinator as coordinator_module
from custom_components.battery_management.const import (
    CONF_EXTERNAL_TIMEOUT,
    CONF_GRID_MAX_AGE,
    FLOW_CHARGE,
    FLOW_DISCHARGE,
    MODE_EXTERNAL,
    POLICY_EXTERNAL,
    POLICY_EXTERNAL_STALE,
)


@pytest.fixture
def planned(build_system):
    """A system in external mode, with a plan already handed in."""

    async def _build(setpoint=1200, *, grid=500, **options):
        system = build_system(grid=grid, **options)
        system.coordinator.mode = MODE_EXTERNAL
        if setpoint is not None:
            await system.coordinator.async_set_setpoint(setpoint)
            system.hass.services.clear()
        return system

    return _build


async def test_follows_the_plan(planned):
    system = await planned(1200)

    await system.coordinator._async_tick(None)

    assert system.coordinator.setpoint == 1200
    assert system.coordinator.active_policy == POLICY_EXTERNAL
    assert sum(system.allocation().values()) == 1200


async def test_the_plan_is_still_split_by_state_of_charge(planned):
    """Our job underneath: the fuller pack does more."""
    system = await planned(1000)

    await system.coordinator._async_tick(None)

    allocation = system.allocation()
    assert allocation["Batterij 1"] > allocation["Batterij 2"]
    assert sum(allocation.values()) == 1000


async def test_a_charging_plan_still_moves_both_packs_the_same_way(planned):
    system = await planned(-2000)

    await system.coordinator._async_tick(None)

    assert system.flows() == [FLOW_CHARGE, FLOW_CHARGE]


async def test_the_plan_cannot_exceed_what_the_packs_can_deliver(planned):
    """The plan proposes; the clamp disposes."""
    system = await planned(50_000)

    await system.coordinator._async_tick(None)

    assert system.coordinator.setpoint == 7000


async def test_the_soc_reserve_overrules_the_plan(planned):
    """Safety underneath means underneath, including someone else's plan."""
    system = await planned(2000, units=(("093", 30.0), ("052", 30.0)))
    system.coordinator.soc_reserve = 30.0

    await system.coordinator._async_tick(None)

    assert system.coordinator.setpoint == 0


async def test_a_stale_plan_hands_control_back(planned, monkeypatch):
    """A plan that stops arriving must not freeze the packs on its last word.

    The handover is smooth rather than abrupt: the setpoint *is* the integrator
    state, so grid-zero resumes from 1200 and regulates from there instead of
    snapping to zero and slamming the packs shut. Here the meter still reads
    500 W of import while discharging 1200, so the house is drawing 1700 — and
    that is exactly where the integrator goes.
    """
    # grid_max_age off: this test yanks the clock forward 20 minutes, which
    # would also make the meter look stale. The plan is the subject here.
    system = await planned(
        1200, **{CONF_EXTERNAL_TIMEOUT: 15, CONF_GRID_MAX_AGE: 0}
    )
    real_now = coordinator_module.dt_util.utcnow()
    monkeypatch.setattr(
        coordinator_module.dt_util,
        "utcnow",
        lambda: real_now + timedelta(minutes=20),
    )

    await system.coordinator._async_tick(None)

    assert system.coordinator.active_policy == POLICY_EXTERNAL_STALE
    assert system.coordinator.setpoint == 1700  # regulating again, not pinned
    assert system.flows() == [FLOW_DISCHARGE, FLOW_DISCHARGE]


async def test_a_plan_that_never_arrived_is_stale_from_the_start(planned):
    system = await planned(None)

    await system.coordinator._async_tick(None)

    assert system.coordinator.active_policy == POLICY_EXTERNAL_STALE
    assert system.coordinator.setpoint == 500  # grid-zero underneath


async def test_a_fresh_plan_replaces_an_older_one(planned):
    system = await planned(1200)
    await system.coordinator._async_tick(None)

    await system.coordinator.async_set_setpoint(-800)
    await system.coordinator._async_tick(None)

    assert system.coordinator.setpoint == -800
    assert system.coordinator.active_policy == POLICY_EXTERNAL


async def test_the_plan_is_ignored_in_other_modes(build_system):
    """set_setpoint stays a nudge everywhere else, not a takeover."""
    system = build_system(grid=500)

    await system.coordinator.async_set_setpoint(-3000)
    await system.coordinator._async_tick(None)

    # grid-zero integrates from there rather than holding the handed-in value
    assert system.coordinator.setpoint != -3000


async def test_the_plan_and_its_age_are_visible(planned):
    system = await planned(1200)

    report = system.coordinator.diagnostics()

    assert report["state"]["external_setpoint_w"] == 1200
    assert report["state"]["external_plan_age_s"] < 5
    assert report["state"]["external_timeout_min"] == 15


async def test_dry_run_still_commands_nothing_under_an_external_plan(planned):
    system = await planned(1200, dry_run=True)

    await system.coordinator._async_tick(None)

    assert system.hass.services.calls == []
    assert system.coordinator.active_policy == POLICY_EXTERNAL

"""The loop must not keep integrating a meter that stopped reporting.

The failure this prevents is not theoretical and it is not the jitter. Across
5917 real ticks the P1 was a median 5.3 s old and 46 s at its worst, which the
integrator shrugs off. What it cannot shrug off is a meter that *freezes*: a
hung integration keeps its last state, so the reading stays perfectly readable
and every other check says the system is healthy. Meanwhile the error never
changes sign, and `sp += kp * error` walks the setpoint to full discharge in
about two minutes and pins it there until the packs are flat - with
`status: ok` the whole way.

Before this guard the only thing that stopped the loop was a reading that had
gone `unavailable`, which is the failure that announces itself.
"""
from __future__ import annotations

from datetime import timedelta

from homeassistant.util import dt as dt_util

from custom_components.battery_management.const import (
    CONF_GRID_MAX_AGE,
    POLICY_GRID_STALE,
    POLICY_NO_GRID_DATA,
)

from .conftest import GRID_SENSOR


def age_grid(system, seconds: float) -> None:
    """Make the meter look like it last said anything `seconds` ago."""
    state = system.hass.states.get(GRID_SENSOR)
    stamp = dt_util.utcnow() - timedelta(seconds=seconds)
    state.last_reported = stamp
    state.last_updated = stamp
    state.last_changed = stamp


async def test_a_fresh_meter_regulates_normally(build_system):
    system = build_system(grid=1000, **{CONF_GRID_MAX_AGE: 60})

    await system.coordinator._async_tick(None)

    assert system.coordinator.active_policy != POLICY_GRID_STALE
    assert system.coordinator.setpoint > 0


async def test_a_frozen_meter_stops_the_integrator(build_system):
    """The setpoint holds where it was instead of walking away."""
    system = build_system(grid=1000, **{CONF_GRID_MAX_AGE: 60})
    await system.coordinator._async_tick(None)
    settled = system.coordinator.setpoint
    system.hass.services.clear()

    age_grid(system, 300)
    await system.coordinator._async_tick(None)

    assert system.coordinator.active_policy == POLICY_GRID_STALE
    assert system.coordinator.status == "degraded"
    assert system.coordinator.setpoint == settled
    # nothing is commanded: the packs keep doing what the last good reading
    # asked for, which beats slamming them shut on every meter hiccup
    assert system.hass.services.calls == []


async def test_it_recovers_by_itself_when_the_meter_comes_back(build_system):
    system = build_system(grid=1000, **{CONF_GRID_MAX_AGE: 60})
    age_grid(system, 300)
    await system.coordinator._async_tick(None)
    assert system.coordinator.active_policy == POLICY_GRID_STALE

    system.hass.states.set(GRID_SENSOR, 1000)
    await system.coordinator._async_tick(None)

    assert system.coordinator.active_policy != POLICY_GRID_STALE
    assert system.coordinator.setpoint > 0


async def test_a_steady_house_is_not_a_dead_meter(build_system):
    """The distinction the whole guard turns on.

    A meter reporting the same 1000 W every second has an ancient
    `last_changed` and a fresh `last_reported`. Reading the wrong stamp would
    stop the packs on a quiet night for no reason - which is why this measures
    against `last_reported` and why the fake carries all three.
    """
    system = build_system(grid=1000, **{CONF_GRID_MAX_AGE: 60})
    state = system.hass.states.get(GRID_SENSOR)
    old = dt_util.utcnow() - timedelta(hours=2)
    state.last_updated = old
    state.last_changed = old
    state.last_reported = dt_util.utcnow()

    await system.coordinator._async_tick(None)

    assert system.coordinator.active_policy != POLICY_GRID_STALE
    assert system.coordinator.setpoint > 0


async def test_zero_turns_the_guard_off(build_system):
    system = build_system(grid=1000, **{CONF_GRID_MAX_AGE: 0})
    age_grid(system, 86_400)

    await system.coordinator._async_tick(None)

    assert system.coordinator.active_policy != POLICY_GRID_STALE
    assert system.coordinator.setpoint > 0


async def test_an_unknown_age_fails_open(build_system):
    """A core without `last_reported` must not stop the packs.

    Falling back to `last_updated` is already a compromise; if neither stamp
    can be read the guard has no opinion, and having no opinion has to mean
    "carry on". Failing closed here would idle a working system.
    """
    system = build_system(grid=1000, **{CONF_GRID_MAX_AGE: 60})
    state = system.hass.states.get(GRID_SENSOR)
    del state.last_reported
    del state.last_updated
    del state.last_changed

    await system.coordinator._async_tick(None)

    assert system.coordinator.active_policy != POLICY_GRID_STALE


async def test_unreadable_still_reports_its_own_reason(build_system):
    """Stale and unreadable are different findings and keep different names."""
    system = build_system(grid=1000, **{CONF_GRID_MAX_AGE: 60})
    system.hass.states.remove(GRID_SENSOR)

    await system.coordinator._async_tick(None)

    assert system.coordinator.active_policy == POLICY_NO_GRID_DATA

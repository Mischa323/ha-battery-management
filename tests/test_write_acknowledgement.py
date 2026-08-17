"""Noticing a pack that has quietly stopped taking orders.

Two facts combine badly. `_svc_select` and `_svc_number` call with
`blocking=False`, so a service call that fails raises inside a task nobody
awaits — the tick never hears about it. And per gotcha 1 the packs have no
watchdog, so one that stops accepting commands keeps running whatever it took
last, indefinitely.

Together that means a silently refused write leaves the device doing something
nobody asked for, while the setpoint, the status and the per-unit sensor all
look completely healthy. The readback was already being measured for the trace;
it was simply never read. This is what reading it looks like.
"""
from __future__ import annotations

from datetime import timedelta

import pytest
from homeassistant.util import dt as dt_util

from custom_components.battery_management import coordinator as coordinator_module
from custom_components.battery_management.const import UNACKED_TICKS

from .conftest import GRID_SENSOR


@pytest.fixture
def clock(monkeypatch):
    """A clock the test drives.

    The threshold is a duration, not a tick count, so that a loop which skipped
    ticks cannot be mistaken for a pack that went deaf. Ticks in a test take no
    time at all, so the time has to come from somewhere.
    """
    now = dt_util.utcnow()

    def advance(seconds: float) -> None:
        nonlocal now
        now = now + timedelta(seconds=seconds)

    monkeypatch.setattr(coordinator_module.dt_util, "utcnow", lambda: now)
    return advance


def issue_for(system) -> str:
    return f"{system.entry.entry_id}_writes_not_acknowledged"


def echo_targets(system) -> None:
    """Play a pack that accepts orders: the device shows the value back."""
    for entity, value in system.hass.services.targets_set().items():
        system.hass.states.set(entity, value, {"max": 3500})


async def run(system, ticks: int, *, obedient: bool, advance) -> None:
    for _ in range(ticks):
        await system.coordinator._async_tick(None)
        if obedient:
            echo_targets(system)
        advance(15)
        # the meter keeps reporting throughout; this is about the packs
        system.hass.states.set(GRID_SENSOR, 1000)


async def test_a_pack_that_answers_is_never_flagged(build_system, issues, clock):
    system = build_system(grid=1000)

    await run(system, UNACKED_TICKS + 3, obedient=True, advance=clock)

    assert system.coordinator.write_stalled == {}
    assert issue_for(system) not in issues


async def test_a_pack_that_never_answers_is_flagged(build_system, issues, clock):
    """The failure that used to be invisible."""
    system = build_system(grid=1000)

    await run(system, UNACKED_TICKS + 2, obedient=False, advance=clock)

    assert set(system.coordinator.write_stalled) == {"Batterij 1", "Batterij 2"}
    assert system.coordinator.status == "degraded"
    assert issue_for(system) in issues


async def test_it_holds_off_until_well_past_the_packs_own_lag(
    build_system, issues, clock
):
    """The packs answer 10-30 s late (gotcha 2); that is not a fault.

    Flagging on the first unanswered tick would cry wolf every time a pack took
    its normal time to respond, and an alarm that is usually wrong gets ignored
    exactly when it is right.
    """
    system = build_system(grid=1000)

    await run(system, 2, obedient=False, advance=clock)

    assert system.coordinator.write_stalled == {}
    assert issue_for(system) not in issues


async def test_it_clears_once_the_pack_answers_again(build_system, issues, clock):
    system = build_system(grid=1000)
    await run(system, UNACKED_TICKS + 2, obedient=False, advance=clock)
    assert issue_for(system) in issues

    await run(system, 2, obedient=True, advance=clock)

    assert system.coordinator.write_stalled == {}
    assert issue_for(system) not in issues


async def test_dry_run_is_not_a_deaf_pack(build_system, issues, clock):
    """Nothing is written in a shadow run, so nothing can be acknowledged.

    Flagging here would fire on every shadow install on day one and teach
    whoever is watching a month of comparison data to ignore the warning.
    """
    system = build_system(grid=1000, dry_run=True)

    await run(system, UNACKED_TICKS + 3, obedient=False, advance=clock)

    assert system.coordinator.write_stalled == {}
    assert issue_for(system) not in issues
    # the shadow run's own proof of life is unaffected
    assert system.coordinator.suppressed_commands > 0


async def test_it_says_which_pack_and_for_how_long(build_system, issues, clock):
    """A warning naming neither is a warning nobody can act on."""
    system = build_system(grid=1000)

    await run(system, UNACKED_TICKS + 2, obedient=False, advance=clock)

    placeholders = issues[issue_for(system)]["translation_placeholders"]
    assert "Batterij 1" in placeholders["units"]
    assert int(placeholders["seconds"]) >= 15 * UNACKED_TICKS

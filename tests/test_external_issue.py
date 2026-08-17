"""Telling the user what to install, not just that nothing is arriving.

"External plan went quiet" is accurate and useless on its own — nobody guesses
from a sensor state that they need EMHASS. A repair issue puts it where Home
Assistant already collects things that need attention.
"""
from __future__ import annotations

from datetime import timedelta

import pytest

from custom_components.battery_management import coordinator as coordinator_module
from custom_components.battery_management.const import (
    CONF_EXTERNAL_TIMEOUT,
    CONF_GRID_MAX_AGE,
    MODE_EXTERNAL,
    MODE_GRID_ZERO,
)

ISSUE = "test_entry_external_no_plan"


async def test_no_warning_in_the_normal_modes(build_system, issues):
    system = build_system(grid=500)

    await system.coordinator._async_tick(None)

    assert ISSUE not in issues


async def test_warns_as_soon_as_the_mode_is_chosen(build_system, issues):
    """Do not make the user wait a tick to find out what is missing."""
    system = build_system(grid=500)

    await system.coordinator.async_set_mode(MODE_EXTERNAL)

    assert ISSUE in issues
    assert issues[ISSUE]["translation_key"] == "external_no_plan"
    assert issues[ISSUE]["severity"] == "warning"


async def test_the_warning_says_how_long_it_waited(build_system, issues):
    system = build_system(grid=500, **{CONF_EXTERNAL_TIMEOUT: 30})

    await system.coordinator.async_set_mode(MODE_EXTERNAL)

    assert issues[ISSUE]["translation_placeholders"]["timeout"] == "30"


async def test_it_points_somewhere_that_explains_emhass(build_system, issues):
    system = build_system(grid=500)

    await system.coordinator.async_set_mode(MODE_EXTERNAL)

    assert issues[ISSUE]["learn_more_url"].startswith("https://github.com/")


async def test_a_plan_arriving_clears_it(build_system, issues):
    system = build_system(grid=500)
    await system.coordinator.async_set_mode(MODE_EXTERNAL)
    assert ISSUE in issues

    await system.coordinator.async_set_setpoint(1200)
    await system.coordinator._async_tick(None)

    assert ISSUE not in issues


async def test_leaving_the_mode_clears_it(build_system, issues):
    system = build_system(grid=500)
    await system.coordinator.async_set_mode(MODE_EXTERNAL)
    assert ISSUE in issues

    await system.coordinator.async_set_mode(MODE_GRID_ZERO)

    assert ISSUE not in issues


async def test_it_comes_back_when_the_plan_goes_quiet(
    build_system, issues, monkeypatch
):
    """The whole failure mode: EMHASS stops, and nobody notices."""
    # same reason as in test_external_plan: the clock jump would otherwise
    # age out the meter as well, and that is not what is under test
    system = build_system(
        grid=500, **{CONF_EXTERNAL_TIMEOUT: 15, CONF_GRID_MAX_AGE: 0}
    )
    await system.coordinator.async_set_mode(MODE_EXTERNAL)
    await system.coordinator.async_set_setpoint(1200)
    await system.coordinator._async_tick(None)
    assert ISSUE not in issues

    real_now = coordinator_module.dt_util.utcnow()
    monkeypatch.setattr(
        coordinator_module.dt_util, "utcnow", lambda: real_now + timedelta(minutes=20)
    )
    await system.coordinator._async_tick(None)

    assert ISSUE in issues


@pytest.mark.parametrize("dry_run", [True, False])
async def test_it_warns_in_dry_run_too(build_system, issues, dry_run):
    """A shadow month is exactly when you want to find this out."""
    system = build_system(grid=500, dry_run=dry_run)

    await system.coordinator.async_set_mode(MODE_EXTERNAL)

    assert ISSUE in issues


async def test_the_registry_is_only_touched_on_a_change(build_system, issues):
    """No registry churn four times a minute for a state that has not moved.

    One reconciliation on the very first tick is deliberate and is the whole
    point of the hand-back fix: a coordinator that has just started cannot know
    what a previous run left in the registry, so it clears once. What must not
    happen is that repeating every tick forever.
    """
    system = build_system(grid=500)
    calls: list[str] = []
    original = coordinator_module.ir.async_delete_issue
    coordinator_module.ir.async_delete_issue = staticmethod(
        lambda hass, domain, issue_id: (calls.append(issue_id), original(hass, domain, issue_id))[1]
    )

    await system.coordinator._async_tick(None)
    after_reconciling = len(calls)

    for _ in range(5):
        await system.coordinator._async_tick(None)

    assert len(calls) == after_reconciling

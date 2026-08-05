"""Dry run: decide everything, command nothing.

This is the gate to testing on real hardware. The owner runs a working YAML
setup on live batteries; the plan is a month of shadow running beside it. So the
bar here is absolute — not one write may escape, including the ones that look
like housekeeping.
"""
from __future__ import annotations

import time

import pytest

from custom_components.battery_management.const import (
    DEFAULT_DRY_RUN,
    MODE_DYNAMIC,
    POLICY_GRID_ZERO,
)


def test_ships_enabled_so_a_fresh_install_watches_first(build_system):
    """Never field-tested: a new site observes before it acts."""
    assert DEFAULT_DRY_RUN is True


async def test_not_a_single_command_escapes(build_system):
    system = build_system(grid=500, dry_run=True)

    await system.coordinator._async_tick(None)

    assert system.hass.services.calls == []


async def test_it_still_decides_everything(build_system):
    """Shadow running is worthless if the decisions stop too."""
    live = build_system(grid=500)
    shadow = build_system(grid=500, dry_run=True)

    await live.coordinator._async_tick(None)
    await shadow.coordinator._async_tick(None)

    assert shadow.coordinator.setpoint == live.coordinator.setpoint
    assert shadow.coordinator.active_policy == POLICY_GRID_ZERO
    # the per-unit targets are the comparison data, so they must keep publishing
    assert {
        n: s.target for n, s in shadow.coordinator.unit_status.items()
    } == {n: s.target for n, s in live.coordinator.unit_status.items()}


async def test_turning_the_coordinator_on_does_not_claim_the_packs(build_system):
    """Setting third_party_control would fight the site's own automations."""
    system = build_system(grid=500, enabled=False, dry_run=True)

    await system.coordinator.async_set_enabled(True)

    assert system.hass.services.calls == []


async def test_the_safe_revert_is_suppressed_too(build_system):
    """Handing the packs back is still a write, and something else owns them."""
    system = build_system(grid=500, dry_run=True)

    await system.coordinator.async_stop(revert=True)

    assert system.hass.services.calls == []


async def test_fast_charge_commands_nothing_either(build_system):
    system = build_system(units=(("093", 20.0), ("052", 20.0)), enabled=False, dry_run=True)

    await system.coordinator.async_set_fast_charge(True)

    assert system.hass.services.calls == []
    assert system.coordinator.unit_status["Batterij 1"].target == -3500


async def test_dynamic_mode_buys_nothing_in_dry_run(build_system):
    """The one mode that spends money must be silent too."""
    system = build_system(
        grid=500,
        units=(("093", 20.0), ("052", 20.0)),
        dry_run=True,
        price_sensor="sensor.prices",
    )
    system.coordinator.mode = MODE_DYNAMIC

    await system.coordinator._async_tick(None)

    assert system.hass.services.calls == []


async def test_counts_what_it_held_back(build_system):
    """Proof of life: a shadow run that suppressed nothing is a broken one."""
    system = build_system(grid=500, dry_run=True)

    await system.coordinator._async_tick(None)

    assert system.coordinator.suppressed_commands == 4  # flow + target, twice


async def test_going_live_lets_commands_through_again(build_system):
    system = build_system(grid=500, dry_run=True)
    await system.coordinator._async_tick(None)
    assert system.hass.services.calls == []

    await system.coordinator.async_set_dry_run(False)

    assert system.hass.services.calls  # the tick it kicks off writes for real


@pytest.mark.parametrize("stored", [True, False])
async def test_the_setting_survives_a_restart(build_system, stored):
    """A month-old shadow install must not come back live after a reboot."""
    system = build_system(grid=500, enabled=False)
    system.coordinator._store.data = {
        "enabled": False,
        "setpoint": 0.0,
        "dry_run": stored,
        "saved_at": time.time(),
    }

    await system.coordinator._async_restore()

    assert system.coordinator.dry_run is stored

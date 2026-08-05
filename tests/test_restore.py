"""Surviving a restart or an options reload.

Both a Home Assistant restart and saving the options tear the entry down and
build a fresh coordinator, which used to mean the kill-switch silently went off
and the integrator restarted from zero.

These drive `_async_restore` rather than `async_start`, for the same reason the
tick tests drive `_async_tick`: the other half of `async_start` only registers a
timer, which needs a real event loop and would never fire here anyway.
"""
from __future__ import annotations

import time

from custom_components.battery_management.const import (
    MAX_SETPOINT_AGE,
    DEVICE_MODE_SELF,
    DEVICE_MODE_THIRD_PARTY,
)


async def test_starts_disabled_when_nothing_was_stored(build_system):
    system = build_system(grid=500, enabled=False)

    await system.coordinator._async_restore()

    assert system.coordinator.enabled is False
    assert system.hass.services.calls == []


async def test_resumes_enabled_after_a_reload(build_system):
    system = build_system(grid=500, enabled=False)
    system.coordinator._store.data = {
        "enabled": True,
        "setpoint": 640.0,
        "saved_at": time.time(),
    }

    await system.coordinator._async_restore()

    assert system.coordinator.enabled is True
    assert system.coordinator.setpoint == 640
    # the packs are put back under third-party control straight away
    modes = system.hass.services.options_set()
    assert modes[system.mode(0)] == DEVICE_MODE_THIRD_PARTY
    assert modes[system.mode(1)] == DEVICE_MODE_THIRD_PARTY


async def test_stale_setpoint_is_dropped_but_the_switch_still_resumes(build_system):
    """An hour-old setpoint says nothing about the house right now."""
    system = build_system(grid=500, enabled=False)
    system.coordinator._store.data = {
        "enabled": True,
        "setpoint": 3000.0,
        "saved_at": time.time() - (MAX_SETPOINT_AGE + 60),
    }

    await system.coordinator._async_restore()

    assert system.coordinator.enabled is True
    assert system.coordinator.setpoint == 0


async def test_stays_off_when_the_user_switched_it_off(build_system):
    system = build_system(grid=500, enabled=False)
    system.coordinator._store.data = {
        "enabled": False,
        "setpoint": 640.0,
        "saved_at": time.time(),
    }

    await system.coordinator._async_restore()

    assert system.coordinator.enabled is False
    assert system.coordinator.setpoint == 0


async def test_fast_charge_is_never_resumed(build_system):
    """Manual emergency action: resuming a grid charge unattended is costly."""
    system = build_system(grid=500, enabled=False)
    system.coordinator._store.data = {
        "enabled": True,
        "fast_charge": True,
        "setpoint": 0.0,
        "saved_at": time.time(),
    }

    await system.coordinator._async_restore()

    assert system.coordinator.fast_charge is False


async def test_switching_off_is_written_through_immediately(build_system):
    system = build_system(grid=500, enabled=False)

    await system.coordinator.async_set_enabled(True)
    assert system.coordinator._store.data["enabled"] is True

    await system.coordinator.async_set_enabled(False)
    assert system.coordinator._store.data["enabled"] is False


async def test_a_tick_persists_the_running_setpoint(build_system):
    system = build_system(grid=500)

    await system.coordinator._async_tick(None)

    stored = system.coordinator._store.data
    assert stored["setpoint"] == 500
    assert stored["enabled"] is True


async def test_a_broken_store_does_not_block_startup(build_system):
    system = build_system(grid=500, enabled=False)

    async def boom():
        raise OSError("storage is corrupt")

    system.coordinator._store.async_load = boom

    await system.coordinator._async_restore()  # must not raise

    assert system.coordinator.enabled is False


async def test_reverting_still_hands_the_units_back(build_system):
    """Auto-resume must not weaken the safe revert on unload."""
    system = build_system(grid=500)
    await system.coordinator._async_tick(None)

    await system.coordinator.async_stop(revert=True)

    modes = system.hass.services.options_set()
    assert modes[system.mode(0)] == DEVICE_MODE_SELF
    assert modes[system.mode(1)] == DEVICE_MODE_SELF

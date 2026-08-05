"""Letting go of a unit that has nowhere to be let go to.

At the primary site the two packs do not offer the same modes. Unit 093 has all
six; unit 052 offers only `third_party_control` and `custom_mode`, because its
own P1 meter is not connected and the firmware therefore hides self-consumption
entirely.

That makes the fixed `self_consumption` in the revert a silent failure on that
unit: the command is simply not accepted, and per gotcha 1 the pack keeps doing
whatever it was last told, forever.
"""
from __future__ import annotations

from custom_components.battery_management.const import (
    CONF_MODE_CONTROL,
    CONF_MODE_SAFE,
    CONF_UNITS,
    DEVICE_MODE_SELF,
    DEVICE_MODE_THIRD_PARTY,
)


def make_meterless(system, index: int = 1) -> None:
    """Reconfigure one unit the way unit 052 actually is."""
    system.coordinator.units[index].mode_safe = None


async def test_a_meterless_unit_is_zeroed_but_its_mode_is_left_alone(build_system):
    system = build_system(grid=500)
    make_meterless(system)

    await system.coordinator.async_stop(revert=True)

    targets = system.hass.services.targets_set()
    modes = system.hass.services.options_set()
    # both packs are commanded to stop
    assert targets[system.target(0)] == 0
    assert targets[system.target(1)] == 0
    # only the one that has somewhere to go is switched back
    assert modes[system.mode(0)] == DEVICE_MODE_SELF
    assert system.mode(1) not in modes


async def test_zeroing_comes_first(build_system):
    """The dangerous order would be switching modes and then zeroing."""
    system = build_system(grid=500)

    await system.coordinator.async_stop(revert=True)

    calls = system.hass.services.calls
    first_target = next(
        i for i, c in enumerate(calls) if c.data.get("entity_id") == system.target(0)
    )
    first_mode = next(
        i for i, c in enumerate(calls) if c.data.get("entity_id") == system.mode(0)
    )
    assert first_target < first_mode


async def test_taking_control_uses_each_unit_s_own_option(build_system):
    system = build_system(grid=500, enabled=False)
    # the property hands back the same objects, so this reaches the coordinator
    system.coordinator.units[1].mode_control = "custom_mode"

    await system.coordinator.async_set_enabled(True)

    modes = system.hass.services.options_set()
    assert modes[system.mode(0)] == DEVICE_MODE_THIRD_PARTY
    assert modes[system.mode(1)] == "custom_mode"


async def test_the_kill_switch_still_stops_a_meterless_unit(build_system):
    """It cannot be handed back, but it must stop. 0 W held forever is safe;
    a non-zero command held forever is what gotcha 1 is about."""
    system = build_system(grid=500)
    make_meterless(system)
    await system.coordinator._async_tick(None)
    assert sum(system.allocation().values()) == 500

    system.hass.services.clear()
    await system.coordinator.async_set_enabled(False)

    assert system.hass.services.targets_set()[system.target(1)] == 0


async def test_a_configured_entry_carries_the_choice_through(build_system):
    system = build_system(grid=500)
    system.entry.data[CONF_UNITS][1][CONF_MODE_CONTROL] = "third_party_control"
    system.entry.data[CONF_UNITS][1][CONF_MODE_SAFE] = ""

    rebuilt = type(system.coordinator)(system.hass, system.entry)

    assert rebuilt.units[1].mode_safe is None
    assert rebuilt.units[0].mode_safe == DEVICE_MODE_SELF

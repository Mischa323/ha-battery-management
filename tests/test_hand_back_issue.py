"""Noticing that a unit cannot be handed back to the mode it is told to.

Found the hard way at the primary site. One pack has no P1 meter of its own, so
its firmware offers no self-consumption mode at all — but the stored setting
said to return it there. The safe revert would have been silently refused, and
per gotcha 1 that pack then keeps its last instruction forever.

Dry run never surfaces it, because nothing is ever written. So it has to be
noticed by looking, not by failing.
"""
from __future__ import annotations

import pytest

from custom_components.battery_management.const import (
    DEVICE_MODE_SELF,
    DEVICE_MODE_THIRD_PARTY,
)

BOTH = [DEVICE_MODE_SELF, DEVICE_MODE_THIRD_PARTY]
METERLESS = [DEVICE_MODE_THIRD_PARTY, "custom_mode"]


def offer(system, index: int, options: list[str]) -> None:
    system.hass.states.set(system.mode(index), options[0], {"options": options})


def issue_for(system, name: str) -> str:
    return f"{system.entry.entry_id}_hand_back_{name}"


async def test_quiet_when_every_unit_can_be_handed_back(build_system, issues):
    system = build_system(grid=500)
    for index in (0, 1):
        offer(system, index, BOTH)

    await system.coordinator._async_tick(None)

    assert issues == {}


async def test_warns_when_the_unit_does_not_offer_that_mode(build_system, issues):
    system = build_system(grid=500)
    offer(system, 0, BOTH)
    offer(system, 1, METERLESS)  # unit 052: no meter, so no self-consumption

    await system.coordinator._async_tick(None)

    key = issue_for(system, "Batterij 2")
    assert key in issues
    assert issues[key]["translation_placeholders"]["unit"] == "Batterij 2"
    assert issues[key]["translation_placeholders"]["mode"] == DEVICE_MODE_SELF
    assert "custom_mode" in issues[key]["translation_placeholders"]["options"]
    # the healthy unit is not accused
    assert issue_for(system, "Batterij 1") not in issues


async def test_an_empty_hand_back_is_never_a_problem(build_system, issues):
    """Commanding 0 W and leaving the mode alone is what a meterless pack needs."""
    system = build_system(grid=500)
    offer(system, 1, METERLESS)
    system.coordinator.units[1].mode_safe = None

    await system.coordinator._async_tick(None)

    assert issues == {}


async def test_a_select_that_publishes_no_options_is_not_accused(build_system, issues):
    """Permissive, like the setup check: do not guess at an odd firmware."""
    system = build_system(grid=500)  # no options attribute at all

    await system.coordinator._async_tick(None)

    assert issues == {}


async def test_it_clears_once_corrected(build_system, issues):
    system = build_system(grid=500)
    offer(system, 1, METERLESS)
    await system.coordinator._async_tick(None)
    assert issue_for(system, "Batterij 2") in issues

    system.coordinator.units[1].mode_safe = None
    await system.coordinator._async_tick(None)

    assert issue_for(system, "Batterij 2") not in issues


@pytest.mark.parametrize("enabled", [True, False])
async def test_it_looks_even_before_being_switched_on(build_system, issues, enabled):
    """The whole point is finding this before it matters."""
    system = build_system(grid=500, enabled=enabled)
    offer(system, 1, METERLESS)

    await system.coordinator._async_tick(None)

    assert issue_for(system, "Batterij 2") in issues


async def test_diagnostics_say_whether_the_modes_were_chosen(build_system):
    """A defaulted value looks identical to a chosen one, which is how this
    stayed invisible in an entry predating the mode step."""
    system = build_system(grid=500)
    offer(system, 0, BOTH)

    report = system.coordinator.diagnostics()

    assert report["units"][0]["modes"]["explicitly_set"] is False
    assert report["units"][0]["modes"]["select_offers"] == BOTH

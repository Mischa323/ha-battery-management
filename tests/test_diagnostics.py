"""The diagnostics download and the flight recorder behind it.

During a shadow month this is how the data leaves the site: one button on the
integration page, one JSON file. So it has to carry enough to explain a single
decision, and it has to be JSON-serialisable — a payload that crashes on
download is worse than none.
"""
from __future__ import annotations

import json

from custom_components.battery_management.const import (
    MODE_CHARGE_ONLY,
    POLICY_GRID_ZERO,
    TICK_LOG_SIZE,
)


async def test_records_a_row_per_tick(build_system):
    system = build_system(grid=500)

    for _ in range(3):
        await system.coordinator._async_tick(None)

    assert len(system.coordinator.tick_log) == 3


async def test_a_row_explains_the_decision(build_system):
    system = build_system(grid=500)

    await system.coordinator._async_tick(None)

    row = system.coordinator.tick_log[-1]
    assert row["grid_w"] == 500
    assert row["setpoint_w"] == 500
    assert row["policy"] == POLICY_GRID_ZERO
    assert row["units"]["Batterij 1"]["target_w"] == 288
    assert row["units"]["Batterij 1"]["soc"] == 80


async def test_dry_run_is_recorded_too(build_system):
    """Recording only live ticks would defeat the point of dry run."""
    system = build_system(grid=500, dry_run=True)

    await system.coordinator._async_tick(None)

    assert system.coordinator.tick_log[-1]["dry_run"] is True
    assert system.coordinator.tick_log[-1]["setpoint_w"] == 500


async def test_the_log_is_bounded(build_system):
    """A month of ticks must not become a database in RAM."""
    system = build_system(grid=500)

    for _ in range(TICK_LOG_SIZE + 50):
        await system.coordinator._async_tick(None)

    assert len(system.coordinator.tick_log) == TICK_LOG_SIZE


async def test_diagnostics_carry_settings_state_and_units(build_system):
    system = build_system(grid=500, dry_run=True)
    system.coordinator.mode = MODE_CHARGE_ONLY
    await system.coordinator._async_tick(None)

    report = system.coordinator.diagnostics()

    assert report["settings"]["kp"] == 1.0
    assert report["state"]["dry_run"] is True
    assert report["state"]["mode"] == MODE_CHARGE_ONLY
    assert [u["name"] for u in report["units"]] == ["Batterij 1", "Batterij 2"]
    assert report["units"][0]["entities"]["soc_sensor"].startswith("sensor.")
    assert len(report["recent_ticks"]) == 1


async def test_diagnostics_survive_being_turned_into_json(build_system):
    """It is downloaded as a file; an unserialisable value breaks the button."""
    system = build_system(grid=500)
    await system.coordinator._async_tick(None)

    encoded = json.dumps(system.coordinator.diagnostics())

    assert "recent_ticks" in encoded


async def test_diagnostics_work_before_any_tick_has_run(build_system):
    """The button exists from the moment the integration loads."""
    system = build_system(grid=500, enabled=False)

    report = system.coordinator.diagnostics()

    assert report["recent_ticks"] == []
    assert report["state"]["last_tick"] is None

"""The trace file: what it records, and that it cannot break a tick.

Every diagnosis attempted so far ran into the same wall - the question was
about a day that had already scrolled out of the in-memory log. So the bar for
these tests is not "a file appears" but "the file answers the questions we
actually asked": what did we command, what did the pack do, which bound was
biting, and which leg moved during a probe.
"""
from __future__ import annotations

import csv
import os

import pytest

from custom_components.battery_management.const import (
    CONF_KP,
    CONF_PHASE_DETECT,
    CONF_TRACE,
    CONF_TRACE_DAYS,
)
from custom_components.battery_management.trace import Trace

pytestmark = pytest.mark.asyncio


def rows(directory) -> list[dict]:
    out: list[dict] = []
    for name in sorted(os.listdir(directory)):
        with open(os.path.join(directory, name), encoding="utf-8") as handle:
            out.extend(csv.DictReader(handle))
    return out


@pytest.fixture
def traced(build_system, tmp_path, monkeypatch):
    """A system whose trace lands in a temporary directory."""

    def _build(**kwargs):
        system = build_system(**{CONF_TRACE: True, **kwargs})
        system.coordinator._trace = Trace(str(tmp_path), keep_days=14)
        system.trace_dir = str(tmp_path)
        return system

    return _build


async def test_a_tick_lands_on_disk_with_the_numbers_that_made_it(traced):
    system = traced(grid=1200)

    for _ in range(25):                      # past the flush threshold
        await system.coordinator._async_tick(None)

    written = rows(system.trace_dir)
    assert written, "nothing was written at all"
    row = written[0]
    # the inputs...
    assert row["grid_w"] == "1200"
    assert row["error_w"] and row["setpoint_w"]
    # ...and why the setpoint came out the way it did
    assert row["sp_before_w"] and row["sp_wanted_w"]
    assert row["sp_reason"] in {"integrate", "deadband", "clamped_upper", "clamped_lower"}
    assert row["upper_w"] and row["lower_w"]
    assert row["mode"] and row["policy"]


async def test_it_records_what_each_pack_was_told(traced):
    system = traced(grid=2000)

    for _ in range(25):
        await system.coordinator._async_tick(None)

    row = rows(system.trace_dir)[-1]
    assert "batterij_1_target_w" in row
    assert "batterij_2_target_w" in row
    assert row["batterij_1_soc"]


async def test_the_clamp_says_which_way_it_bit(traced):
    """A flat setpoint with no reason is the hardest thing to diagnose."""
    system = traced(grid=40000)              # far more than the packs can give

    for _ in range(25):
        await system.coordinator._async_tick(None)

    assert any(r["sp_reason"] == "clamped_upper" for r in rows(system.trace_dir))


async def test_the_legs_are_recorded_when_they_are_configured(traced):
    # detection off: a probe returns before the tick is logged, and this
    # test is about the legs appearing in the row, not about probing
    system = traced(grid=500, phases=(1000, 200, 300), **{CONF_PHASE_DETECT: False})

    for _ in range(25):
        await system.coordinator._async_tick(None)

    row = rows(system.trace_dir)[0]
    assert row["phase1_w"] == "1000"
    assert row["phase3_w"] == "300"


async def test_a_broken_disk_costs_the_trace_and_not_the_batteries(traced):
    """The whole point of the try/except around the writer."""
    system = traced(grid=1500)
    system.coordinator._trace._dir = "\0 definitely not a directory"

    for _ in range(25):
        await system.coordinator._async_tick(None)

    assert system.coordinator.status != "degraded"
    assert system.coordinator._trace.errors > 0
    assert system.coordinator._trace.last_error
    assert sum(system.allocation().values()) != 0, "it stopped regulating"


async def test_old_days_are_deleted_and_recent_ones_kept(tmp_path):
    trace = Trace(str(tmp_path), keep_days=3)
    from datetime import datetime, timedelta, timezone

    now = datetime.now(timezone.utc)
    old = tmp_path / f"{now - timedelta(days=10):%Y-%m-%d}.csv"
    recent = tmp_path / f"{now - timedelta(days=1):%Y-%m-%d}.csv"
    stranger = tmp_path / "notes.txt"
    for path in (old, recent, stranger):
        path.write_text("x", encoding="utf-8")

    trace.add({"at": "now", "grid_w": 1})
    trace.flush()

    assert not old.exists()
    assert recent.exists()
    assert stranger.exists(), "it deleted a file that was not its own"


async def test_the_header_survives_a_new_column(tmp_path):
    """A field appearing mid-run must not shift every earlier column."""
    trace = Trace(str(tmp_path))
    trace.add({"at": "1", "grid_w": 10})
    trace.flush()
    trace.add({"at": "2", "grid_w": 20, "new_thing": 5})
    trace.flush()

    written = rows(tmp_path)
    assert written[0]["grid_w"] == "10"
    assert written[1]["grid_w"] == "20"


async def test_stopping_writes_out_what_was_buffered(traced):
    """The last minute before a restart is the one worth having."""
    system = traced(grid=900)
    await system.coordinator._async_tick(None)
    assert not os.listdir(system.trace_dir), "flushed earlier than expected"

    await system.coordinator.async_stop(revert=False)

    assert rows(system.trace_dir)


async def test_it_can_be_switched_off(build_system):
    system = build_system(grid=1000, **{CONF_TRACE: False})
    assert system.coordinator._trace is None

    await system.coordinator._async_tick(None)          # must not raise

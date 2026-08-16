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


def misaligned(directory) -> list[str]:
    """Files where some row does not match its own header. Should be none."""
    bad = []
    for name in sorted(os.listdir(directory)):
        lines = (
            open(os.path.join(directory, name), encoding="utf-8").read().splitlines()
        )
        width = len(lines[0].split(","))
        if any(len(line.split(",")) != width for line in lines[1:]):
            bad.append(name)
    return bad


async def test_new_columns_start_a_new_file_instead_of_corrupting_the_old_one(tmp_path):
    """The bug this exists to prevent, seen on the first day it ran.

    An upgrade took the row from 37 fields to 46. The file for that day already
    existed, so no new header was written, and 619 of 643 rows were appended
    under a header nine columns too short - every value in them shifted. The
    file looked complete and read back as mostly-empty columns.
    """
    trace = Trace(str(tmp_path))
    trace.add({"at": "1", "grid_w": 10})
    trace.flush()

    # a new release adds a column, mid-day, to a file that already exists
    later = Trace(str(tmp_path))
    later.add({"at": "2", "grid_w": 20, "ack_s": 5})
    later.flush()

    assert not misaligned(tmp_path), "a row does not match its own header"
    written = {r["at"]: r for r in rows(tmp_path)}
    assert written["1"]["grid_w"] == "10"
    assert written["2"]["grid_w"] == "20"
    assert written["2"]["ack_s"] == "5"
    assert len(os.listdir(tmp_path)) == 2, "should have rotated to a second file"


async def test_the_same_columns_keep_appending_to_one_file(tmp_path):
    """Rotating on every restart would litter the folder for no reason."""
    first = Trace(str(tmp_path))
    first.add({"at": "1", "grid_w": 10})
    first.flush()

    second = Trace(str(tmp_path))          # a restart, same schema
    second.add({"at": "2", "grid_w": 20})
    second.flush()

    assert len(os.listdir(tmp_path)) == 1
    assert len(rows(tmp_path)) == 2


async def test_a_unit_coming_online_mid_run_does_not_shift_the_columns(traced):
    """The same failure without an upgrade: a pack appears and brings its own
    columns with it."""
    system = traced(grid=1500, units=(("093", 80.0), ("052", None)))

    for _ in range(25):
        await system.coordinator._async_tick(None)
    system.hass.states.set(system.soc(1), 45)      # 052 comes back
    for _ in range(25):
        await system.coordinator._async_tick(None)
    await system.coordinator.async_stop(revert=False)

    assert not misaligned(system.trace_dir)


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


async def test_it_records_how_old_the_meter_reading_was(traced):
    """Regulating on a stale number explains a lot, once it is written down."""
    from datetime import timedelta

    from homeassistant.util import dt as dt_util

    from tests.conftest import GRID_SENSOR, FakeState

    system = traced(grid=800)
    system.hass.states.set(GRID_SENSOR, 800)
    stale = dt_util.utcnow() - timedelta(seconds=45)
    system.hass.states._states[GRID_SENSOR] = FakeState(800, last_updated=stale)

    for _ in range(25):
        await system.coordinator._async_tick(None)

    ages = [float(r["grid_age_s"]) for r in rows(system.trace_dir) if r["grid_age_s"]]
    assert ages and max(ages) >= 44, ages


async def test_it_times_how_long_a_pack_takes_to_accept_a_command(traced):
    """The other half of the lag, and the half nobody had measured."""
    system = traced(grid=2000)
    coordinator = system.coordinator

    await coordinator._async_tick(None)
    commanded = system.allocation()["Batterij 1"]
    assert commanded, "nothing was commanded, so there is nothing to time"
    # the pack has not echoed it back yet
    assert coordinator._check_ack(coordinator._units[0]) is None

    # ...and now it has
    system.hass.states.set(system.target(0), abs(commanded))
    assert coordinator._check_ack(coordinator._units[0]) is not None


async def test_a_pack_that_never_answers_leaves_the_time_empty(traced):
    """An empty column is the finding, not a gap in the data."""
    system = traced(grid=2000)
    coordinator = system.coordinator

    for _ in range(25):
        await coordinator._async_tick(None)          # readback never moves

    written = rows(system.trace_dir)
    assert all(not r.get("batterij_1_ack_s") for r in written)
    # but what the device *does* hold is recorded, so the two can be compared
    assert any(r.get("batterij_1_readback_w") is not None for r in written)


async def test_the_gain_is_not_rounded_away(traced):
    """It logged as "0" for a whole day.

    The bounds are watts and round to whole numbers; the gain is 0.25 or 0.5
    and went through the same rounding, so the one column that says which of
    the two directions was in play recorded nothing at all.
    """
    system = traced(grid=3000, **{CONF_KP: 0.25})

    for _ in range(25):
        await system.coordinator._async_tick(None)

    gains = {r["gain"] for r in rows(system.trace_dir) if r["gain"]}
    assert gains, "no gain recorded at all"
    assert gains == {"0.25"}, gains


async def test_it_records_which_of_the_two_gains_was_used(traced):
    """The whole reason the column exists."""
    from tests.conftest import GRID_SENSOR

    system = traced(grid=3000, **{CONF_KP: 0.25, "kp_return": 0.5})
    coordinator = system.coordinator

    for _ in range(12):
        await coordinator._async_tick(None)
    system.hass.states.set(GRID_SENSOR, -3000)      # now swing the other way
    for _ in range(13):
        await coordinator._async_tick(None)
    await coordinator.async_stop(revert=False)

    gains = {r["gain"] for r in rows(system.trace_dir) if r["gain"]}
    assert gains == {"0.25", "0.5"}, gains


async def test_the_commanded_power_is_signed(traced):
    """It was not, and the direction lived in a separate `flow` column.

    Summing it then said the packs had never charged, all day - which is how
    an attempt to derive their capacity from a day of trace came out as a
    discharge. Signed here means signed everywhere: the same convention as the
    Setpoint sensor and the per-unit target sensors.
    """
    from tests.conftest import GRID_SENSOR

    system = traced(grid=2500)
    coordinator = system.coordinator

    for _ in range(12):
        await coordinator._async_tick(None)
    system.hass.states.set(GRID_SENSOR, -2500)      # push it into charging
    for _ in range(13):
        await coordinator._async_tick(None)
    await coordinator.async_stop(revert=False)

    written = rows(system.trace_dir)
    values = [float(r["batterij_1_target_w"]) for r in written if r["batterij_1_target_w"]]
    assert any(v > 0 for v in values), "never recorded a discharge"
    assert any(v < 0 for v in values), "charging was not recorded as negative"
    # and the unsigned figure is still there for anyone who wants it
    assert all(
        float(r["batterij_1_target_magnitude_w"]) >= 0
        for r in written
        if r["batterij_1_target_magnitude_w"]
    )


async def test_a_meaning_change_rotates_the_file(tmp_path):
    """Renaming nothing but changing what a column means is the trap the
    schema marker exists for: same header, different semantics, no rotation."""
    first = Trace(str(tmp_path))
    first.add({"at": "1", "schema": 1, "target_w": 500})
    first.flush()

    second = Trace(str(tmp_path))
    second.add({"at": "2", "schema": 2, "target_w": -500, "target_magnitude_w": 500})
    second.flush()

    assert not misaligned(tmp_path)
    assert len(os.listdir(tmp_path)) == 2


async def test_it_records_which_run_wrote_the_row(traced):
    """Two coordinators commanding the same packs would otherwise be
    invisible. The first real trace had an unexplained extra tick three
    seconds out of cadence, and nothing in the file could say whether that
    was one loop hiccupping or two loops running."""
    system = traced(grid=1000)

    for _ in range(25):
        await system.coordinator._async_tick(None)

    runs = {r["run"] for r in rows(system.trace_dir)}
    assert len(runs) == 1 and runs != {""}


async def test_stale_and_unchanged_are_told_apart(traced):
    """A large age means "nothing arrived" or "the value held steady", and
    those are opposite conclusions. Recording both stamps separates them."""
    system = traced(grid=1000)

    for _ in range(25):
        await system.coordinator._async_tick(None)

    row = rows(system.trace_dir)[-1]
    assert row["grid_age_s"] != ""
    assert row["grid_changed_s"] != ""

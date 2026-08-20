"""Counting how much went into the packs, and how much of it was bought.

The measurement is deliberately of the *packs*, not of us. Everything the
coordinator knows about its own commands is a plan: per gotcha 2 the packs
answer 10-30 s later and their Modbus sensors arrive in bursts, so integrating
our own orders would produce an authoritative-looking kilowatt-hour figure that
is not what happened. These counters read the packs' own charging power.

The split is marginal attribution and it is exact rather than a convention:
while the packs draw Y W and the meter reads X W of import, the house without
them would have been at X - Y, so min(X, Y) was bought and the rest came out of
the surplus. Export means all of it was sun.

Most of what can go wrong here is not an exception - it is a plausible number.
So the failures are pinned individually.
"""
from __future__ import annotations

import pytest

from custom_components.battery_management import coordinator as coordinator_module
from custom_components.battery_management.const import MAX_ENERGY_GAP_INTERVALS

CHARGE_SENSORS = (
    "sensor.093_battery_charging_power",
    "sensor.052_battery_charging_power",
)


@pytest.fixture
def clock(monkeypatch):
    """A clock the test drives.

    The counters multiply power by *elapsed* time rather than by the nominal
    interval, because a tick can be late. Ticks in a test take no time at all,
    so the time has to come from somewhere.
    """
    now = [1_000_000.0]

    def advance(seconds: float) -> None:
        now[0] += seconds

    monkeypatch.setattr(coordinator_module.time, "time", lambda: now[0])
    return advance


def charging(system, *watts: float) -> None:
    for entity, value in zip(CHARGE_SENSORS, watts):
        system.hass.states.set(entity, value)


async def run(system, ticks: int, *, advance, seconds: float = 15.0) -> None:
    for _ in range(ticks):
        await system.coordinator._async_tick(None)
        advance(seconds)


async def test_it_counts_nothing_without_a_sensor(build_system, clock):
    """No pack has a charging-power sensor, so there is nothing to count.

    Unavailable rather than nought: zero is a claim about the packs, and a
    graph flat on the floor must not be able to mean "nobody is counting".
    """
    system = build_system(grid=1000)

    await run(system, 5, advance=clock)

    assert system.coordinator.counts_charge_energy is False
    assert system.coordinator.charged_wh == 0.0


async def test_a_full_hour_of_sun_is_counted_as_sun(build_system, clock):
    """Exporting throughout: every watt-hour came off the roof."""
    system = build_system(grid=-2000, charge_power=True)
    charging(system, 1000, 1000)

    # one hour, in ticks of a minute so the arithmetic is not one long step
    await run(system, 60, advance=clock, seconds=60)

    assert system.coordinator.charged_wh == pytest.approx(2000, rel=0.02)
    assert system.coordinator.charged_grid_wh == 0.0


async def test_importing_more_than_the_packs_draw_is_all_bought(build_system, clock):
    """The house is importing anyway; the packs' whole draw came off the meter."""
    system = build_system(grid=5000, charge_power=True)
    charging(system, 800, 400)

    await run(system, 60, advance=clock, seconds=60)

    assert system.coordinator.charged_wh == pytest.approx(1200, rel=0.02)
    assert system.coordinator.charged_grid_wh == pytest.approx(1200, rel=0.02)


async def test_a_partial_import_splits_at_the_margin(build_system, clock):
    """The case the whole feature exists for.

    The packs draw 2000 W while the meter imports 500 W, so without them the
    house would have been exporting 1500 W. Exactly 500 W was bought.
    """
    system = build_system(grid=500, charge_power=True)
    charging(system, 1200, 800)

    await run(system, 60, advance=clock, seconds=60)

    assert system.coordinator.charged_wh == pytest.approx(2000, rel=0.02)
    assert system.coordinator.charged_grid_wh == pytest.approx(500, rel=0.02)


async def test_the_two_halves_add_up_to_the_total(build_system, clock):
    """The sun is the remainder, never a second measurement.

    Two independent counters would drift apart within a day, and then the card
    would be splitting something that is not the whole.
    """
    system = build_system(grid=300, charge_power=True)
    charging(system, 900, 700)
    await run(system, 30, advance=clock, seconds=60)

    solar = system.coordinator.charged_wh - system.coordinator.charged_grid_wh

    assert solar > 0
    assert (
        system.coordinator.charged_grid_wh + solar
        == pytest.approx(system.coordinator.charged_wh)
    )


async def test_an_outage_does_not_manufacture_energy(build_system, clock):
    """The failure that would look most convincing.

    Home Assistant is down for two hours. The next tick sees a huge elapsed
    time, and multiplying the power read *now* by it would invent kilowatt-
    hours that were never stored - and they would land in a total nobody can
    audit afterwards. Past the cap the tick counts nothing at all.
    """
    system = build_system(grid=5000, charge_power=True)
    charging(system, 1000, 1000)

    # two ticks, so exactly one interval has genuinely been counted
    await run(system, 2, advance=clock, seconds=15)
    counted = system.coordinator.charged_wh
    assert counted == pytest.approx(2000 * 15 / 3600, rel=0.01)

    clock(2 * 3600)
    await run(system, 1, advance=clock, seconds=15)

    # the outage tick added nothing at all - not a share of it, none of it
    assert system.coordinator.charged_wh == pytest.approx(counted)


async def test_a_merely_late_tick_is_still_counted(build_system, clock):
    """The other half of that guard: lateness is normal, an outage is not."""
    system = build_system(grid=5000, charge_power=True)
    charging(system, 1000, 1000)
    interval = system.coordinator._interval

    await run(system, 1, advance=clock, seconds=interval)
    late = interval * (MAX_ENERGY_GAP_INTERVALS - 1)
    clock(late)
    await run(system, 1, advance=clock, seconds=interval)

    expected = 2000 * (interval + late) / 3600
    assert system.coordinator.charged_wh == pytest.approx(expected, rel=0.05)


async def test_an_unreadable_meter_counts_nothing(build_system, clock):
    """No meter, no attribution - and no total either.

    Crediting the lot to the sun because the meter is unreadable is exactly the
    flattering guess to avoid: it would make a broken meter look like a good
    solar day.
    """
    system = build_system(grid=None, charge_power=True)
    charging(system, 1000, 1000)

    await run(system, 10, advance=clock, seconds=60)

    assert system.coordinator.charged_wh == 0.0
    assert system.coordinator.charged_grid_wh == 0.0


async def test_an_offline_pack_contributes_nothing(build_system, clock):
    """Under-report rather than invent.

    A pack that dropped off Modbus is one we cannot see. Holding its last value
    forward would credit it with energy it may never have stored.
    """
    system = build_system(grid=-2000, charge_power=True)
    system.hass.states.set(CHARGE_SENSORS[0], 1000)
    system.hass.states.set(CHARGE_SENSORS[1], "unavailable")

    await run(system, 60, advance=clock, seconds=60)

    assert system.coordinator.charged_wh == pytest.approx(1000, rel=0.02)


async def test_a_discharge_leaking_through_is_not_charge(build_system, clock):
    """Some firmware reports the sensor signed. A negative is not a charge."""
    system = build_system(grid=-2000, charge_power=True)
    charging(system, -1500, 500)

    await run(system, 60, advance=clock, seconds=60)

    assert system.coordinator.charged_wh == pytest.approx(500, rel=0.02)


async def test_it_counts_while_the_coordinator_is_switched_off(build_system, clock):
    """The question is how the packs got charged, not who ordered it.

    With the kill-switch off the packs run native self-consumption and still
    charge; during a shadow run the site's own automations are driving. That
    energy is just as real, and a counter that only runs while we are steering
    would make a month of shadow running look like a month of nothing.
    """
    system = build_system(grid=-2000, enabled=False, charge_power=True)
    charging(system, 1000, 1000)

    await run(system, 60, advance=clock, seconds=60)

    assert system.coordinator.charged_wh == pytest.approx(2000, rel=0.02)


async def test_the_totals_survive_a_restart(build_system, clock):
    """A total that resets on restart is not a total.

    Unlike the setpoint these are restored whatever their age: an old reading
    is the entire point of a cumulative counter.
    """
    system = build_system(grid=5000, charge_power=True)
    charging(system, 1000, 1000)
    await run(system, 30, advance=clock, seconds=60)
    stored = system.coordinator._state_to_save()

    assert stored["charged_wh"] > 0
    assert stored["charged_grid_wh"] > 0

    fresh = build_system(grid=5000, charge_power=True)
    fresh.coordinator._store.async_load = _returning(stored)
    await fresh.coordinator._async_restore()

    assert fresh.coordinator.charged_wh == pytest.approx(stored["charged_wh"])
    assert fresh.coordinator.charged_grid_wh == pytest.approx(stored["charged_grid_wh"])


def _returning(value):
    async def _load():
        return value

    return _load


# --- per month, and the months that have gone, 2026-08-20 ------------------
#
# Asked for by the owner: a monthly figure that starts again on the 1st, and
# the months before it kept so the packs can be looked back on. The lifetime
# counters stay - they are what the Energy dashboard wants - and these ride
# alongside them off the same measurement.


@pytest.fixture
def wall_clock(monkeypatch):
    """The calendar, separate from the elapsed-time clock.

    The month boundary is a *local calendar* question and the energy arithmetic
    is an elapsed-seconds one, so they are two different clocks and the tests
    need to move them independently - a month has to be able to turn over
    between two ticks fifteen seconds apart.
    """
    from datetime import datetime, timezone

    at = [datetime(2026, 8, 20, 12, 0, tzinfo=timezone.utc)]
    monkeypatch.setattr(coordinator_module.dt_util, "now", lambda: at[0])

    def set_to(year, month, day=1, hour=0):
        at[0] = datetime(year, month, day, hour, tzinfo=timezone.utc)

    return set_to


async def test_the_month_counts_alongside_the_lifetime_total(build_system, clock, wall_clock):
    system = build_system(grid=5000, charge_power=True)
    charging(system, 800, 400)

    await run(system, 60, advance=clock, seconds=60)

    assert system.coordinator.month_charged_wh == pytest.approx(1200, rel=0.02)
    assert system.coordinator.month_charged_grid_wh == pytest.approx(1200, rel=0.02)
    assert system.coordinator.charged_wh == system.coordinator.month_charged_wh


async def test_the_first_of_the_month_starts_again_at_nought(build_system, clock, wall_clock):
    """August's hour must not follow the counter into September."""
    system = build_system(grid=5000, charge_power=True)
    charging(system, 800, 400)
    await run(system, 60, advance=clock, seconds=60)
    august = system.coordinator.month_charged_wh
    assert august > 0

    wall_clock(2026, 9, 1)
    await run(system, 1, advance=clock, seconds=60)

    assert system.coordinator.month_key == "2026-09"
    # September holds this one tick and nothing of August's hour: 1200 W for a
    # minute is 20 Wh. The tick that straddles the boundary is credited whole
    # to the new month by design - splitting fifteen seconds of energy across
    # two months is arithmetic nobody would ever read.
    assert system.coordinator.month_charged_wh == pytest.approx(20, rel=0.02)
    assert system.coordinator.month_charged_grid_wh == pytest.approx(20, rel=0.02)
    assert system.coordinator.month_charged_wh < august / 50
    # and the lifetime total is untouched by the changeover
    assert system.coordinator.charged_wh == pytest.approx(august + 20, rel=0.02)


async def test_the_month_that_ended_is_kept(build_system, clock, wall_clock):
    """The whole point: it has to still be there afterwards."""
    system = build_system(grid=2000, charge_power=True)
    charging(system, 1500, 500)
    await run(system, 60, advance=clock, seconds=60)

    wall_clock(2026, 9, 1)
    await run(system, 1, advance=clock, seconds=60)

    closed = system.coordinator.month_history["2026-08"]
    assert closed["charged_kwh"] == pytest.approx(2.0, rel=0.02)
    # the meter read 2000 W against 2000 W of charging: all of it bought
    assert closed["grid_kwh"] == pytest.approx(2.0, rel=0.02)


async def test_a_month_missed_entirely_still_closes_the_one_before(build_system, clock, wall_clock):
    """Home Assistant was off for six weeks. The August figure must survive it.

    This is why the changeover is checked against the clock every tick instead
    of being scheduled: nothing fires at midnight on the 1st if nothing is
    running, and the figure would then quietly keep accumulating into October.
    """
    system = build_system(grid=5000, charge_power=True)
    charging(system, 1000, 0)
    await run(system, 60, advance=clock, seconds=60)

    wall_clock(2026, 10, 3)
    await run(system, 1, advance=clock, seconds=60)

    assert system.coordinator.month_key == "2026-10"
    closed = system.coordinator.month_history["2026-08"]
    assert closed["charged_kwh"] == pytest.approx(1.0, rel=0.02)
    # October carries this one tick only - 1000 W for a minute - so August's
    # hour did not leak across the six weeks nothing was running
    assert system.coordinator.month_charged_wh == pytest.approx(16.7, rel=0.02)


async def test_the_month_turns_over_even_when_nothing_can_be_counted(build_system, clock, wall_clock):
    """No meter reading, so no energy is attributable - but the month still ends.

    The rollover sits before the early returns for exactly this: an unreadable
    meter on the 1st must not postpone the changeover to whenever the meter
    comes back, which would file the new month's first hours under the old one.
    """
    system = build_system(grid=None, charge_power=True)
    charging(system, 1000, 1000)
    await run(system, 5, advance=clock, seconds=60)

    wall_clock(2026, 9, 1)
    await run(system, 1, advance=clock, seconds=60)

    assert system.coordinator.month_key == "2026-09"


async def test_the_history_does_not_grow_without_end(build_system, clock, wall_clock):
    system = build_system(grid=5000, charge_power=True)
    charging(system, 1000, 0)

    # three years of changeovers, one tick each
    for year in (2026, 2027, 2028):
        for month in range(1, 13):
            wall_clock(year, month)
            await run(system, 1, advance=clock, seconds=60)

    from custom_components.battery_management.const import MONTH_HISTORY_MONTHS

    assert len(system.coordinator.month_history) == MONTH_HISTORY_MONTHS
    # the ones dropped are the oldest, not an arbitrary few
    assert max(system.coordinator.month_history) == "2028-11"


async def test_the_month_survives_a_restart(build_system, clock, wall_clock):
    """Come back inside the same month and the figure carries on.

    `month_key` has to be restored *before* the first tick, or that tick sees a
    coordinator with no month at all, treats the current one as brand new and
    silently starts it again at nought - which on a site that reboots weekly
    would mean the monthly figure never covered more than a few days.
    """
    system = build_system(grid=5000, charge_power=True)
    charging(system, 1000, 0)
    await run(system, 60, advance=clock, seconds=60)
    saved = system.coordinator._state_to_save()
    assert saved["periods"]["month"]["key"] == "2026-08"

    revived = build_system(grid=5000, charge_power=True)
    charging(revived, 1000, 0)
    revived.coordinator._store.data = saved
    await revived.coordinator._async_restore()

    assert revived.coordinator.month_key == "2026-08"
    restored = revived.coordinator.month_charged_wh
    assert restored == pytest.approx(983, rel=0.02)

    # The first tick back counts nothing - `_charged_at` is empty, so the
    # elapsed time is unknown and inventing one would manufacture energy. The
    # second one carries on from the restored figure rather than from nought,
    # which is the thing being checked here.
    await run(revived, 1, advance=clock, seconds=60)
    assert revived.coordinator.month_charged_wh == restored

    await run(revived, 1, advance=clock, seconds=60)
    assert revived.coordinator.month_key == "2026-08"
    assert revived.coordinator.month_charged_wh == pytest.approx(restored + 16.7, rel=0.02)


async def test_a_restart_after_the_first_still_closes_the_old_month(build_system, clock, wall_clock):
    """Down over the changeover: the first tick back has to file August, not
    lose it and not carry it into September."""
    system = build_system(grid=5000, charge_power=True)
    charging(system, 1000, 0)
    await run(system, 60, advance=clock, seconds=60)
    saved = system.coordinator._state_to_save()

    wall_clock(2026, 9, 2)
    revived = build_system(grid=5000, charge_power=True)
    charging(revived, 1000, 0)
    revived.coordinator._store.data = saved
    await revived.coordinator._async_restore()
    await run(revived, 1, advance=clock, seconds=60)

    assert revived.coordinator.month_key == "2026-09"
    assert revived.coordinator.month_history["2026-08"]["charged_kwh"] == pytest.approx(
        1.0, rel=0.02
    )


async def test_closed_months_survive_a_restart(build_system, clock, wall_clock):
    system = build_system(grid=5000, charge_power=True)
    charging(system, 1000, 0)
    await run(system, 60, advance=clock, seconds=60)
    wall_clock(2026, 9, 1)
    await run(system, 1, advance=clock, seconds=60)
    saved = system.coordinator._state_to_save()

    revived = build_system(grid=5000, charge_power=True)
    revived.coordinator._store.data = saved
    await revived.coordinator._async_restore()

    assert revived.coordinator.month_history["2026-08"]["charged_kwh"] == pytest.approx(
        1.0, rel=0.02
    )


async def test_the_reset_moment_is_the_first_of_the_month(build_system, clock, wall_clock):
    """What Home Assistant is handed as `last_reset`.

    Pinned here as well as in `test_month_sensors.py` because the arithmetic
    lives on the coordinator and this suite runs without a real Home Assistant
    - the sensor module needs the entity platform and skips when there is none,
    so without this the date behind the reset would go unchecked on every
    stubbed run.
    """
    system = build_system(grid=5000, charge_power=True)
    await run(system, 1, advance=clock, seconds=60)

    started = system.coordinator.month_started_at

    assert (started.year, started.month, started.day) == (2026, 8, 1)
    assert started.hour == 0
    assert started.tzinfo is not None

    wall_clock(2027, 1, 15)
    await run(system, 1, advance=clock, seconds=60)

    started = system.coordinator.month_started_at
    assert (started.year, started.month, started.day) == (2027, 1, 1)


async def test_no_month_yet_has_no_reset_moment(build_system):
    """Before the first tick, dating the reset to anything would be a guess."""
    system = build_system(grid=5000, charge_power=True)

    assert system.coordinator.month_key is None
    assert system.coordinator.month_started_at is None


# --- day and week, off the same accumulation, 2026-08-20 -------------------
#
# Asked for by the owner: the split per day and per week as well, chooseable,
# with the month as the default. One accumulation read at three lengths - not
# three counters, which would drift apart within a day.


async def test_all_three_periods_count_the_same_energy(build_system, clock, wall_clock):
    system = build_system(grid=5000, charge_power=True)
    charging(system, 800, 400)

    await run(system, 60, advance=clock, seconds=60)

    totals = {n: s["charged_wh"] for n, s in system.coordinator.periods.items()}
    assert set(totals) == {"day", "week", "month"}
    assert len(set(totals.values())) == 1          # one accumulation, three views
    assert totals["day"] == pytest.approx(1200, rel=0.02)


async def test_a_new_day_resets_the_day_and_leaves_the_month(build_system, clock, wall_clock):
    """The point of having three: they end at different moments."""
    system = build_system(grid=5000, charge_power=True)
    charging(system, 1000, 0)
    await run(system, 60, advance=clock, seconds=60)
    before = system.coordinator.periods["month"]["charged_wh"]

    wall_clock(2026, 8, 21)          # a Friday, so the ISO week does not turn
    await run(system, 1, advance=clock, seconds=60)

    periods = system.coordinator.periods
    assert periods["day"]["key"] == "2026-08-21"
    assert periods["day"]["charged_wh"] == pytest.approx(16.7, rel=0.02)
    # the week and the month carry straight on through a mere day boundary
    assert periods["week"]["charged_wh"] > before
    assert periods["month"]["charged_wh"] > before
    assert "2026-08-20" in periods["day"]["history"]


async def test_monday_ends_the_week(build_system, clock, wall_clock):
    """ISO weeks, so Monday is the changeover - not Sunday."""
    system = build_system(grid=5000, charge_power=True)
    charging(system, 1000, 0)
    wall_clock(2026, 8, 23)          # a Sunday
    await run(system, 30, advance=clock, seconds=60)
    sunday_week = system.coordinator.periods["week"]["key"]

    wall_clock(2026, 8, 24)          # the Monday after
    await run(system, 1, advance=clock, seconds=60)

    assert system.coordinator.periods["week"]["key"] != sunday_week
    assert sunday_week in system.coordinator.periods["week"]["history"]


async def test_the_week_key_uses_the_iso_year(build_system, clock, wall_clock):
    """31 December 2026 falls in ISO week 53 of 2026; 1 January 2027 is still
    in it. Writing the calendar year would file those two days under different
    years and sort the history wrongly across every New Year."""
    system = build_system(grid=5000, charge_power=True)
    charging(system, 1000, 0)

    wall_clock(2026, 12, 31)
    await run(system, 1, advance=clock, seconds=60)
    old_year = system.coordinator.periods["week"]["key"]

    wall_clock(2027, 1, 1)
    await run(system, 1, advance=clock, seconds=60)

    assert system.coordinator.periods["week"]["key"] == old_year


async def test_each_period_starts_where_it_should(build_system, clock, wall_clock):
    """What Home Assistant is handed as `last_reset`, for all three."""
    system = build_system(grid=5000, charge_power=True)
    wall_clock(2026, 8, 20)          # a Thursday
    await run(system, 1, advance=clock, seconds=60)

    starts = {
        name: system.coordinator.period_started_at(name)
        for name in ("day", "week", "month")
    }

    assert starts["day"].day == 20
    assert starts["week"].day == 17          # the Monday of that week
    assert starts["month"].day == 1
    assert all(s.hour == 0 and s.tzinfo is not None for s in starts.values())


async def test_each_period_keeps_its_own_depth(build_system, clock, wall_clock):
    """Two months of days, a year of weeks, two years of months."""
    from custom_components.battery_management.const import PERIOD_HISTORY

    system = build_system(grid=5000, charge_power=True)
    charging(system, 1000, 0)
    for day in range(1, 29):
        for month in (1, 2, 3, 4, 5, 6):
            wall_clock(2027, month, day)
            await run(system, 1, advance=clock, seconds=60)

    for name in ("day", "week", "month"):
        assert len(system.coordinator.periods[name]["history"]) <= PERIOD_HISTORY[name]
    # and the days really did fill up, so the cap above is doing work
    assert len(system.coordinator.periods["day"]["history"]) == PERIOD_HISTORY["day"]


async def test_a_store_from_before_the_split_keeps_its_month(build_system, clock, wall_clock):
    """An install that has been counting must not lose what it had.

    The old shape was a flat month at the top level of the store. Read as the
    month, so upgrading carries the figure over instead of starting again.
    """
    system = build_system(grid=5000, charge_power=True)
    system.coordinator._store.data = {
        "month_key": "2026-08",
        "month_charged_wh": 4321.0,
        "month_charged_grid_wh": 1000.0,
        "month_history": {"2026-07": {"charged_kwh": 180.0, "grid_kwh": 12.0}},
    }

    await system.coordinator._async_restore()

    assert system.coordinator.month_key == "2026-08"
    assert system.coordinator.month_charged_wh == 4321.0
    assert system.coordinator.month_history["2026-07"]["charged_kwh"] == 180.0
    # the day and week simply start now - there is nothing on record for them,
    # and inventing one from the month would be a figure nobody measured
    assert system.coordinator.periods["day"]["key"] is None
    assert system.coordinator.periods["day"]["charged_wh"] == 0.0


# --- the published shape, pinned where this run can reach it ---------------
#
# CI caught a renamed attribute key that every local run had passed: the
# entity platforms need a real Home Assistant, which cannot be installed on
# Windows/3.14, so `test_month_sensors.py` skips here and anything asserted
# only there is checked on push and nowhere else. The *shape* now lives on the
# coordinator, which this run does reach.


async def test_the_period_attributes_keep_their_names(build_system, clock, wall_clock):
    """A renamed key is invisible to the arithmetic and breaks every card and
    template that reads it. Named explicitly, so renaming one is a decision."""
    system = build_system(grid=5000, charge_power=True)
    charging(system, 1000, 0)
    await run(system, 2, advance=clock, seconds=60)

    for name in ("day", "week", "month"):
        attributes = system.coordinator.period_attributes(name)
        assert set(attributes) == {"period", "key", "history"}, name
        assert attributes["period"] == name
        assert attributes["key"] == system.coordinator.periods[name]["key"]
        assert isinstance(attributes["history"], dict)


async def test_a_closed_period_carries_all_three_figures(build_system, clock, wall_clock):
    system = build_system(grid=2000, charge_power=True)
    charging(system, 1500, 500)
    await run(system, 60, advance=clock, seconds=60)
    wall_clock(2026, 9, 1)
    await run(system, 1, advance=clock, seconds=60)

    closed = system.coordinator.period_attributes("month")["history"]["2026-08"]

    assert set(closed) == {"charged_kwh", "grid_kwh", "solar_kwh"}


async def test_the_sun_share_is_the_remainder(build_system, clock, wall_clock):
    """Published rather than left to be subtracted - the subtraction is the one
    thing a reader gets backwards."""
    system = build_system(grid=600, charge_power=True)
    charging(system, 1000, 0)
    await run(system, 60, advance=clock, seconds=60)

    c = system.coordinator
    for name in ("day", "week", "month"):
        assert c.period_solar_kwh(name) == pytest.approx(
            c.period_charged_kwh(name) - c.period_charged_kwh(name, grid=True), abs=0.002
        )
    # and it really is a split of something, not two zeroes agreeing
    assert c.period_solar_kwh("day") > 0
    assert c.period_charged_kwh("day", grid=True) > 0

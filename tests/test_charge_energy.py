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

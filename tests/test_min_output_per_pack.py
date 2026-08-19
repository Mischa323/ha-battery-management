"""A pack is either off, or working properly. Never something in between.

Reported from the primary site: the Anker integration raising "the input power
of 63 W is below the optimal operating range, control accuracy may deviate",
with `min_output` configured at 100.

Three mechanisms have to agree for that never to happen, and each covers a case
the others cannot:

* `_distribute` drops the lowest-weight pack and re-splits, so a demand that
  would leave both packs with too little goes to one pack instead.
* the hysteresis latch turns everything off below the floor and back on above
  it, so a small demand is not served at all rather than served badly.
* between those sits the band the latch deliberately leaves engaged - and that
  is where the 63 W came from, so the *demand* is raised to the floor there
  instead of passed through.

None of this raises when it goes wrong. It writes a number to a pack and the
pack complains hours later, so the invariant is asserted directly: across a
sweep of loads and directions, no pack is ever told to do something between
nothing and the floor.
"""
from __future__ import annotations

import pytest

from custom_components.battery_management.coordinator import BatteryCoordinator

FLOOR = 100


def written(system) -> dict[str, float]:
    return system.hass.services.targets_set()


def offending(system) -> dict[str, float]:
    """Targets that are neither off nor at least the floor."""
    return {e: v for e, v in written(system).items() if 0 < abs(v) < FLOOR}


async def sweep(system, loads) -> None:
    """Run the real loop across a range of house loads."""
    for load in loads:
        system.hass.states.set("sensor.p1_meter_power", load)
        await system.coordinator._async_tick(None)


# every one of these lands the setpoint somewhere different, including inside
# the hysteresis band and either side of the release point
LOADS = [0, 30, 63, 88, 110, 125, 200, 60, 40, 20, 0, -50, -125, -300, -80, -20]


async def test_no_pack_is_ever_told_to_do_something_it_cannot(build_system):
    """The invariant, over a day's worth of small loads in both directions."""
    system = build_system(grid=0, min_output=FLOOR)

    await sweep(system, LOADS * 4)

    assert offending(system) == {}


async def held(build_system, setpoint: float, **kwargs):
    """Put the setpoint exactly where it needs to be, and keep it there.

    The band this is about is narrow - between three quarters of the floor and
    the floor - and the fixture's meter does not respond to the packs, so a
    sweep of house loads walks the setpoint straight past it. Parking the
    integrator and feeding it an error inside the deadband holds it still for
    one tick, which is all it takes to see what gets written.
    """
    system = build_system(grid=0, min_output=FLOOR, bias=0, **kwargs)
    system.coordinator.setpoint = setpoint
    # the latch is already engaged: this is the band it deliberately holds
    system.coordinator._above_min_output = True
    system.hass.states.set("sensor.p1_meter_power", 0)   # error 0, so sp stays
    await system.coordinator._async_tick(None)
    return system


@pytest.mark.parametrize("setpoint", [80, 88, 99, -80, -88, -99])
async def test_inside_the_band_the_pack_gets_the_whole_floor(build_system, setpoint):
    """Where the 63 W came from.

    The latch leaves the packs on between three quarters of the floor and the
    floor, on purpose - releasing there would make them flap on and off every
    tick. What must not happen is passing that setpoint straight through, which
    hands one pack a number its own firmware rejects as too small.
    """
    system = await held(build_system, setpoint)

    values = [v for v in written(system).values()]
    assert offending(system) == {}, written(system)
    assert max(abs(v) for v in values) == FLOOR, written(system)
    # and on one pack, not spread across both
    assert len([v for v in values if v]) == 1, written(system)


async def test_it_still_regulates_at_all(build_system):
    """A floor that silences the packs entirely would pass the test above.

    So: somewhere in that sweep the packs must actually have been working.
    """
    system = build_system(grid=0, min_output=FLOOR)
    seen: list[float] = []

    for load in LOADS * 4:
        system.hass.states.set("sensor.p1_meter_power", load)
        await system.coordinator._async_tick(None)
        seen.extend(v for v in written(system).values() if v)

    assert seen, "the packs were never asked to do anything at all"
    assert max(seen) >= FLOOR


async def test_one_pack_holds_what_two_would_split_too_thin(build_system):
    """The case the owner described, on the function that decides it.

    125 W over two packs is about 62 W each, and 62 W is not a smaller version
    of the job - it is a number neither pack can hold accurately.
    """
    alloc = BatteryCoordinator._distribute(
        125, {"a": 50.0, "b": 50.0}, {"a": 3500, "b": 3500}, FLOOR
    )

    assert sorted(alloc.values()) == [0, 125], alloc


async def test_a_demand_both_can_hold_is_still_shared(build_system):
    """The floor must not turn a two-pack system into a one-pack system."""
    alloc = BatteryCoordinator._distribute(
        2000, {"a": 50.0, "b": 50.0}, {"a": 3500, "b": 3500}, FLOOR
    )

    assert len([v for v in alloc.values() if v > 0]) == 2, alloc
    assert sum(alloc.values()) == pytest.approx(2000, abs=2), alloc


async def test_a_ceiling_that_undoes_the_floor_turns_the_pack_off(build_system):
    """The one `_distribute` cannot fix by re-splitting.

    Every pack capped below the floor - a nearly-full leg does this, and so
    does a pack that is almost full - leaves the last one standing holding
    60 W with nowhere to hand the remainder. Off is the honest answer: 60 W is
    a number the firmware refuses to regulate on, not a reduced version of the
    job.
    """
    system = await held(
        build_system,
        3000,                            # plenty wanted, almost none available
        target_max=60,          # every ceiling here is below the floor
    )

    assert offending(system) == {}, written(system)
    assert set(written(system).values()) == {0}, written(system)


async def test_a_floor_of_zero_is_a_real_opt_out(build_system):
    """No floor configured, no interference in either direction.

    Not rounded up to anything, not zeroed, and not consolidated: what the
    packs are told adds up to exactly what the loop asked for.
    """
    system = build_system(grid=0, min_output=0)

    await sweep(system, [63] * 4)

    assert sum(written(system).values()) == pytest.approx(
        abs(system.coordinator.setpoint), abs=2
    ), written(system)


async def test_a_floor_of_zero_does_not_consolidate_either(build_system):
    """The other half of the opt-out, on the split itself."""
    alloc = BatteryCoordinator._distribute(
        63, {"a": 50.0, "b": 50.0}, {"a": 3500, "b": 3500}, 0
    )

    assert len([v for v in alloc.values() if v > 0]) == 2, alloc

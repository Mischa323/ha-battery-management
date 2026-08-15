"""The main fuse as a bound on the control loop.

The loop regulates the household total; the packs are single-phase. A pack
charging at 3500 W on a leg already pulling 20 A takes that leg to 35 A while
the other two sit idle and the total looks perfectly reasonable. So the fuse is
a bound per leg, applied per unit, in the same place as every other bound - the
anti-windup clamp - so the integrator cannot build pressure against it either.

25 A x 230 V, less the 10 % margin, is 5175 W a leg.
"""
from __future__ import annotations

import pytest

from tests.conftest import GRID_SENSOR
from custom_components.battery_management.const import (
    CONF_PHASE_DETECT,
    CONF_PHASE_LIMIT_AMPS,
    CONF_PHASE_MARGIN,
    POLICY_PHASE_LIMIT,
)

LIMIT = 5175.0
# equal states of charge, so the split is even and the arithmetic is visible
EVEN = (("093", 50.0), ("052", 50.0))


async def test_nothing_changes_without_phase_sensors(build_system):
    """The whole feature is opt-in. No sensors, no behaviour."""
    system = build_system(grid=-6000, units=EVEN)

    await system.coordinator._async_tick(None)

    assert system.coordinator.phase_protection is False
    assert system.coordinator.setpoint == -6000
    assert sum(system.allocation().values()) == 6000


async def test_a_loaded_leg_caps_the_pack_sitting_on_it(build_system):
    """L1 is already pulling 4500 W: 675 W of fuse left, and no more."""
    system = build_system(
        grid=-6000, units=EVEN, phases=(4500, 200, 200), unit_phase=(1, 2)
    )

    await system.coordinator._async_tick(None)

    alloc = system.allocation()
    assert alloc["Batterij 1"] == 675
    assert alloc["Batterij 2"] == 3500     # its own leg is quiet, so its rating


async def test_the_setpoint_is_clamped_so_the_integrator_cannot_wind_up(build_system):
    """Gotcha 3 in a new coat: a bound the setpoint may not cross."""
    system = build_system(
        grid=-6000, units=EVEN, phases=(4500, 200, 200), unit_phase=(1, 2)
    )

    for _ in range(5):
        await system.coordinator._async_tick(None)
        system.settle_phases(4500, 200, 200)   # the meter catches up with us

    # 675 on L1 + 3500 on L2, and it stays there however long the export lasts
    assert system.coordinator.setpoint == pytest.approx(-4175)
    assert system.allocation() == {"Batterij 1": 675, "Batterij 2": 3500}


async def test_it_says_the_fuse_is_what_is_stopping_it(build_system):
    system = build_system(
        grid=-6000, units=EVEN, phases=(4500, 200, 200), unit_phase=(1, 2)
    )

    await system.coordinator._async_tick(None)

    assert system.coordinator.active_policy == POLICY_PHASE_LIMIT


async def test_a_quiet_house_never_notices_it(build_system):
    """It only bites where it was going to matter; otherwise it is invisible."""
    system = build_system(
        grid=-6000, units=EVEN, phases=(300, 300, 300), unit_phase=(1, 2)
    )

    await system.coordinator._async_tick(None)

    assert sum(system.allocation().values()) == 6000
    assert system.coordinator.active_policy != POLICY_PHASE_LIMIT


async def test_a_leg_full_of_sun_limits_discharging_too(build_system):
    """A fuse carries net current: 5 kW of export is as close to it as import."""
    system = build_system(
        grid=2000, units=EVEN, phases=(-5000, 200, 200), unit_phase=(1, 2)
    )

    await system.coordinator._async_tick(None)

    assert system.allocation()["Batterij 1"] == 175   # -5000 + 175 = -5175


async def test_both_packs_on_one_leg_share_its_room(build_system):
    system = build_system(
        grid=-6000, units=EVEN, phases=(3000, 200, 200), unit_phase=(1, 1)
    )

    await system.coordinator._async_tick(None)

    # 2175 W of room on L1, split down the middle
    assert system.allocation() == {"Batterij 1": 1088, "Batterij 2": 1088}


async def test_an_unplaced_pack_is_treated_as_being_on_the_worst_leg(build_system):
    """Guessing which leg would be worse than being cautious about it.

    Probing is off here, which is the real shape of this case: someone who
    switched detection off and then typed in only one of the two legs.
    """
    system = build_system(
        grid=-6000,
        units=EVEN,
        phases=(4500, 200, 200),
        unit_phase=(0, 2),
        **{CONF_PHASE_DETECT: False},
    )

    await system.coordinator._async_tick(None)

    # L2 is placed and claims 3500 of its 4975; L1 has 675 left, which is the
    # least of the two, so that is all the unplaced pack may have
    assert system.allocation()["Batterij 1"] == 675


async def test_unreadable_phase_sensors_hold_the_packs(build_system):
    """Configured but blind. Falling back to "no limit" would disarm the guard
    at exactly the moment the meter is misbehaving."""
    system = build_system(
        grid=-6000, units=EVEN, phases=(4500, 200, 200), **{CONF_PHASE_DETECT: False}
    )
    for leg in (1, 2, 3):
        system.hass.states.remove(system.phase(leg))

    await system.coordinator._async_tick(None)

    assert system.coordinator.setpoint == 0
    assert sum(system.allocation().values()) == 0


async def test_our_own_charging_is_not_counted_as_household_load(build_system):
    """Otherwise it throttles itself: the leg reads high *because* of us."""
    system = build_system(
        grid=-6000, units=EVEN, phases=(200, 200, 200), unit_phase=(1, 2)
    )
    await system.coordinator._async_tick(None)
    assert system.allocation()["Batterij 1"] == 3000

    # the meter catches up: L1 now reads its own 200 W plus our 3000 W
    system.set_phases(3200, 200, 200)
    system.hass.services.clear()
    system.hass.states.set(GRID_SENSOR, 0)      # and the export is now absorbed
    await system.coordinator._async_tick(None)

    # the house is still only doing 200 W, so nothing is limited
    assert system.coordinator.phase_report()["phases"][1]["without_us_w"] == 200
    assert system.allocation()["Batterij 1"] == 3000


async def test_fast_charge_respects_the_fuse_as_well(build_system):
    """The one place that commands full rating outright, so the likeliest
    single thing to drop a leg."""
    system = build_system(
        grid=0, units=EVEN, phases=(4500, 200, 200), unit_phase=(1, 2)
    )
    system.coordinator.fast_charge = True

    await system.coordinator._async_tick(None)

    assert system.allocation() == {"Batterij 1": 675, "Batterij 2": 3500}
    assert system.coordinator.active_policy == POLICY_PHASE_LIMIT


async def test_the_margin_and_the_fuse_size_are_settings(build_system):
    system = build_system(
        grid=-9000,
        units=EVEN,
        phases=(4500, 200, 200),
        unit_phase=(1, 2),
        **{CONF_PHASE_LIMIT_AMPS: 35, CONF_PHASE_MARGIN: 0},
    )

    await system.coordinator._async_tick(None)

    # 35 A x 230 V = 8050 W, all of it usable, so 3550 W of room on L1 - the
    # pack's own rating is what stops it now, not the fuse
    assert system.allocation()["Batterij 1"] == 3500
    assert system.coordinator.active_policy != POLICY_PHASE_LIMIT


async def test_the_headroom_sensor_reports_the_tightest_leg(build_system):
    system = build_system(
        grid=0, units=EVEN, phases=(4600, 200, 200), unit_phase=(1, 2)
    )

    await system.coordinator._async_tick(None)

    # (5175 - 4600) / 230 = 2.5 A left on L1, the worst of the three
    assert system.coordinator.fuse_headroom_amps() == 2.5


async def test_the_headroom_sensor_is_absent_when_unconfigured(build_system):
    system = build_system(grid=0, units=EVEN)

    assert system.coordinator.fuse_headroom_amps() is None


# -- what the headroom sensor is actually saying -------------------------------


async def test_the_headroom_is_the_tightest_leg_not_a_total(build_system):
    """Three legs, one number: the one that would trip first."""
    system = build_system(
        grid=0, units=EVEN, phases=(4600, 1000, 200), unit_phase=(1, 2)
    )

    await system.coordinator._async_tick(None)
    report = system.coordinator.phase_report()

    assert system.coordinator.fuse_headroom_amps() == 2.5   # (5175-4600)/230
    assert report["tightest_phase"] == 1
    assert report["phases"][2]["headroom_amps"] > 2.5


async def test_the_headline_and_the_per_leg_amps_measure_the_same_thing(build_system):
    """They used to disagree: one counted our own packs, the other did not."""
    system = build_system(
        grid=-3000, units=EVEN, phases=(2000, 500, 500), unit_phase=(1, 2)
    )

    await system.coordinator._async_tick(None)
    system.settle_phases(2000, 500, 500)
    report = system.coordinator.phase_report()

    tightest = report["tightest_phase"]
    assert report["phases"][tightest]["headroom_amps"] == (
        system.coordinator.fuse_headroom_amps()
    )


async def test_it_reports_what_the_margin_leaves_usable(build_system):
    """25 A on the fuse, 10 % kept back, so 22.5 A is ours to spend."""
    system = build_system(grid=0, units=EVEN, phases=(500, 500, 500), unit_phase=(1, 2))

    assert system.coordinator.phase_report()["usable_amps"] == 22.5

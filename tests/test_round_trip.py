"""The round trip as measured, and the house's weekend.

Asked for on 1 November, from the list of what other systems do. The 12 % lost
through the packs was a datasheet figure, and the nights had measured 78-98 %
one way depending on the load - so each day now measures what went into the
packs, what came out of their AC outputs and how their charge moved, and the
buying, the selling and the forecast use the median of the last two weeks.

And Saturday is not Tuesday: the house's draw per hour is learned for the
weekend on its own.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from custom_components.battery_management import coordinator as coordinator_module
from custom_components.battery_management.const import CONF_FULL_CHARGE_MINUTES
from custom_components.battery_management.trading import ROUND_TRIP

DAY = "2026-11-01"
NEXT = "2026-11-02"


@pytest.fixture
def packs(build_system):
    """Two 14 kWh packs at 50 %, each with its AC output sensor."""
    system = build_system(units=(("093", 50.0), ("052", 50.0)), **{CONF_FULL_CHARGE_MINUTES: 240})
    for unit in system.coordinator._units:
        unit.power_sensor = f"sensor.{unit.name}_ac_output"
        system.hass.states.set(unit.power_sensor, 0)
    return system


def set_flow(system, *, target, output=0.0, soc=None):
    co = system.coordinator
    for index, unit in enumerate(co._units):
        co.unit_status[unit.name].online = True
        co.unit_status[unit.name].target = target
        system.hass.states.set(unit.power_sensor, output)
        if soc is not None:
            system.hass.states.set(system.soc(index), soc)


def a_day(system, *, charged_h=1.0, delivered_w=1000.0, end_soc=65.0):
    """7 kW in for `charged_h`, `delivered_w` per pack out for an hour."""
    co = system.coordinator
    co._track_round_trip(DAY, 0.0)
    set_flow(system, target=-3500)
    co._track_round_trip(DAY, 3600.0 * charged_h)
    set_flow(system, target=delivered_w, output=delivered_w)
    co._track_round_trip(DAY, 3600.0)
    set_flow(system, target=0, soc=end_soc)
    co._track_round_trip(NEXT, 60.0)


def test_a_day_is_measured(packs):
    """7 kWh in, 2 out, 4.2 left in the packs - worth 4.2 x 0.94 out:
    (2 + 3.94) / 7 = 0.85."""
    a_day(packs)
    record = packs.coordinator.round_trip_history[DAY]

    assert record["charged_kwh"] == pytest.approx(7.0)
    assert record["delivered_kwh"] == pytest.approx(2.0)
    assert record["stored_kwh"] == pytest.approx(4.2)
    assert record["round_trip"] == pytest.approx((2.0 + 4.2 * ROUND_TRIP ** 0.5) / 7.0, abs=0.001)


def test_too_little_through_the_packs_measures_nothing(packs):
    """3.5 kWh in: a percent of SoC either way is too much of the answer."""
    a_day(packs, charged_h=0.5, end_soc=57.0)

    assert packs.coordinator.round_trip_history == {}


def test_without_the_packs_own_output_nothing(packs):
    for unit in packs.coordinator._units:
        unit.power_sensor = None
    a_day(packs)

    assert packs.coordinator.round_trip_history == {}


def test_what_a_full_pack_is_told_is_not_counted(packs):
    """A pack at its limit takes nothing, whatever it is told."""
    co = packs.coordinator
    co._track_round_trip(DAY, 0.0)
    set_flow(packs, target=-3500, soc=100.0)
    co._track_round_trip(DAY, 3600.0)

    assert co._rt_day["charged_wh"] == 0.0


def test_a_day_that_makes_no_sense_is_left_out(packs):
    """More out than in: a sensor fault, not a pack."""
    a_day(packs, delivered_w=5000.0, end_soc=65.0)

    assert packs.coordinator.round_trip_history == {}


def test_until_three_days_the_datasheet_figure(packs):
    co = packs.coordinator
    co.round_trip_history = {"2026-10-30": {"round_trip": 0.80}, "2026-10-31": {"round_trip": 0.80}}

    assert co.round_trip() == ROUND_TRIP


def test_then_the_median_of_the_last_two_weeks(packs):
    co = packs.coordinator
    co.round_trip_history = {
        f"2026-10-{d:02d}": {"round_trip": value}
        for d, value in zip(range(1, 21), [0.95] * 6 + [0.80, 0.82, 0.84] * 4 + [0.81, 0.83])
    }

    # only the last 14: the six at 0.95 are too old
    assert co.round_trip() == pytest.approx(0.82)


def test_buying_counts_the_measured_loss(packs):
    co = packs.coordinator
    co._battery_price = 8500.0
    co._battery_cycles = 10000
    co.round_trip_history = {f"2026-10-{d}": {"round_trip": 0.80} for d in (29, 30, 31)}

    assert co._buy_cost()(0.30) == pytest.approx(0.30 / 0.80 + co.battery_wear())


async def test_the_history_and_today_are_restored(packs, build_system):
    a_day(packs)
    packs.coordinator._track_round_trip(NEXT, 60.0)
    stored = packs.coordinator._state_to_save()

    fresh = build_system(units=(("093", 50.0), ("052", 50.0)), **{CONF_FULL_CHARGE_MINUTES: 240})
    fresh.coordinator._store.data = stored
    await fresh.coordinator._async_restore()

    assert fresh.coordinator.round_trip_history == packs.coordinator.round_trip_history
    assert fresh.coordinator._rt_day["day"] == NEXT


# -- the weekend ----------------------------------------------------------------

SATURDAY = datetime(2026, 10, 31, 19, 0, tzinfo=timezone.utc)
TUESDAY = datetime(2026, 10, 27, 19, 0, tzinfo=timezone.utc)


class Slot:
    def __init__(self, start):
        self.start = start


def test_a_saturday_hour_draws_what_saturdays_draw(build_system):
    co = build_system().coordinator
    co.draw_profile = {19: 900.0}
    co.draw_profile_weekend = {19: 2400.0}

    assert co._draw_in(Slot(SATURDAY)) == 2400.0
    assert co._draw_in(Slot(TUESDAY)) == 900.0


def test_until_it_is_learned_the_weekday_stands_in(build_system):
    co = build_system().coordinator
    co.draw_profile = {19: 900.0}

    assert co._draw_in(Slot(SATURDAY)) == 900.0


def test_a_saturday_is_learned_into_the_weekend(build_system, monkeypatch):
    system = build_system(grid=1500)
    co = system.coordinator
    monkeypatch.setattr(coordinator_module.dt_util, "utcnow", lambda: SATURDAY)
    co._other_controller_power = lambda: 500.0

    co._track_house_draw(1500.0, 1000.0)
    co._track_house_draw(1500.0, 1060.0)

    assert co.draw_profile_weekend[19] == pytest.approx(2000.0)
    assert 19 not in co.draw_profile


async def test_the_weekend_survives_a_restart(build_system):
    system = build_system()
    system.coordinator.draw_profile_weekend = {19: 2400.0}
    stored = system.coordinator._state_to_save()

    fresh = build_system()
    fresh.coordinator._store.data = stored
    await fresh.coordinator._async_restore()

    assert fresh.coordinator.draw_profile_weekend == {19: 2400.0}

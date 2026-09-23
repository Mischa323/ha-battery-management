"""Buying only what the sun will not bring anyway.

The owner's insight, and it exposed a real fault: a whole-day forecast is right
at 02:00 and wrong at 17:00. At 17:00 "22 kWh expected today" is mostly already
in the house, yet the old check read it as "lots of sun coming, do not buy" -
exactly when topping up for the evening is the right move.

So the rule is a ceiling rather than a veto:

    buy up to 100 % - (sun still coming / usable capacity)

At 02:00 with more sun coming than the packs hold, that is zero: buy nothing.
At 17:00 with an hour of sun left, it is nearly 100 %: fill up.
"""
from __future__ import annotations

import pytest

from custom_components.battery_management.const import (
    CONF_FULL_CHARGE_MINUTES,
    CONF_SOLAR_FORECAST_SENSORS,
    CONF_SOLAR_PRODUCED_SENSOR,
)

WEST = "sensor.forecast_west"
SOUTH = "sensor.forecast_south"
NORTH = "sensor.forecast_north"
PRODUCED = "sensor.enphase_today"

# 2 x 3500 W for 120 minutes = 14 kWh, the size of the owner's packs
MEASURED = {CONF_FULL_CHARGE_MINUTES: 120}
THREE_PLANES = {CONF_SOLAR_FORECAST_SENSORS: [WEST, SOUTH, NORTH]}


def set_forecast(system, west, south, north, produced=None):
    system.hass.states.set(WEST, west)
    system.hass.states.set(SOUTH, south)
    system.hass.states.set(NORTH, north)
    if produced is not None:
        system.hass.states.set(PRODUCED, produced)


# -- several sensors ---------------------------------------------------------


def test_sums_every_roof_plane(build_system):
    """Forecast.Solar publishes one sensor per plane; the site has three."""
    system = build_system(**THREE_PLANES)
    set_forecast(system, 7.2, 3.6, 4.1)

    assert system.coordinator.solar_remaining() == pytest.approx(14.9)


def test_a_plane_that_is_unavailable_does_not_sink_the_rest(build_system):
    system = build_system(**THREE_PLANES)
    set_forecast(system, 7.2, "unavailable", 4.1)

    assert system.coordinator.solar_remaining() == pytest.approx(11.3)


def test_no_forecast_at_all_is_unknown_rather_than_zero(build_system):
    system = build_system(**THREE_PLANES)

    assert system.coordinator.solar_remaining() is None


def test_an_older_single_sensor_entry_still_works(build_system):
    system = build_system(solar_forecast_sensor=WEST)
    system.hass.states.set(WEST, 9.0)

    assert system.coordinator.solar_remaining() == pytest.approx(9.0)


def test_production_so_far_can_be_subtracted(build_system):
    """Turns a day total into what is actually still to come."""
    system = build_system(
        **THREE_PLANES, **{CONF_SOLAR_PRODUCED_SENSOR: PRODUCED}
    )
    set_forecast(system, 7.2, 3.6, 4.1, produced=12.0)

    assert system.coordinator.solar_remaining() == pytest.approx(2.9)


def test_producing_more_than_forecast_does_not_go_negative(build_system):
    """It happened on day one: 14.9 predicted, 22.46 actually produced."""
    system = build_system(
        **THREE_PLANES, **{CONF_SOLAR_PRODUCED_SENSOR: PRODUCED}
    )
    set_forecast(system, 7.2, 3.6, 4.1, produced=22.46)

    assert system.coordinator.solar_remaining() == pytest.approx(0.0)


# -- capacity, for free ------------------------------------------------------


def test_capacity_comes_from_the_measured_charge_time(build_system):
    system = build_system(**MEASURED)

    # 2 packs x 3500 W x 2 h = 14 kWh
    assert system.coordinator.usable_capacity_kwh() == pytest.approx(14.0)


def test_capacity_is_unknown_until_it_has_been_measured(build_system):
    assert build_system().coordinator.usable_capacity_kwh() is None


# -- the ceiling itself ------------------------------------------------------


def test_at_night_with_a_full_day_coming_it_buys_nothing(build_system):
    system = build_system(**MEASURED, **THREE_PLANES)
    set_forecast(system, 7.2, 3.6, 4.1)  # 14.9 kWh against 14 kWh of storage

    assert system.coordinator._solar_headroom_ceiling() == pytest.approx(0.0)


def test_late_afternoon_it_fills_up(build_system):
    """An hour of sun left: nothing to wait for."""
    system = build_system(**MEASURED, **THREE_PLANES)
    set_forecast(system, 0.7, 0.2, 0.1)  # 1 kWh of 14

    ceiling = system.coordinator._solar_headroom_ceiling()

    assert 92 < ceiling < 93


def test_midday_it_leaves_room_for_what_is_coming(build_system):
    system = build_system(**MEASURED, **THREE_PLANES)
    set_forecast(system, 3.5, 2.1, 1.4)  # 7 kWh of 14

    assert system.coordinator._solar_headroom_ceiling() == pytest.approx(50.0)


def test_without_a_measured_capacity_there_is_no_ceiling(build_system):
    """Falls back to the plain threshold rather than guessing at kWh."""
    system = build_system(**THREE_PLANES)
    set_forecast(system, 7.2, 3.6, 4.1)

    assert system.coordinator._solar_headroom_ceiling() is None


# -- checking the configuration ----------------------------------------------


def test_the_breakdown_shows_every_part(build_system):
    """"0 kWh remaining" is right at midnight and alarming at noon, and the
    figure alone does not say which sensor is at fault."""
    system = build_system(
        **THREE_PLANES, **{CONF_SOLAR_PRODUCED_SENSOR: PRODUCED}
    )
    set_forecast(system, 7.2, 3.6, 4.1, produced=12.0)

    parts = system.coordinator.solar_breakdown()

    assert parts["forecast_per_sensor"] == {WEST: 7.2, SOUTH: 3.6, NORTH: 4.1}
    assert parts["forecast_total_kwh"] == pytest.approx(14.9)
    assert parts["produced_today_sensor"] == PRODUCED
    assert parts["produced_today_kwh"] == 12.0
    assert parts["remaining_kwh"] == pytest.approx(2.9)


def test_a_sensor_that_is_not_reading_shows_up_as_none(build_system):
    """Names the culprit instead of quietly lowering the total."""
    system = build_system(**THREE_PLANES)
    set_forecast(system, 7.2, "unavailable", 4.1)

    parts = system.coordinator.solar_breakdown()

    assert parts["forecast_per_sensor"][SOUTH] is None
    assert parts["forecast_total_kwh"] == pytest.approx(11.3)


def test_the_breakdown_survives_nothing_being_configured(build_system):
    parts = build_system().coordinator.solar_breakdown()

    assert parts["forecast_per_sensor"] == {}
    assert parts["forecast_total_kwh"] is None
    assert parts["produced_today_kwh"] is None


# -- how much of the sun actually arrives ------------------------------------
#
# The ceiling above reserves room for the sun still to come. It assumed all of
# it reaches a pack. It does not: the house is first in the queue, and at this
# owner's site it takes the larger share. On 18 September the ceiling held
# buying back at 45 % because the forecast promised ~15 kWh more sun; 7.4 kWh
# arrived and 1.7 kWh of that reached the packs. They were flat by 08:00 the
# next morning, having skipped a five-hour cheap window at EUR 0.129.
#
# So the reservation is scaled by what recent days actually delivered.


def day(produced, charged, grid=0.0, room=None):
    """One closed day, as `_roll_periods` writes it.

    `room` is the part of `produced` that fell while a pack could take it, and
    defaults to all of it: these days are about the share, not about fullness.
    """
    return {
        "produced_kwh": produced,
        "produced_room_kwh": produced if room is None else room,
        "charged_kwh": charged,
        "grid_kwh": grid,
    }


def with_days(system, *days):
    """Give the coordinator a run of closed days, oldest first."""
    history = system.coordinator.periods["day"]["history"]
    history.clear()
    for index, figures in enumerate(days):
        history[f"2026-09-{index + 1:02d}"] = figures
    return system.coordinator


def test_without_enough_history_it_reserves_for_the_whole_forecast(build_system):
    """Which is exactly how it behaved before any of this existed."""
    system = build_system(**THREE_PLANES, **MEASURED)
    set_forecast(system, 3.5, 3.5, 0)
    with_days(system, day(10, 2), day(10, 2))

    assert system.coordinator.solar_capture() == (None, 0)
    assert system.coordinator._solar_headroom_ceiling() == pytest.approx(50.0)


def test_three_days_are_enough_to_start_scaling(build_system):
    system = build_system(**THREE_PLANES, **MEASURED)
    set_forecast(system, 3.5, 3.5, 0)
    with_days(system, day(10, 2), day(10, 2), day(10, 2))

    share, days = system.coordinator.solar_capture()

    assert (share, days) == (pytest.approx(0.2), 3)
    # 7 kWh forecast, but only a fifth of it has been arriving: reserve 1.4 kWh
    # of the 14 kWh packs rather than all 7
    assert system.coordinator._solar_headroom_ceiling() == pytest.approx(90.0)


def test_the_bought_half_is_not_counted_as_sun(build_system):
    """`charged_kwh` includes grid buying; only the remainder is the sun's."""
    system = build_system(**THREE_PLANES, **MEASURED)
    with_days(system, *[day(10, 8, grid=6)] * 3)

    assert system.coordinator.solar_capture()[0] == pytest.approx(0.2)


def test_one_odd_day_cannot_move_it(build_system):
    """A median, because a fortnight contains the odd day away from home."""
    system = build_system(**THREE_PLANES, **MEASURED)
    with_days(system, day(10, 2), day(10, 2), day(10, 9), day(10, 2), day(10, 2))

    # the mean would be 0.34, dragged up by the one strange day
    assert system.coordinator.solar_capture()[0] == pytest.approx(0.2)


def test_a_day_with_no_sun_says_nothing_and_is_skipped(build_system):
    system = build_system(**THREE_PLANES, **MEASURED)
    with_days(system, day(0.4, 0), day(10, 2), day(10, 2), day(10, 2))

    assert system.coordinator.solar_capture() == (pytest.approx(0.2), 3)


def test_a_day_that_measured_no_charging_is_not_a_day_of_zero_capture(build_system):
    """The charge-power sensors are optional. A day without them means "not
    measured here", and reading it as "the sun delivered nothing" would drive
    the ceiling to 100 % on the strength of missing data."""
    system = build_system(**THREE_PLANES, **MEASURED)
    with_days(system, day(10, 0), day(10, 0), day(10, 2), day(10, 2), day(10, 2))

    assert system.coordinator.solar_capture() == (pytest.approx(0.2), 3)


def test_it_looks_back_a_fortnight_and_no_further(build_system):
    """Older days are still stored - 62 of them - but a share measured in July
    should not steer the packs in October."""
    system = build_system(**THREE_PLANES, **MEASURED)
    # twenty days: the oldest six would halve the answer if they counted
    with_days(system, *([day(10, 9)] * 6 + [day(10, 2)] * 14))

    assert system.coordinator.solar_capture() == (pytest.approx(0.2), 14)


def test_the_share_never_reserves_nothing_at_all(build_system):
    """A fortnight of cloud would otherwise say "buy to full" on the morning of
    a blazing day."""
    system = build_system(**THREE_PLANES, **MEASURED)
    set_forecast(system, 7.0, 7.0, 0)
    with_days(system, *[day(10, 0.01)] * 3)

    assert system.coordinator.solar_capture()[0] == pytest.approx(0.05)
    assert system.coordinator._solar_headroom_ceiling() == pytest.approx(95.0)


def test_the_measurement_can_only_raise_the_ceiling(build_system):
    """The safety property worth stating out loud: the share never exceeds 1,
    so this can talk the packs into buying more but never into buying less. It
    cannot invent a new way to be caught empty."""
    system = build_system(**THREE_PLANES, **MEASURED)
    set_forecast(system, 3.5, 3.5, 0)
    bare = 50.0  # 7 kWh of 14 kWh capacity

    for charged in (2, 5, 9, 10, 14):
        with_days(system, *[day(10, charged)] * 3)
        assert system.coordinator._solar_headroom_ceiling() >= bare


def test_a_day_that_captured_everything_leaves_the_ceiling_where_it_was(build_system):
    system = build_system(**THREE_PLANES, **MEASURED)
    set_forecast(system, 3.5, 3.5, 0)
    with_days(system, *[day(10, 12)] * 3)

    assert system.coordinator.solar_capture()[0] == pytest.approx(1.0)
    assert system.coordinator._solar_headroom_ceiling() == pytest.approx(50.0)


def test_the_ceiling_sensor_says_what_it_is_reserving_for(build_system):
    """The bare number reads as a setting; these two say it is a forecast being
    trusted, and how much measurement is behind that trust."""
    system = build_system(**THREE_PLANES, **MEASURED)
    set_forecast(system, 3.5, 3.5, 0)
    with_days(system, *[day(10, 2)] * 3)

    report = system.coordinator.diagnostics()["state"]

    assert report["solar_capture_share"] == pytest.approx(0.2)
    assert report["solar_capture_days"] == 3

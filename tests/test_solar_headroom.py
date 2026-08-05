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

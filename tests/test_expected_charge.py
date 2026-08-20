"""How much is still meant to come off the meter, and how much off the roof.

Asked for by the owner on 2026-08-20: "hoeveel wil hij laten opladen via de zon
en hoeveel via het net". Both are read back out of the buy ceiling rather than
invented, because the ceiling *is* that decision - `100 % - remaining sun /
capacity` says "fill this much from the grid and leave that much for the sun".

The failure mode here is not an exception, it is a plausible kilowatt-hour
figure on a dashboard. So each way of getting it wrong is pinned separately.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from custom_components.battery_management import coordinator as coordinator_module
from custom_components.battery_management.const import (
    CONF_FULL_CHARGE_MINUTES,
    CONF_SOLAR_FORECAST_SENSORS,
)

FORECAST = "sensor.forecast_remaining"
NOW = datetime(2026, 8, 20, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def site(build_system, monkeypatch):
    """Two 3500 W packs and a measured 120 min to full: 7 kWh each, 14 total."""

    def _build(*, soc=(50.0, 50.0), sun=7.0, minutes=120, forecast=True, **options):
        monkeypatch.setattr(coordinator_module.dt_util, "utcnow", lambda: NOW)
        extra = {CONF_SOLAR_FORECAST_SENSORS: [FORECAST]} if forecast else {}
        system = build_system(
            grid=300,
            units=(("093", soc[0]), ("052", soc[1])),
            **{CONF_FULL_CHARGE_MINUTES: minutes, **extra, **options},
        )
        if forecast:
            system.hass.states.set(FORECAST, sun)
        return system

    return _build


def test_the_two_halves_are_the_room_below_and_above_the_ceiling(site):
    """7 kWh of sun against 14 kWh of packs puts the ceiling at 50 %.

    The packs are at 50 %, so there is nothing left to buy and the whole upper
    half - 7 kWh - is the space being kept for the sun.
    """
    expected = site(soc=(50.0, 50.0), sun=7.0).coordinator.expected_charge()

    assert expected["known"] is True
    assert expected["ceiling"] == pytest.approx(50.0)
    assert expected["grid_kwh"] == pytest.approx(0.0)
    assert expected["solar_kwh"] == pytest.approx(7.0)


def test_packs_below_the_ceiling_leave_something_to_buy(site):
    """Ceiling still 50 %, packs at 20 %: 30 points of 14 kWh is 4.2 kWh."""
    expected = site(soc=(20.0, 20.0), sun=7.0).coordinator.expected_charge()

    assert expected["grid_kwh"] == pytest.approx(4.2)
    assert expected["solar_kwh"] == pytest.approx(7.0)


def test_the_sun_figure_is_capped_by_the_sun_actually_coming(site):
    """Reserving space means nothing if the sun is not going to fill it.

    A late afternoon: only 1 kWh left to come, so the ceiling climbs to ~93 %
    and the room above it is about 1 kWh - not the 10 kWh of empty pack.
    """
    expected = site(soc=(20.0, 20.0), sun=1.0).coordinator.expected_charge()

    assert expected["ceiling"] == pytest.approx(92.9, abs=0.2)
    assert expected["solar_kwh"] == pytest.approx(1.0, abs=0.05)
    assert expected["solar_kwh"] <= expected["room_for_solar_kwh"]


def test_the_room_and_the_sun_agree_unless_the_ceiling_is_bounded(site):
    """Left alone they are the same number, and that is not a coincidence.

    The ceiling is *defined* as `100 % - remaining sun / capacity`, so the room
    above it is the forecast sun by construction. They only come apart when the
    owner's own bounds override the calculation - which is exactly when the two
    figures need to be shown side by side, because the sun is then no longer
    the reason the space is that size.
    """
    plain = site(soc=(0.0, 0.0), sun=2.0).coordinator.expected_charge()
    assert plain["room_for_solar_kwh"] == pytest.approx(plain["solar_kwh"])

    bounded = site(soc=(0.0, 0.0), sun=2.0)
    bounded.coordinator.buy_ceiling_max = 60.0
    capped = bounded.coordinator.expected_charge()

    # buying now stops at 60 %, so 40 % of 14 kWh stands empty - but only
    # 2 kWh of sun is coming to fill it
    assert capped["ceiling"] == pytest.approx(60.0)
    assert capped["room_for_solar_kwh"] == pytest.approx(5.6)
    assert capped["solar_kwh"] == pytest.approx(2.0)
    assert capped["solar_remaining_kwh"] == pytest.approx(2.0)


def test_it_is_per_pack_not_against_a_mean(site):
    """One full pack and one empty is not two half-full ones.

    With the ceiling at 50 %, the full pack has nothing to buy and no room
    below it - only the empty one does. A mean of 50 % would report nothing to
    buy at all, which is wrong by the whole of one pack.
    """
    expected = site(soc=(100.0, 0.0), sun=7.0).coordinator.expected_charge()

    # the empty pack alone: 50 points of its own 7 kWh
    assert expected["grid_kwh"] == pytest.approx(3.5)


def test_a_pack_above_the_ceiling_is_never_asked_to_give_back(site):
    """Buying is a floor to reach, not a level to hold - a fuller pack simply
    contributes nothing to the figure rather than a negative."""
    expected = site(soc=(90.0, 90.0), sun=7.0).coordinator.expected_charge()

    assert expected["grid_kwh"] == pytest.approx(0.0)
    assert expected["grid_kwh"] >= 0


def test_without_a_measured_capacity_it_refuses_to_say(site):
    """The same rule as minutes-to-full: a figure built on a guessed capacity
    is worse than admitting the split is not knowable."""
    expected = site(minutes=0).coordinator.expected_charge()

    assert expected["known"] is False
    assert expected["reason"] == "no_capacity"
    assert "grid_kwh" not in expected


def test_without_a_forecast_it_refuses_to_say(site):
    expected = site(forecast=False).coordinator.expected_charge()

    assert expected["known"] is False
    assert expected["reason"] == "no_forecast"


def test_nothing_reachable_is_not_a_fact_about_the_packs(site):
    """Both packs offline: zero kWh to buy would read as "we are full"."""
    expected = site(soc=(None, None)).coordinator.expected_charge()

    assert expected["known"] is False
    assert expected["reason"] == "no_units"


def test_the_plan_carries_it(site):
    plan = site(soc=(20.0, 20.0), sun=7.0).coordinator.plan()

    assert plan["expected"]["grid_kwh"] == pytest.approx(4.2)
    assert plan["expected"]["solar_kwh"] == pytest.approx(7.0)

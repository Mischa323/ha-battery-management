"""The buy ceiling's hand-set bounds, and the plan a dashboard renders.

The computed ceiling is only as good as the solar forecast behind it, and the
primary site's under-reads by half. So both ends have to be reachable without
waiting for a code change - and you have to be able to see what it intends
before you go turning knobs.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from custom_components.battery_management import coordinator as coordinator_module
from custom_components.battery_management.const import (
    CONF_CHEAP_HOURS,
    CONF_EXPENSIVE_HOURS,
    CONF_FULL_CHARGE_MINUTES,
    CONF_PRICE_SENSOR,
    CONF_SOLAR_FORECAST_SENSORS,
    MODE_DYNAMIC,
    POLICY_DYNAMIC_CHARGE,
)

PRICES = "sensor.energy_prices"
FORECAST = "sensor.forecast_total"
NOW = datetime(2026, 8, 5, 12, 0, tzinfo=timezone.utc)


def price_attributes(cheap_hour: int, dear_hour: int) -> dict:
    midnight = NOW.replace(hour=0, minute=0, second=0, microsecond=0)
    slots = []
    for i in range(48):
        start = midnight + timedelta(hours=i)
        price = 0.20
        if start.hour == cheap_hour and start.day == NOW.day:
            price = 0.02
        elif start.hour == dear_hour and start.day == NOW.day:
            price = 0.60
        slots.append(
            {
                "start": start.isoformat(),
                "end": (start + timedelta(hours=1)).isoformat(),
                "value": price,
            }
        )
    return {"raw_today": slots}


@pytest.fixture
def planned(build_system, monkeypatch):
    def _build(*, remaining=1.0, soc=(20.0, 20.0), **options):
        monkeypatch.setattr(coordinator_module.dt_util, "utcnow", lambda: NOW)
        system = build_system(
            grid=300,
            units=(("093", soc[0]), ("052", soc[1])),
            **{
                CONF_PRICE_SENSOR: PRICES,
                CONF_SOLAR_FORECAST_SENSORS: [FORECAST],
                CONF_FULL_CHARGE_MINUTES: 120,  # 2 x 3500 W x 2 h = 14 kWh
                CONF_CHEAP_HOURS: 2,
                CONF_EXPENSIVE_HOURS: 3,
                **options,
            },
        )
        system.hass.states.set(PRICES, 0.02, price_attributes(12, 18))
        system.hass.states.set(FORECAST, remaining)
        system.coordinator.mode = MODE_DYNAMIC
        return system

    return _build


# -- the hand-set bounds -----------------------------------------------------


def test_wide_open_by_default(build_system):
    coordinator = build_system().coordinator

    assert coordinator.buy_ceiling_min == 0
    assert coordinator.buy_ceiling_max == 100


def test_a_maximum_caps_an_over_optimistic_calculation(planned):
    """The site's forecast under-reads by half, so the ceiling runs too high."""
    system = planned(remaining=1.0)
    assert round(system.coordinator.charge_ceiling()) == 93

    system.coordinator.buy_ceiling_max = 70.0

    assert system.coordinator.charge_ceiling() == 70.0


def test_a_minimum_overrides_a_gloomy_forecast(planned):
    """Lots of sun expected would say "buy nothing"; the floor says otherwise."""
    system = planned(remaining=20.0)
    assert system.coordinator.charge_ceiling() == 0.0

    system.coordinator.buy_ceiling_min = 30.0

    assert system.coordinator.charge_ceiling() == 30.0


async def test_the_bounds_take_effect_on_the_next_tick(planned):
    system = planned(remaining=20.0, soc=(20.0, 20.0))
    await system.coordinator._async_tick(None)
    assert system.coordinator.active_policy != POLICY_DYNAMIC_CHARGE

    await system.coordinator.async_set_buy_ceiling(low=50)
    await system.coordinator._async_tick(None)

    assert system.coordinator.active_policy == POLICY_DYNAMIC_CHARGE


async def test_the_bounds_are_clamped_to_a_percentage(planned):
    system = planned()

    await system.coordinator.async_set_buy_ceiling(low=-10, high=500)

    assert system.coordinator.buy_ceiling_min == 0
    assert system.coordinator.buy_ceiling_max == 100


def test_a_floor_above_the_ceiling_does_not_win(planned):
    """Nonsense input must not quietly invert the meaning of the two."""
    system = planned(remaining=1.0)
    system.coordinator.buy_ceiling_min = 90.0
    system.coordinator.buy_ceiling_max = 40.0

    assert system.coordinator.charge_ceiling() == 40.0


async def test_the_bounds_survive_a_restart(planned):
    system = planned()
    await system.coordinator.async_set_buy_ceiling(low=25, high=75)
    stored = system.coordinator._store.data

    system.coordinator.buy_ceiling_min = 0.0
    system.coordinator.buy_ceiling_max = 100.0
    system.coordinator._store.data = stored
    await system.coordinator._async_restore()

    assert system.coordinator.buy_ceiling_min == 25
    assert system.coordinator.buy_ceiling_max == 75


# -- the plan ----------------------------------------------------------------


def test_the_plan_names_the_hours_it_picked(planned):
    system = planned()

    plan = system.coordinator.plan()

    assert plan["has_prices"] is True
    assert len(plan["cheap_hours"]) == 2
    assert len(plan["dear_hours"]) == 3
    # the 0.02 hour is in the cheap set, the 0.60 hour in the dear set
    assert any(h["price"] == 0.02 for h in plan["cheap_hours"])
    assert any(h["price"] == 0.60 for h in plan["dear_hours"])


def test_the_plan_carries_what_the_ceiling_was_computed_from(planned):
    system = planned(remaining=7.0)

    plan = system.coordinator.plan()

    assert plan["solar_remaining_kwh"] == 7.0
    assert plan["usable_capacity_kwh"] == pytest.approx(14.0)
    assert plan["charge_ceiling"] == pytest.approx(50.0)


def test_the_plan_says_so_when_there_are_no_prices(planned):
    system = planned()
    system.hass.states.set(PRICES, "unavailable")

    plan = system.coordinator.plan()

    assert plan["has_prices"] is False
    assert plan["cheap_hours"] == []
    assert plan["dear_hours"] == []


def test_the_plan_works_without_any_of_it_configured(build_system):
    """A dashboard card must not break on a site that configured nothing."""
    plan = build_system().coordinator.plan()

    assert plan["has_prices"] is False
    assert plan["solar_remaining_kwh"] is None
    assert plan["charge_ceiling"] is None

"""On a day without sun, the sale waits for the evening peak.

26 November: the night bought for the sale, and at 10:15 it sold at EUR 0.52
while the evening peak was at 0.59. Selling looked ahead to the next midday
only, because the sun refills the packs after it - but that day 0.2 kWh of
sun was to reach them. "Hij kan toch beter in de piek verkopen?" Without the
sun the morning's charge is still there in the evening, so the window runs
on to the next midday and the evening's quarters compete for it.

Prices: 0.52 all day, the 16:00-19:00 peak at 0.59. The night's purchase cost
0.36. The packs are at 98 % over a 50 % line, the house draws 300 W.
"""
from __future__ import annotations

from datetime import timedelta

import pytest

from custom_components.battery_management import coordinator as coordinator_module
from custom_components.battery_management.const import (
    CONF_CHEAP_HOURS,
    CONF_EXPENSIVE_HOURS,
    CONF_SOLAR_FORECAST_SENSORS,
)

from .test_trade_fill import DAY, prices
from .test_trade_sell import PRICE_SENSOR, trading  # noqa: F401

MORNING = DAY.replace(hour=10, minute=15)
FORECAST = "sensor.solar_forecast_today"
WINTER = dict(cheap=(), ordinary=0.52, peak=(16, 17, 18), peak_at=0.59)


@pytest.fixture
def morning(trading, monkeypatch):
    def _build(*, sun: float | None):
        options = {CONF_SOLAR_FORECAST_SENSORS: [FORECAST]} if sun is not None else {}
        system = trading(soc=(98.0, 98.0), **{CONF_CHEAP_HOURS: 5, CONF_EXPENSIVE_HOURS: 4}, **options)
        system.hass.states.set(PRICE_SENSOR, 0.52, prices(**WINTER))
        system.coordinator.sell_floor = 50
        if sun is not None:
            system.hass.states.set(FORECAST, sun)
        monkeypatch.setattr(coordinator_module.dt_util, "utcnow", lambda: MORNING)
        co = system.coordinator
        co._trade_margin = 0.01
        co.draw_profile = {hour: 300.0 for hour in range(24)}
        # what the night bought for the sale, kept as the window then stood
        until = co._sell_window_end(MORNING)
        co._fill_lot = {"until": until.isoformat(), "kwh": 13.0, "eur": 13.0 * 0.36, "sold": 0.0}
        return system

    return _build


def hours(sells) -> list[int]:
    return sorted({int(key[11:13]) for key in sells})


def planned(system):
    co = system.coordinator
    slots = co._price_forecast()
    return co.planned_sell_slots(slots, {coordinator_module._slot_key(s) for s in co._buy_slots(slots, MORNING)})


def test_without_sun_the_window_runs_to_the_next_midday(morning):
    co = morning(sun=0.2).coordinator

    assert co._sell_window_end(MORNING) == (DAY + timedelta(days=1)).replace(hour=12)


def test_with_sun_it_closes_at_this_midday(morning):
    co = morning(sun=12.0).coordinator

    assert co._sell_window_end(MORNING) == DAY.replace(hour=12)


def test_without_a_forecast_it_closes_at_this_midday_as_before(morning):
    co = morning(sun=None).coordinator

    assert co._sell_window_end(MORNING) == DAY.replace(hour=12)


def test_without_sun_it_sells_in_the_evening_peak(morning):
    """The morning at 0.52 against the peak at 0.59: the same kWh, bought the
    same night, earns more in the peak."""
    sells = planned(morning(sun=0.2))

    assert sells
    assert hours(sells) and min(hours(sells)) >= 16


def test_with_sun_it_still_sells_before_midday(morning):
    """The sun puts back after noon what was sold before it, so the morning
    is a sale of its own, as it always was."""
    sells = planned(morning(sun=12.0))

    assert sells
    assert max(hours(sells)) < 12


async def test_the_tick_waits_rather_than_selling_in_the_morning(morning):
    system = morning(sun=0.2)

    await system.coordinator._async_tick(None)

    assert system.coordinator.active_policy != "trade_sell"
    assert system.coordinator.last_trade_verdict["why"] == "later"

"""Refilling a sale from the sun - asked for by the owner on 25 September:
"ik had winst gehad omdat de batterij met zon is opgeladen".

Until now a sold kilowatt hour was always bought back from the grid. When
tomorrow's sun will fill the packs anyway, the kWh sold tonight comes back
for what that sun would have earned going to the grid instead - less than the
cheapest grid hour on most days. But only the sun *beyond* the room the packs
have by sunrise refills the sale: first the room there is now, and what the
house draws from them overnight.
"""
from __future__ import annotations

from datetime import timedelta

import pytest

from custom_components.battery_management import coordinator as coordinator_module
from custom_components.battery_management.const import (
    CONF_BATTERY_POWER_SENSOR,
    CONF_SOLAR_FORECAST_SENSORS,
    CONF_SOLAR_FORECAST_TOMORROW_SENSORS,
    CONF_TRADE_MARGIN,
)

from .conftest import GRID_SENSOR
from .test_trade_sell import ENERGY_TAX, MARKUP_AND_VAT, NOW, PRICE_SENSOR, trading  # noqa: F401

TODAY = "sensor.energy_production_today"
TOMORROW = "sensor.energy_production_tomorrow"
PACKS = "sensor.battery_power"

#: tomorrow's sunny hours, and what a kWh of them earns fed back with the tax
MIDDAY = 0.17
MIDDAY_VALUE = MIDDAY - ENERGY_TAX - MARKUP_AND_VAT + ENERGY_TAX


def prices() -> dict:
    """The evening peak at 0.55 now, tomorrow's sunny hours at 0.17, and
    0.30 otherwise - so the cheapest grid hour to buy back in is a sunny one."""
    midnight = NOW.replace(hour=0)
    allin, market, untaxed = [], [], []
    for i in range(48):
        start = midnight + timedelta(hours=i)
        tomorrow_midday = start.date() > NOW.date() and 10 <= start.hour < 16
        value = 0.55 if start == NOW else MIDDAY if tomorrow_midday else 0.30
        row = {"start": start.isoformat(), "end": (start + timedelta(hours=1)).isoformat()}
        allin.append({**row, "value": value})
        market.append({**row, "value": round(value - ENERGY_TAX - MARKUP_AND_VAT, 4)})
        untaxed.append({**row, "value": round(value - ENERGY_TAX, 4)})
    return {"raw_today": allin, "market_prices": market, "untaxed_prices": untaxed}


@pytest.fixture
def sunny(trading, monkeypatch):
    """Packs at 80 %, selling down to 70 %, 400 W overnight, and a capture
    share of 60 % measured."""

    def _build(*, tomorrow=30.0, share=0.6, draw=400.0, **options):
        system = trading(
            soc=(80.0, 80.0),
            **{CONF_SOLAR_FORECAST_SENSORS: [TODAY], **options},
        )
        system.hass.states.set(PRICE_SENSOR, 0.55, prices())
        system.hass.states.set(TODAY, 0.0)
        if tomorrow is not None:
            system.hass.states.set(TOMORROW, tomorrow)
        system.coordinator.sell_floor = 70
        monkeypatch.setattr(
            system.coordinator, "solar_capture", lambda: (share, 0 if share is None else 5)
        )
        system.coordinator._house_draw_w = draw
        return system

    return _build


def verdict(system):
    return system.coordinator.trade_verdict()


def test_tomorrow_s_sun_refills_the_sale_at_what_it_would_have_earned(sunny):
    """18 kWh of sun reaches the packs; 5.6 kWh of room now and 6.0 overnight
    (15 hours at 400 W) come first, and the 2.8 kWh sold still fits: all of it
    from the sun."""
    got = verdict(sunny())

    assert got["solar"]["share"] == 1.0
    assert got["refill"] == pytest.approx(MIDDAY_VALUE)
    assert got["refill_grid"] == pytest.approx(MIDDAY)
    assert got["margin"] == pytest.approx(0.52 - MIDDAY_VALUE / 0.88 - 5000 / 6000 / 28)


def test_a_grey_tomorrow_leaves_it_to_the_grid(sunny):
    """6 kWh of sun does not even fill the room the night leaves."""
    got = verdict(sunny(tomorrow=10.0))

    assert got["solar"]["share"] == 0.0
    assert got["refill"] == pytest.approx(MIDDAY)


def test_part_of_the_sale_can_come_back_from_the_sun(sunny):
    """13 kWh of sun: 11.6 for the room, 1.4 of the 2.8 kWh sold."""
    got = verdict(sunny(tomorrow=13.0 / 0.6))

    assert got["solar"]["share"] == pytest.approx(0.5, abs=0.01)
    assert got["refill"] == pytest.approx((MIDDAY + MIDDAY_VALUE) / 2, abs=0.001)


def test_the_night_counts(sunny):
    """No draw overnight: the same 13 kWh now refills the whole sale."""
    assert verdict(sunny(tomorrow=13.0 / 0.6, draw=0.0))["solar"]["share"] == 1.0


def test_not_without_a_measured_capture_share(sunny):
    """The forecast is what the panels make, and the house takes most of it
    on the way past. Unmeasured, it would be counted whole - and a sale on
    the strength of sun that never reaches the packs is a sale at a loss."""
    got = verdict(sunny(share=None))

    assert got["solar"] is None
    assert got["refill"] == pytest.approx(MIDDAY)


def test_not_without_knowing_the_night(sunny):
    assert verdict(sunny(draw=None))["solar"] is None


def test_not_without_tomorrow_s_forecast(sunny):
    assert verdict(sunny(tomorrow=None))["solar"] is None


def test_tomorrow_s_sensor_can_be_named(sunny):
    system = sunny(tomorrow=None, **{CONF_SOLAR_FORECAST_TOMORROW_SENSORS: ["sensor.my_tomorrow"]})
    system.hass.states.set("sensor.my_tomorrow", 30.0)

    assert verdict(system)["solar"]["share"] == 1.0


def test_the_twins_are_found_per_plane(sunny):
    system = sunny(**{CONF_SOLAR_FORECAST_SENSORS: [TODAY, "sensor.energy_production_today_2", "sensor.other"]})
    system.hass.states.set("sensor.energy_production_tomorrow_2", 5.0)

    assert system.coordinator._tomorrow_sensors() == [TOMORROW, "sensor.energy_production_tomorrow_2"]
    assert system.coordinator.solar_tomorrow() == pytest.approx(35.0)


def test_the_plan_counts_the_sun_the_same_way(sunny):
    system = sunny(**{CONF_TRADE_MARGIN: 0.30})

    margin = system.coordinator.sell_forecast()[NOW.isoformat()]

    assert margin == pytest.approx(verdict(system)["margin"], abs=1e-4)


def test_a_sale_after_noon_tomorrow_has_no_sun_to_count_on(sunny):
    """Its refill would be the day after tomorrow's sun, which no forecast
    here covers - so the grid's price, however sunny tomorrow is."""
    system = sunny()
    ctx = system.coordinator._refill_context(system.coordinator._price_forecast())
    tomorrow_afternoon = next(
        s for s in ctx["slots"] if s.start == NOW + timedelta(hours=20)
    )

    assert system.coordinator._solar_refill(tomorrow_afternoon, ctx) is None


def test_a_morning_sale_is_refilled_by_the_same_day_s_sun(sunny):
    system = sunny()
    ctx = system.coordinator._refill_context(system.coordinator._price_forecast())
    ctx["sun_tomorrow"] = 18.0
    tomorrow_morning = next(s for s in ctx["slots"] if s.start == NOW + timedelta(hours=14))

    got = system.coordinator._solar_refill(tomorrow_morning, ctx)

    # 08:00 to 10:00 is two hours of night, not sixteen
    assert got["room_kwh"] == pytest.approx(5.6 + 0.4 * 1.0, abs=0.05)


def test_the_status_says_how_much_is_sun(sunny):
    system = sunny()
    system.coordinator.last_trade_verdict = system.coordinator.trade_verdict()

    attributes = system.coordinator.trade_attributes()

    assert attributes["refill_solar_share"] == 1.0
    assert attributes["refill_solar_eur_kwh"] == pytest.approx(MIDDAY_VALUE)
    assert attributes["refill_grid_eur_kwh"] == pytest.approx(MIDDAY)


# -- the house draw ------------------------------------------------------------


async def test_the_house_draw_follows_meter_plus_packs(trading, monkeypatch):
    clock = [1_000_000.0]
    monkeypatch.setattr(coordinator_module.time, "time", lambda: clock[0])
    system = trading(**{CONF_BATTERY_POWER_SENSOR: PACKS})
    system.coordinator.enabled = False
    system.hass.states.set(GRID_SENSOR, 100)
    system.hass.states.set(PACKS, 300)

    for _ in range(240):
        await system.coordinator._async_tick(None)
        clock[0] += 60

    assert system.coordinator._house_draw_w == pytest.approx(400, abs=1)

    # a sunny afternoon - the house net of the sun below nought - is ignored
    system.hass.states.set(GRID_SENSOR, -2000)
    for _ in range(60):
        await system.coordinator._async_tick(None)
        clock[0] += 60
    assert system.coordinator._house_draw_w == pytest.approx(400, abs=1)

    # and a busier evening pulls it up, slowly
    system.hass.states.set(GRID_SENSOR, 1500)
    await system.coordinator._async_tick(None)
    assert 400 < system.coordinator._house_draw_w < 500

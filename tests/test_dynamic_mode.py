"""Charging from the grid on the cheapest hours.

Buying from the grid is the one thing that cannot be expressed as a bound on the
setpoint - there is no surplus to regulate against - so this mode forces a value
where every other mode only constrains one. Three conditions must all hold
before a cent is spent: cheap now, packs low, and not much sun coming.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from custom_components.battery_management import coordinator as coordinator_module
from custom_components.battery_management.const import (
    CONF_CHARGE_BELOW_SOC,
    CONF_CHEAP_HOURS,
    CONF_PRICE_SENSOR,
    CONF_SOLAR_FORECAST_MAX,
    CONF_SOLAR_FORECAST_SENSOR,
    FLOW_CHARGE,
    MODE_DYNAMIC,
    MODE_GRID_ZERO,
    MODES,
    POLICY_DYNAMIC_CHARGE,
    POLICY_DYNAMIC_NO_PRICES,
    POLICY_GRID_ZERO,
)

PRICE_SENSOR = "sensor.energy_prices"
FORECAST_SENSOR = "sensor.solar_forecast_today"

#: a fixed clock, so "is this hour the cheapest" cannot depend on when the suite
#: happens to run
NOW = datetime(2026, 8, 5, 12, 0, tzinfo=timezone.utc)


def price_attributes(bargain_at: datetime, now: datetime) -> dict:
    """Two days of expensive hours with exactly one bargain, at `bargain_at`.

    Spans 48 h from midnight so the bargain is always inside the forecast
    whatever the wall clock says - a window built on `now.hour + 6 % 24` lands
    in the past after 18:00, and then every remaining hour costs the same and
    the tie-break makes "now" the cheapest.
    """
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    slots = []
    for i in range(48):
        start = midnight + timedelta(hours=i)
        slots.append(
            {
                "start": start.isoformat(),
                "end": (start + timedelta(hours=1)).isoformat(),
                "value": 0.02 if start <= bargain_at < start + timedelta(hours=1) else 0.30,
            }
        )
    return {"raw_today": slots}


@pytest.fixture
def dynamic(build_system, monkeypatch):
    """A dynamic-mode system whose current hour is the cheapest of the day."""

    def _build(*, soc=(20.0, 20.0), grid=300, cheap=True, **options):
        monkeypatch.setattr(coordinator_module.dt_util, "utcnow", lambda: NOW)
        now = NOW
        system = build_system(
            grid=grid,
            units=(("093", soc[0]), ("052", soc[1])),
            **{
                CONF_PRICE_SENSOR: PRICE_SENSOR,
                CONF_CHEAP_HOURS: 1,
                CONF_CHARGE_BELOW_SOC: 40,
                **options,
            },
        )
        # the bargain is either the hour we are in, or one safely ahead of us
        bargain_at = now if cheap else now + timedelta(hours=6)
        system.hass.states.set(
            PRICE_SENSOR, 0.02, price_attributes(bargain_at, now)
        )
        system.coordinator.mode = MODE_DYNAMIC
        return system

    return _build


def test_dynamic_is_hidden_without_a_price_sensor(build_system):
    """Nothing is mandatory: no sensor, no mode, no change to anything else."""
    system = build_system(grid=500)

    assert system.coordinator.available_modes == MODES
    assert MODE_DYNAMIC not in system.coordinator.available_modes


def test_dynamic_appears_once_a_price_sensor_is_set(build_system):
    system = build_system(grid=500, **{CONF_PRICE_SENSOR: PRICE_SENSOR})

    assert MODE_DYNAMIC in system.coordinator.available_modes


async def test_selecting_dynamic_without_a_price_sensor_is_refused(build_system):
    system = build_system(grid=500)

    with pytest.raises(ValueError):
        await system.coordinator.async_set_mode(MODE_DYNAMIC)


async def test_buys_from_the_grid_during_the_cheapest_hour(dynamic):
    system = dynamic()

    await system.coordinator._async_tick(None)

    assert system.coordinator.active_policy == POLICY_DYNAMIC_CHARGE
    assert system.coordinator.setpoint == -7000  # both packs, full tilt
    assert system.flows() == [FLOW_CHARGE, FLOW_CHARGE]


async def test_does_nothing_special_outside_the_cheap_hour(dynamic):
    """Grid-zero underneath: it still covers the house from the packs."""
    system = dynamic(cheap=False)

    await system.coordinator._async_tick(None)

    assert system.coordinator.active_policy == POLICY_GRID_ZERO
    assert system.coordinator.setpoint == 300


async def test_will_not_buy_when_the_packs_are_already_full_enough(dynamic):
    system = dynamic(soc=(80.0, 90.0))

    await system.coordinator._async_tick(None)

    assert system.coordinator.active_policy != POLICY_DYNAMIC_CHARGE


async def test_one_low_pack_is_reason_enough(dynamic):
    system = dynamic(soc=(90.0, 20.0))

    await system.coordinator._async_tick(None)

    assert system.coordinator.active_policy == POLICY_DYNAMIC_CHARGE


async def test_skips_buying_when_plenty_of_sun_is_coming(dynamic):
    """Do not pay for what arrives free in four hours."""
    system = dynamic(
        **{CONF_SOLAR_FORECAST_SENSOR: FORECAST_SENSOR, CONF_SOLAR_FORECAST_MAX: 20}
    )
    system.hass.states.set(FORECAST_SENSOR, 35)

    await system.coordinator._async_tick(None)

    assert system.coordinator.active_policy != POLICY_DYNAMIC_CHARGE


async def test_buys_when_little_sun_is_expected(dynamic):
    system = dynamic(
        **{CONF_SOLAR_FORECAST_SENSOR: FORECAST_SENSOR, CONF_SOLAR_FORECAST_MAX: 20}
    )
    system.hass.states.set(FORECAST_SENSOR, 4)

    await system.coordinator._async_tick(None)

    assert system.coordinator.active_policy == POLICY_DYNAMIC_CHARGE


async def test_a_missing_forecast_does_not_block_a_cheap_hour(dynamic):
    """No forecast is not the same as 'lots of sun coming'."""
    system = dynamic(
        **{CONF_SOLAR_FORECAST_SENSOR: FORECAST_SENSOR, CONF_SOLAR_FORECAST_MAX: 20}
    )
    system.hass.states.set(FORECAST_SENSOR, "unavailable")

    await system.coordinator._async_tick(None)

    assert system.coordinator.active_policy == POLICY_DYNAMIC_CHARGE


async def test_says_so_when_the_price_sensor_is_mute(dynamic):
    """An impotent mode must announce itself, not look like normal operation."""
    system = dynamic()
    system.hass.states.set(PRICE_SENSOR, "unavailable")

    await system.coordinator._async_tick(None)

    assert system.coordinator.active_policy == POLICY_DYNAMIC_NO_PRICES


async def test_an_unreadable_price_sensor_still_regulates(dynamic):
    """Losing prices must not stop the packs covering the house."""
    system = dynamic(grid=800)
    system.hass.states.set(PRICE_SENSOR, "unavailable")

    await system.coordinator._async_tick(None)

    assert system.coordinator.setpoint == 800
    assert sum(system.allocation().values()) == 800


async def test_the_soc_reserve_still_applies_in_dynamic_mode(dynamic):
    """Settings that apply in every mode really do apply in every mode."""
    system = dynamic(cheap=False, grid=500, soc=(30.0, 30.0))
    system.coordinator.soc_reserve = 30.0

    await system.coordinator._async_tick(None)

    assert system.coordinator.setpoint == 0


async def test_switching_away_from_dynamic_stops_buying(dynamic):
    system = dynamic()
    await system.coordinator._async_tick(None)
    assert system.coordinator.setpoint == -7000

    system.coordinator.mode = MODE_GRID_ZERO
    system.hass.services.clear()
    await system.coordinator._async_tick(None)

    assert system.coordinator.setpoint > -7000

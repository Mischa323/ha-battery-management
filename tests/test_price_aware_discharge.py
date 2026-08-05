"""Spending the stored kWh where they save the most.

The packs hold less than a day's consumption at the primary site, so the useful
question is not whether they can be filled but where their charge goes. Covering
a cheap midday hour from the battery and then buying at the evening peak is the
expensive way round.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from custom_components.battery_management import coordinator as coordinator_module
from custom_components.battery_management.const import (
    CONF_DISCHARGE_ANYWAY_SOC,
    CONF_EXPENSIVE_HOURS,
    CONF_PRICE_SENSOR,
    FLOW_DISCHARGE,
    MODE_DYNAMIC,
    MODE_GRID_ZERO,
    POLICY_DYNAMIC_HOLD,
    POLICY_GRID_ZERO,
)

PRICE_SENSOR = "sensor.energy_prices"
NOW = datetime(2026, 8, 5, 12, 0, tzinfo=timezone.utc)


def prices(dear_at: datetime, now: datetime = NOW) -> dict:
    """A flat day with one genuinely expensive hour."""
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    slots = []
    for i in range(48):
        start = midnight + timedelta(hours=i)
        dear = start <= dear_at < start + timedelta(hours=1)
        slots.append(
            {
                "start": start.isoformat(),
                "end": (start + timedelta(hours=1)).isoformat(),
                "value": 0.45 if dear else 0.20,
            }
        )
    return {"raw_today": slots}


@pytest.fixture
def dynamic(build_system, monkeypatch):
    def _build(*, dear_now: bool, soc=(60.0, 60.0), grid=800, **options):
        monkeypatch.setattr(coordinator_module.dt_util, "utcnow", lambda: NOW)
        system = build_system(
            grid=grid,
            units=(("093", soc[0]), ("052", soc[1])),
            **{
                CONF_PRICE_SENSOR: PRICE_SENSOR,
                CONF_EXPENSIVE_HOURS: 1,
                **options,
            },
        )
        system.hass.states.set(
            PRICE_SENSOR, 0.20, prices(NOW if dear_now else NOW + timedelta(hours=6))
        )
        system.coordinator.mode = MODE_DYNAMIC
        return system

    return _build


async def test_holds_the_charge_during_a_cheap_hour(dynamic):
    """Buy this hour from the grid instead, and keep the kWh for the peak."""
    system = dynamic(dear_now=False)

    await system.coordinator._async_tick(None)

    assert system.coordinator.setpoint == 0
    assert system.allocation() == {"Batterij 1": 0, "Batterij 2": 0}
    assert system.coordinator.active_policy == POLICY_DYNAMIC_HOLD


async def test_discharges_during_the_dear_hour(dynamic):
    system = dynamic(dear_now=True)

    await system.coordinator._async_tick(None)

    assert system.coordinator.setpoint == 800
    assert system.flows() == [FLOW_DISCHARGE, FLOW_DISCHARGE]
    assert system.coordinator.active_policy == POLICY_GRID_ZERO


async def test_a_nearly_full_pack_discharges_anyway(dynamic):
    """Refusing would leave nowhere for the sun still to come. Spilling free
    energy to save a few cents is a bad trade."""
    system = dynamic(dear_now=False, soc=(95.0, 60.0))

    await system.coordinator._async_tick(None)

    assert system.coordinator.setpoint == 800
    assert system.coordinator.active_policy == POLICY_GRID_ZERO


async def test_the_full_enough_threshold_is_configurable(dynamic):
    system = dynamic(
        dear_now=False, soc=(70.0, 60.0), **{CONF_DISCHARGE_ANYWAY_SOC: 65}
    )

    await system.coordinator._async_tick(None)

    assert system.coordinator.setpoint == 800


async def test_charging_is_never_blocked_by_holding(dynamic):
    """Holding is a ceiling on discharge, not a freeze: surplus still goes in."""
    system = dynamic(dear_now=False, grid=-1500)

    await system.coordinator._async_tick(None)

    assert system.coordinator.setpoint == -1500


async def test_no_prices_means_no_cleverness(dynamic):
    """Losing the price feed must not stop the packs covering the house."""
    system = dynamic(dear_now=False)
    system.hass.states.set(PRICE_SENSOR, "unavailable")

    await system.coordinator._async_tick(None)

    assert system.coordinator.setpoint == 800


async def test_it_only_applies_to_the_dynamic_mode(dynamic):
    system = dynamic(dear_now=False)
    system.coordinator.mode = MODE_GRID_ZERO

    await system.coordinator._async_tick(None)

    assert system.coordinator.setpoint == 800


async def test_setting_expensive_hours_to_zero_switches_it_off(dynamic):
    system = dynamic(dear_now=False, **{CONF_EXPENSIVE_HOURS: 0})

    await system.coordinator._async_tick(None)

    assert system.coordinator.setpoint == 800


async def test_holding_does_not_wind_the_integrator_up(dynamic):
    """It reuses the existing clamp, so releasing cannot unleash stored error."""
    system = dynamic(dear_now=False, grid=200)

    for _ in range(10):
        await system.coordinator._async_tick(None)
    assert system.coordinator.setpoint == 0

    system.coordinator.mode = MODE_GRID_ZERO
    await system.coordinator._async_tick(None)

    assert system.coordinator.setpoint == 200

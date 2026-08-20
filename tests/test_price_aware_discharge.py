"""Grid-zero is the floor, including outside the expensive hours.

Dynamic mode used to refuse to discharge unless the current hour was one of the
dearest ahead, so the stored kWh would go to the peak instead of to a cheap
hour. It read well and it was wrong, and the night of 2026-08-19/20 at the
primary site is why.

The ranking runs over a rolling 24 h window, so at midnight that window already
contains the coming evening peak. No night or morning hour can ever win it. The
hold was therefore not occasional - it was guaranteed to last from midnight
until the evening, every single day. That night it held 7.3 kWh untouched
through 5.62 kWh of imports, at prices the same forecast said were not the
cheapest ahead.

The owner's ruling: after the cheap hours the packs simply hold the grid at
zero. Running empty is an answer about how big the batteries are, not a reason
to stop discharging. So `expensive_hours` now only colours the chart, and
nothing bounds discharge but the packs' own limits.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from custom_components.battery_management import coordinator as coordinator_module
from custom_components.battery_management.const import (
    CONF_EXPENSIVE_HOURS,
    CONF_PRICE_SENSOR,
    FLOW_DISCHARGE,
    MODE_DYNAMIC,
    MODE_GRID_ZERO,
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


async def test_discharges_outside_the_dearest_hours(dynamic):
    """The night of 2026-08-19: 57 % in the packs, the house on the grid.

    This is the regression. The hour is not one of the dearest ahead and the
    packs are nowhere near full, which is exactly the state that used to park
    the setpoint at 0 and leave the house importing.
    """
    system = dynamic(dear_now=False, soc=(58.0, 57.0))

    await system.coordinator._async_tick(None)

    assert system.coordinator.setpoint == 800
    assert system.flows() == [FLOW_DISCHARGE, FLOW_DISCHARGE]
    assert system.coordinator.active_policy == POLICY_GRID_ZERO


async def test_discharges_during_the_dear_hour(dynamic):
    system = dynamic(dear_now=True)

    await system.coordinator._async_tick(None)

    assert system.coordinator.setpoint == 800
    assert system.flows() == [FLOW_DISCHARGE, FLOW_DISCHARGE]
    assert system.coordinator.active_policy == POLICY_GRID_ZERO


async def test_a_nearly_empty_pack_still_discharges(dynamic):
    """Running out is a fact about the capacity, not a reason to stop.

    The old escape hatch worked the other way round - only a *nearly full* pack
    was allowed to discharge outside the peak. Low packs were precisely the ones
    held back, which is how a 57 % pack sat out a whole night.
    """
    system = dynamic(dear_now=False, soc=(12.0, 10.0))

    await system.coordinator._async_tick(None)

    assert system.coordinator.setpoint == 800
    assert system.coordinator.active_policy == POLICY_GRID_ZERO


async def test_expensive_hours_no_longer_bounds_anything(dynamic):
    """It survives as the chart's colouring, so it must not touch the setpoint."""
    for hours in (0, 1, 6, 12):
        system = dynamic(dear_now=False, **{CONF_EXPENSIVE_HOURS: hours})

        await system.coordinator._async_tick(None)

        assert system.coordinator.setpoint == 800, hours


async def test_charging_is_still_never_blocked(dynamic):
    """Surplus goes in whatever the hour costs."""
    system = dynamic(dear_now=False, grid=-1500)

    await system.coordinator._async_tick(None)

    assert system.coordinator.setpoint == -1500


async def test_no_prices_means_no_cleverness(dynamic):
    """Losing the price feed must not stop the packs covering the house."""
    system = dynamic(dear_now=False)
    system.hass.states.set(PRICE_SENSOR, "unavailable")

    await system.coordinator._async_tick(None)

    assert system.coordinator.setpoint == 800


async def test_grid_zero_mode_is_unchanged(dynamic):
    system = dynamic(dear_now=False)
    system.coordinator.mode = MODE_GRID_ZERO

    await system.coordinator._async_tick(None)

    assert system.coordinator.setpoint == 800

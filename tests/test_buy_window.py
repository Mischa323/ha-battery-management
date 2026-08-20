"""Not selling in the hour we came to buy in.

Reported from the primary site on 2026-08-20: during one cheap hour the packs
charged, reached the ceiling, and then discharged into the house again. The
hour both bought and sold, paying the round-trip loss for nothing.

While a purchase is actually running the setpoint is forced negative and cannot
discharge. The gap is the rest of the hour: `_dynamic_should_charge` says no
once the packs reach the ceiling or the sun turns out to be enough, and then
plain grid-zero resumed and covered the house from the battery. So the hour
itself now bounds discharge, whether or not power is being drawn this tick.

This is **not** the discharge hold that was removed the same day, and the
difference is what keeps it honest: that one blocked discharge for eighteen
hours a day, this one for the hours actually earmarked for buying - and it lets
go by itself, because an hour drops out of `slots_to_buy` as soon as the
shrinking need no longer reaches it.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from custom_components.battery_management import coordinator as coordinator_module
from tests.conftest import GRID_SENSOR
from custom_components.battery_management.const import (
    CONF_CHARGE_BELOW_SOC,
    CONF_CHEAP_HOURS,
    CONF_FULL_CHARGE_MINUTES,
    CONF_PRICE_SENSOR,
    FLOW_CHARGE,
    MODE_DYNAMIC,
    MODE_GRID_ZERO,
    POLICY_BUY_WINDOW,
    POLICY_DYNAMIC_CHARGE,
)

PRICES = "sensor.energy_prices"
NOW = datetime(2026, 8, 20, 12, 30, tzinfo=timezone.utc)


def day(bargain_hour: int) -> dict:
    """A day with one clearly cheap hour, wide enough to clear the margin."""
    midnight = NOW.replace(hour=0, minute=0, second=0, microsecond=0)
    return {
        "raw_today": [
            {
                "start": (midnight + timedelta(hours=i)).isoformat(),
                "end": (midnight + timedelta(hours=i + 1)).isoformat(),
                "value": 0.02 if i == bargain_hour else 0.40,
            }
            for i in range(24)
        ]
    }


@pytest.fixture
def site(build_system, monkeypatch):
    """12:30, inside the day's one cheap hour, with the house importing."""

    def _build(*, soc=(50.0, 50.0), grid=800, cheap_hour=12, **options):
        monkeypatch.setattr(coordinator_module.dt_util, "utcnow", lambda: NOW)
        system = build_system(
            grid=grid,
            units=(("093", soc[0]), ("052", soc[1])),
            **{
                CONF_PRICE_SENSOR: PRICES,
                CONF_CHEAP_HOURS: 1,
                CONF_FULL_CHARGE_MINUTES: 120,
                CONF_CHARGE_BELOW_SOC: 60,
                **options,
            },
        )
        system.hass.states.set(PRICES, 0.02, day(cheap_hour))
        system.coordinator.mode = MODE_DYNAMIC
        return system

    return _build


async def test_it_buys_while_there_is_room(site):
    """The ordinary case, unchanged: below the ceiling, it draws from the grid."""
    system = site(soc=(20.0, 20.0))

    await system.coordinator._async_tick(None)

    assert system.coordinator.setpoint < 0
    assert system.coordinator.active_policy == POLICY_DYNAMIC_CHARGE
    assert system.flows() == [FLOW_CHARGE, FLOW_CHARGE]


async def filled_mid_purchase(site, grid=800):
    """The reported sequence: buy into the hour, then reach the ceiling.

    This is the only way the fault could happen, and it is worth spelling out.
    An hour is earmarked because the packs need it; once they are full the need
    is gone and the hour would no longer be earmarked - so the bound rests on
    the latch, which remembers that a purchase started in this hour.
    """
    system = site(soc=(20.0, 20.0), grid=grid)
    await system.coordinator._async_tick(None)
    assert system.coordinator.active_policy == POLICY_DYNAMIC_CHARGE
    for prefix in ("093", "052"):
        system.hass.states.set(f"sensor.{prefix}_soc", 95.0)
    return system


async def test_it_does_not_discharge_after_filling_mid_hour(site):
    """The regression. The house is importing 800 W, which every other hour of
    the day would cover from the battery - and this one must not, because we
    have just paid for that charge.

    Watched over a stretch of ticks rather than one, because the setpoint does
    not snap: it comes back from the buying value the integrator left it at,
    and what has to hold is that it never crosses into discharge on the way.
    """
    system = await filled_mid_purchase(site)

    seen = []
    for _ in range(20):
        await system.coordinator._async_tick(None)
        seen.append(system.coordinator.setpoint)

    assert max(seen) <= 0, seen           # never once sells
    assert seen[-1] == 0                  # and settles against the bound
    assert system.coordinator.active_policy == POLICY_BUY_WINDOW


async def test_the_bound_does_not_wind_the_integrator_up(site):
    """It reuses the existing clamp, so letting go cannot unleash stored error."""
    system = await filled_mid_purchase(site)

    for _ in range(10):
        await system.coordinator._async_tick(None)
    assert system.coordinator.setpoint == 0

    system.coordinator.mode = MODE_GRID_ZERO
    await system.coordinator._async_tick(None)

    assert system.coordinator.setpoint == 800


async def test_an_hour_it_never_buys_in_is_not_bound(site):
    """The boundary that keeps this from growing back into a whole-day hold.

    The packs are already above the ceiling when the cheap hour arrives, so
    nothing is ever bought and the hour is not one of "the hours it buys on".
    Grid-zero covers the house exactly as it would at any other time.
    """
    system = site(soc=(80.0, 80.0), grid=800)

    await system.coordinator._async_tick(None)

    assert system.coordinator.setpoint == 800
    assert system.coordinator.active_policy != POLICY_BUY_WINDOW


async def test_surplus_still_goes_in(site):
    """A bound on discharging, not a freeze: the sun is still welcome.

    Settled against the bound first, so the setpoint is at 0 and any movement
    has to come from the surplus rather than from the buying value it was left
    at - otherwise this passes without the bound doing anything at all.
    """
    system = await filled_mid_purchase(site)
    for _ in range(20):
        await system.coordinator._async_tick(None)
    assert system.coordinator.setpoint == 0

    system.hass.states.set(GRID_SENSOR, -1500)
    await system.coordinator._async_tick(None)

    assert system.coordinator.setpoint == -1500


async def test_an_ordinary_hour_discharges_as_before(site):
    """The bargain is at 03:00, so 12:30 is an ordinary hour and grid-zero
    covers the house exactly as it always did. This is the assertion that keeps
    the rule from creeping back into a whole-day hold."""
    system = site(soc=(80.0, 80.0), grid=800, cheap_hour=3)

    await system.coordinator._async_tick(None)

    assert system.coordinator.setpoint == 800
    assert system.coordinator.active_policy != POLICY_BUY_WINDOW


async def test_it_only_applies_to_the_dynamic_mode(site):
    system = site(soc=(80.0, 80.0), grid=800)
    system.coordinator.mode = MODE_GRID_ZERO

    await system.coordinator._async_tick(None)

    assert system.coordinator.setpoint == 800


async def test_no_prices_means_no_bound(site):
    system = site(soc=(80.0, 80.0), grid=800)
    system.hass.states.set(PRICES, "unavailable")

    await system.coordinator._async_tick(None)

    assert system.coordinator.setpoint == 800


async def test_a_purchase_under_way_keeps_the_bound_for_its_hour(site):
    """The latch, and why it matters here.

    The packs fill part-way through the hour, so the need no longer reaches
    this slot and it drops out of `slots_to_buy`. Without the latch the bound
    would let go mid-hour and the packs would start covering the house from
    the charge just bought - which is the round trip this exists to stop.
    """
    system = site(soc=(20.0, 20.0), grid=800)
    await system.coordinator._async_tick(None)
    assert system.coordinator.active_policy == POLICY_DYNAMIC_CHARGE

    # the packs fill right up, so nothing more is worth buying this hour
    for prefix in ("093", "052"):
        system.hass.states.set(f"sensor.{prefix}_soc", 95.0)
    await system.coordinator._async_tick(None)

    assert system.coordinator.setpoint <= 0
    assert system.coordinator.active_policy == POLICY_BUY_WINDOW

"""Buying for tonight when tomorrow is cheaper, and keeping the minimum.

Reported on 2 November: "het zijn nu de goedkope uren, dan zou hij toch moeten
inkopen zodat hij voor vanavond klaar staat". With tomorrow a cent cheaper -
which, at the owner's one-cent margin, is most days - the buying ceiling was
lowered to "minimaal laden", 25 %, and the forecast had the packs dry at
21:00. A cheaper day is a reason to take only what tonight needs; 25 % was
not that. Now it is what the coming peak draws, on top of the packs' floor.

And the same morning the minimum was kept by buying: the house drew the packs
under 25 %, the minimum bought them back, the house drew them under again -
8.2 kWh between 03:45 and 07:45 at up to EUR 0.47. Before the peak the
minimum is now kept by not discharging under it.
"""
from __future__ import annotations

import pytest

from custom_components.battery_management import coordinator as coordinator_module
from custom_components.battery_management.const import (
    POLICY_CHEAPER_TOMORROW,
    POLICY_MINIMUM_HOLD,
)
from custom_components.battery_management.trading import ROUND_TRIP

from .conftest import GRID_SENSOR
from .test_save_for_peak import AFTERNOON, AFTERNOON_AT_040, house  # noqa: F401
from .test_trade_fill import afternoon, prices  # noqa: F401
from .test_trade_sell import trading  # noqa: F401

#: 18:00-22:00 at 2 kW is 8 kWh; through one leg of the loss, of 28 kWh,
#: on top of a 5 % floor
TONIGHT = 5.0 + 8.0 / ROUND_TRIP ** 0.5 / 28.0 * 100.0


def test_tonight_is_what_the_peak_draws(house):
    co = house().coordinator
    co.buy_ceiling_min = 25.0

    assert co._tonight_level() == pytest.approx(TONIGHT, abs=0.5)


def test_never_less_than_the_minimum(house):
    co = house(draw=100.0).coordinator
    co.buy_ceiling_min = 25.0

    assert co._tonight_level() == 25.0


def test_with_tomorrow_cheaper_it_still_buys_for_tonight(house):
    """Tomorrow at 0.20 is a cheaper day; the ceiling comes down to what the
    evening needs, not to the 25 % minimum."""
    system = house(soc=20.0, **dict(AFTERNOON_AT_040, tomorrow=0.20))
    co = system.coordinator
    co.buy_ceiling_min = 25.0

    ceiling, reason = co._buy_ceiling()

    assert reason == POLICY_CHEAPER_TOMORROW
    assert ceiling == pytest.approx(TONIGHT, abs=0.5)
    assert ceiling > 30.0


# -- keeping the minimum ------------------------------------------------------


async def test_at_the_minimum_before_the_peak_it_is_kept(house):
    """13:00, the packs at 24 % with the minimum at 25, a light house: the
    house takes the grid rather than the packs going under it."""
    system = house(soc=24.0, draw=100.0)
    co = system.coordinator
    co.buy_ceiling_min = 25.0
    system.hass.states.set(GRID_SENSOR, 600)

    await co._async_tick(None)

    assert co.active_policy == POLICY_MINIMUM_HOLD
    assert co.setpoint <= 0
    assert "houdt het minimum van 25 % vast" in co.tick_log[-1]["doing"]


async def test_above_it_the_packs_give_as_before(house):
    system = house(soc=60.0, draw=100.0)
    co = system.coordinator
    co.buy_ceiling_min = 25.0

    await co._async_tick(None)

    assert co.active_policy != POLICY_MINIMUM_HOLD


async def test_in_the_peak_the_minimum_goes_out(house, monkeypatch):
    """What it is for. Tomorrow cheaper, with a peak of its own at 18:00, so
    there is a next peak to keep for - and still not in this one."""
    tomorrow = {24 + h: 0.30 for h in range(24)} | {24 + h: 0.45 for h in (18, 19, 20, 21)}
    day = dict(AFTERNOON_AT_040, overrides={**AFTERNOON_AT_040["overrides"], **tomorrow})
    system = house(soc=24.0, draw=100.0, **day)
    co = system.coordinator
    co.buy_ceiling_min = 25.0
    monkeypatch.setattr(coordinator_module.dt_util, "utcnow",
                        lambda: AFTERNOON.replace(hour=19))
    system.hass.states.set(GRID_SENSOR, 600)     # read at the new hour

    await co._async_tick(None)

    assert co.tick_log, "the tick ran"
    assert co.active_policy != POLICY_MINIMUM_HOLD
    assert co.setpoint > 0


async def test_no_minimum_no_keeping(house):
    system = house(soc=24.0, draw=100.0)

    await system.coordinator._async_tick(None)

    assert system.coordinator.active_policy != POLICY_MINIMUM_HOLD


# -- what is kept for the peak ------------------------------------------------


def test_a_slot_it_buys_in_is_not_also_kept(house):
    """15:00 at 0.20 is bought; keeping it as well would only show twice."""
    day = dict(AFTERNOON_AT_040, overrides={13: 0.40, 14: 0.40, 15: 0.20, 16: 0.40})
    system = house(soc=10.0, **day)
    co = system.coordinator
    now = coordinator_module.dt_util.utcnow()
    slots = co._price_forecast()

    bought = {coordinator_module._slot_key(s) for s in co._buy_slots(slots, now)}
    kept = co.peak_holds(slots, now)

    assert bought
    assert not bought & kept

"""A flat day: what counts as a peak, and what is not done in one.

Reported on 13 November, of the day before: "waarom ging hij gisteren tijdens
een piek zeggen dat hij charge ging bewaren terwijl het in een piek was, het
was rond 7 uur". 12 November ran between EUR 0.30 and 0.44, with the morning
peak at 07:15-09:45 (local) and the evening's at 17:15. Three things went
wrong on it, all from what was taken for a peak:

* at 07:00, EUR 0.401, it "kept the charge for the peak of 10:00" - one
  quarter at 0.415 - a quarter before the real peak began;
* at 07:24, inside that peak, it bought for the minimum at 0.418;
* at 10:13, with tomorrow's prices not yet published, the dearest four
  hours of what was left of the day reached down to 0.391 at 11:00, and the
  minimum was bought at 0.36-0.415 to be ready for that, with 13:00-15:15
  at 0.31-0.34 still to come before the evening.

The prices are that day's, per quarter, in UTC (local time is an hour later).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from custom_components.battery_management import coordinator as coordinator_module
from custom_components.battery_management.const import POLICY_DYNAMIC_CHARGE
from custom_components.battery_management.prices import Slot, dear_slots

from .conftest import GRID_SENSOR
from .test_save_for_peak import house  # noqa: F401
from .test_trade_fill import afternoon  # noqa: F401
from .test_trade_sell import ENERGY_TAX, MARKUP_AND_VAT, PRICE_SENSOR, trading  # noqa: F401

DAY = datetime(2026, 11, 12, tzinfo=timezone.utc)
QUARTERS = [
    0.388, 0.385, 0.382, 0.376, 0.377, 0.370, 0.368, 0.360, 0.348, 0.351, 0.356, 0.352,
    0.348, 0.346, 0.349, 0.345, 0.357, 0.360, 0.355, 0.352, 0.356, 0.377, 0.400, 0.407,
    0.401, 0.418, 0.424, 0.431, 0.435, 0.431, 0.434, 0.426, 0.444, 0.428, 0.427, 0.396,
    0.415, 0.382, 0.362, 0.325, 0.391, 0.383, 0.368, 0.357, 0.356, 0.347, 0.342, 0.340,
    0.338, 0.336, 0.326, 0.323, 0.331, 0.332, 0.338, 0.341, 0.306, 0.334, 0.350, 0.358,
    0.363, 0.372, 0.384, 0.396, 0.400, 0.406, 0.410, 0.416, 0.414, 0.407, 0.412, 0.407,
    0.413, 0.402, 0.392, 0.380, 0.378, 0.369, 0.368, 0.364, 0.372, 0.364, 0.354, 0.339,
    0.366, 0.340, 0.317, 0.301, 0.360, 0.338, 0.320, 0.301,
]   # to 23:00 UTC: local midnight, where the published day ended


def at(hour, minute=0):
    """A moment of that day, given in local (CET) time."""
    return DAY + timedelta(hours=hour - 1, minutes=minute)


def the_day() -> dict:
    allin, market, untaxed = [], [], []
    for i, value in enumerate(QUARTERS):
        start = DAY + timedelta(minutes=15 * i)
        row = {"start": start.isoformat(), "end": (start + timedelta(minutes=15)).isoformat()}
        allin.append({**row, "value": value})
        market.append({**row, "value": round(value - ENERGY_TAX - MARKUP_AND_VAT, 4)})
        untaxed.append({**row, "value": round(value - ENERGY_TAX, 4)})
    return {"raw_today": allin, "market_prices": market, "untaxed_prices": untaxed}


@pytest.fixture
def flat_day(house, monkeypatch):
    """12 November at `moment`: a 1 kW house, the owner's one-cent margin."""

    def _build(moment, *, soc=26.0, minimum=25.0):
        system = house(soc=soc, draw=1000.0)
        co = system.coordinator
        co._price_margin = 0.01
        co.buy_ceiling_min = minimum
        monkeypatch.setattr(coordinator_module.dt_util, "utcnow", lambda: moment)
        system.hass.states.set(PRICE_SENSOR, 0.4, the_day())
        system.hass.states.set(GRID_SENSOR, 1000)
        return system

    return _build


def local(moment):
    return f"{moment + timedelta(hours=1):%H:%M}"


# -- what counts as dear --------------------------------------------------------


def test_of_a_whole_day_the_dearest_four_hours():
    window = [
        Slot(DAY + timedelta(hours=h), DAY + timedelta(hours=h + 1), 0.30 + h / 100)
        for h in range(24)
    ]

    assert len(dear_slots(window, 4.0)) == 4


def test_of_half_a_day_half_as_many():
    window = [
        Slot(DAY + timedelta(hours=h), DAY + timedelta(hours=h + 1), 0.30 + h / 100)
        for h in range(12)
    ]

    assert len(dear_slots(window, 4.0)) == 2


def test_at_1013_the_next_peak_is_the_evening_not_eleven(flat_day):
    co = flat_day(at(10, 13), soc=8.0).coordinator
    slots = co._price_forecast()

    assert local(co._next_peak_start(slots, at(10, 13))) == "17:15"


# -- the minimum ----------------------------------------------------------------


def test_the_minimum_waits_for_the_cheap_afternoon(flat_day):
    """Not 10:15-11:00 at 0.325-0.382: the cheapest before the evening."""
    co = flat_day(at(10, 13), soc=8.0).coordinator
    chosen = co._floor_slots(co._price_forecast(), at(10, 13))

    assert chosen
    assert all(s.price <= 0.326 for s in chosen)
    assert "15:00" in {local(s.start) for s in chosen}


def test_the_minimum_is_not_bought_inside_a_peak(flat_day):
    """07:24, EUR 0.418, under the minimum: nothing at peak level is chosen."""
    co = flat_day(at(7, 24), soc=19.0).coordinator
    slots = co._price_forecast()
    level = co._peak_level(slots, at(7, 24))

    chosen = co._floor_slots(slots, at(7, 24))

    assert all(s.price < level for s in chosen)
    assert "07:15" not in {local(s.start) for s in chosen}


async def test_and_the_tick_does_not_buy_there(flat_day):
    system = flat_day(at(7, 24), soc=19.0)

    await system.coordinator._async_tick(None)

    assert system.coordinator.active_policy != POLICY_DYNAMIC_CHARGE
    assert system.coordinator.setpoint >= 0


# -- keeping the charge -----------------------------------------------------------


def test_at_seven_the_peak_it_keeps_for_is_the_one_about_to_begin(flat_day):
    co = flat_day(at(7, 0)).coordinator
    block = co.plan()["forecast"]

    assert local(datetime.fromisoformat(block["peak"]["start"])) == "07:15"


def test_nothing_is_kept_in_a_slot_at_peak_level(flat_day):
    """06:45 at 0.407 is the peak already: the house gets the packs there."""
    co = flat_day(at(6, 30)).coordinator
    slots = co._price_forecast()
    level = co._peak_level(slots, at(6, 30))

    kept = co.peak_holds(slots, at(6, 30))

    by_key = {coordinator_module._slot_key(s): s for s in slots}
    assert all(by_key[key].price < level for key in kept)

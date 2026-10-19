""""Buy at least" is binding, and it is an amount for the day.

Asked for on 19 October: the owner would rather have the day's amount in the
packs, a few cents a kWh notwithstanding, than have the margin decide that
topping up does not pay. And the same evening, after the first version tried
to bring the level back up to 50 % for the night: "hij moet gewoon 50 %
opgeladen zijn over de hele dag. dus 14 kWh moest in de batterijen zijn gegaan
op de goedkoopste tijden. als hij in de avond geen 50 % haalt is dat geen
probleem."

So at 50 % of a 28 kWh bank, 14 kWh go into the packs each day - sun
included - in the cheapest slots before midnight, at full power, without the
margin. Once that is in, the margin decides as before.
"""
from __future__ import annotations

from datetime import timedelta

import pytest

from custom_components.battery_management import coordinator as coordinator_module
from custom_components.battery_management.const import POLICY_DYNAMIC_CHARGE, TRADE_OFF

from .conftest import GRID_SENSOR
from .test_trade_fill import afternoon, prices  # noqa: F401
from .test_trade_sell import trading  # noqa: F401

#: 18 October in miniature: nothing pays - 13:00-15:00 at 0.338, 0.39 otherwise
DEAR_DAY = dict(cheap=(13, 14), cheap_at=0.338, peak=(), ordinary=0.39)


@pytest.fixture
def day(afternoon, monkeypatch):
    def _build(*, soc=30.0, floor=50.0, done_kwh=0.0, **today):
        system = afternoon(soc=(soc, soc), **(today or DEAR_DAY))
        co = system.coordinator
        co.trade_mode = TRADE_OFF
        co._battery_price = 8500.0
        co._battery_cycles = 10000
        co._price_margin = 0.03
        co.buy_ceiling_min = floor
        done = [done_kwh]
        co.floor_done_kwh = lambda: done[0]
        co.test_done = done
        monkeypatch.setattr(co, "_solar_headroom_ceiling", lambda: 95.0)
        return system

    return _build


def bought(system):
    return [h for h in system.coordinator.plan()["hours"] if h["buy"]]


def test_the_floor_is_an_amount_for_the_day(day):
    """50 % of 28 kWh is 14 kWh a day."""
    co = day().coordinator

    assert co.floor_kwh() == pytest.approx(14.0)
    assert co.floor_remaining_kwh() == pytest.approx(14.0)


def test_a_day_where_nothing_pays_still_puts_it_in(day):
    """14 kWh at 7 kW is two hours: the two cheapest, whatever they cost."""
    picked = bought(day())

    assert len(picked) == 2
    assert {h["price"] for h in picked} == {0.338}


def test_what_went_in_today_counts_sun_included(day):
    """13.1 kWh in by the evening, as on 19 October: one quarter to go, not
    the six kWh it takes to bring the level back up to 50 %."""
    co = day(soc=28.0, done_kwh=13.1).coordinator

    assert co.floor_remaining_kwh() == pytest.approx(0.9)
    assert len(bought(day(soc=28.0, done_kwh=13.1))) == 1


def test_once_in_the_evening_level_does_not_matter(day):
    """In for the day, and the packs drawn down to 20 % since: nothing more."""
    assert bought(day(soc=20.0, done_kwh=14.0)) == []


def test_never_more_than_there_is_room_for(day):
    """At 90 % with "buy at most" at 95 there is 1.4 kWh of room, whatever
    the day's amount says."""
    system = day(soc=90.0)
    system.coordinator.buy_ceiling_max = 95.0

    assert system.coordinator.floor_remaining_kwh() == pytest.approx(1.4)


def test_without_a_floor_nothing_is_bought(day):
    assert bought(day(floor=0.0)) == []


def test_only_before_the_end_of_the_day(day):
    """Tomorrow's cheap hours do not count towards today's amount."""
    system = day(cheap=(), cheap_at=0.39, peak=(), ordinary=0.39, tomorrow=0.10)
    co = system.coordinator
    now = coordinator_module.dt_util.utcnow()

    slots = co._floor_slots(co._price_forecast(), now)
    assert slots
    assert all(s.start.date() == now.date() for s in slots)


def test_the_plan_says_how_much_and_how_much_is_in(day):
    floor = day(done_kwh=4.0).coordinator.plan()["floor"]

    assert floor["to"] == 50.0
    assert floor["kwh"] == pytest.approx(14.0)
    assert floor["done_kwh"] == pytest.approx(4.0)
    assert floor["remaining_kwh"] == pytest.approx(10.0)
    assert len(floor["hours"]) == 2


def test_no_floor_no_block(day):
    assert day(floor=0.0).coordinator.plan()["floor"] is None


async def test_the_tick_buys_until_the_day_s_amount_is_in(day, monkeypatch):
    system = day(soc=40.0, done_kwh=12.0)
    co = system.coordinator
    first = co._floor_slots(co._price_forecast(), coordinator_module.dt_util.utcnow())[0]
    at = first.start + timedelta(minutes=1)
    monkeypatch.setattr(coordinator_module.dt_util, "utcnow", lambda: at)
    system.hass.states.set(GRID_SENSOR, 300)

    await co._async_tick(None)
    assert co.active_policy == POLICY_DYNAMIC_CHARGE

    # past 50 %, the amount not yet in: still buying - it is not a level
    system.hass.states.set(system.soc(0), 55.0)
    system.hass.states.set(system.soc(1), 55.0)
    await co._async_tick(None)
    assert co.active_policy == POLICY_DYNAMIC_CHARGE

    co.test_done[0] = 14.0
    await co._async_tick(None)
    assert co.active_policy != POLICY_DYNAMIC_CHARGE


async def test_with_a_charging_sensor_it_counts_what_the_sensor_measured(build_system):
    """Sun included: the sensor sees every kWh that goes in."""
    system = build_system(charge_power=True, **{"full_charge_minutes": 240})
    co = system.coordinator
    co.buy_ceiling_min = 50.0
    co._roll_periods()
    co.periods["day"]["charged_wh"] = 9_000.0

    assert co.floor_done_kwh() == pytest.approx(9.0)


async def test_without_one_it_counts_what_it_commanded(build_system, monkeypatch):
    """No sensor, no sun counted - but the purchases are, or the floor would
    buy to the top every day."""

    clock = [1_000_000.0]
    monkeypatch.setattr(coordinator_module.time, "time", lambda: clock[0])
    system = build_system(grid=7000, **{"full_charge_minutes": 240})
    co = system.coordinator
    co.buy_ceiling_min = 50.0
    for cfg in co._units:
        co.unit_status[cfg.name].online = True
        co.unit_status[cfg.name].target = -3500
    co._accumulate_charge(7000.0)
    clock[0] += 60.0
    co._accumulate_charge(7000.0)

    assert co.floor_done_kwh() == pytest.approx(7.0 / 60.0, rel=0.01)


async def test_the_count_survives_a_restart(build_system):
    system = build_system()
    co = system.coordinator
    today = coordinator_module.dt_util.as_local(coordinator_module.dt_util.utcnow()).date().isoformat()
    co._floor_day = {"day": today, "wh": 5200.0}
    stored = co._state_to_save()

    fresh = build_system()
    fresh.coordinator._store.data = stored
    await fresh.coordinator._async_restore()

    assert fresh.coordinator.floor_done_kwh() == pytest.approx(5.2)


def test_yesterday_s_count_does_not_count_today(build_system):
    co = build_system().coordinator
    co.buy_ceiling_min = 50.0
    co._floor_day = {"day": "2000-01-01", "wh": 14_000.0}

    assert co.floor_done_kwh() == 0.0

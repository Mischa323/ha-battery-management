"""The day goal: an amount into the packs each day, bought where it is cheap.

Asked for on 19 October as "buy at least" read as an amount - "hij moet gewoon
50 % opgeladen zijn over de hele dag ... als hij in de avond geen 50 % haalt is
dat geen probleem" - and split off on 21 October into its own slider, after
that version chased the amount into the evening: at 21:45, at EUR 0.343, for
kWh the packs had had no room for at noon, with tomorrow's afternoon cheaper.
"Eén gewoon vast, de ander kijkt slim": the minimum is the fixed one
(`test_buy_floor.py`), this is the one that looks at prices.

So the goal is bought, whether or not the margin says it pays, in slots that
are among the day's cheapest for the whole goal, past ones included - what did
not fit while those went by is let go - and among the cheapest of the next 24
hours for what is left, so a cheaper tomorrow wins over a dear evening.
"""
from __future__ import annotations

from datetime import timedelta

import pytest

from custom_components.battery_management import coordinator as coordinator_module
from custom_components.battery_management.const import POLICY_DYNAMIC_CHARGE, TRADE_OFF

from .conftest import GRID_SENSOR
from .test_trade_fill import AFTERNOON, afternoon, prices  # noqa: F401
from .test_trade_sell import trading  # noqa: F401

#: 18 October in miniature: nothing pays - 13:00-15:00 at 0.338, 0.39 otherwise
DEAR_DAY = dict(cheap=(13, 14), cheap_at=0.338, peak=(), ordinary=0.39)


@pytest.fixture
def day(afternoon, monkeypatch):
    def _build(*, soc=30.0, goal=14.0, done_kwh=0.0, **today):
        system = afternoon(soc=(soc, soc), **(today or DEAR_DAY))
        co = system.coordinator
        co.trade_mode = TRADE_OFF
        co._battery_price = 8500.0
        co._battery_cycles = 10000
        co._price_margin = 0.03
        co.buy_ceiling_min = 0.0
        co.day_goal_kwh = goal
        done = [done_kwh]
        co.goal_done_kwh = lambda: done[0]
        co.test_done = done
        monkeypatch.setattr(co, "_solar_headroom_ceiling", lambda: 95.0)
        return system

    return _build


def bought(system):
    return [h for h in system.coordinator.plan()["hours"] if h["buy"]]


def at(monkeypatch, moment):
    monkeypatch.setattr(coordinator_module.dt_util, "utcnow", lambda: moment)


def goal_slots(co):
    return co._goal_slots(co._price_forecast(), coordinator_module.dt_util.utcnow())


def test_a_day_where_nothing_pays_still_puts_it_in(day):
    """14 kWh at 7 kW is two hours: the two cheapest, whatever they cost."""
    picked = bought(day())

    assert len(picked) == 2
    assert {h["price"] for h in picked} == {0.338}


def test_what_went_in_today_counts_sun_included(day):
    """13.1 kWh in, as on 19 October: one quarter to go, not six kWh."""
    co = day(soc=28.0, done_kwh=13.1).coordinator

    assert co.goal_remaining_kwh() == pytest.approx(0.9)
    assert len(bought(day(soc=28.0, done_kwh=13.1))) == 1


def test_once_in_the_evening_level_does_not_matter(day):
    assert bought(day(soc=20.0, done_kwh=14.0)) == []


def test_never_more_than_there_is_room_for(day):
    system = day(soc=90.0)
    system.coordinator.buy_ceiling_max = 95.0

    assert system.coordinator.goal_remaining_kwh() == pytest.approx(1.4)


def test_without_a_goal_nothing_is_bought(day):
    assert bought(day(goal=0.0)) == []


def test_what_did_not_fit_is_not_chased_into_the_evening(day, monkeypatch):
    """21 October: the day's cheap hours went by with the packs nearly full,
    and in the evening, drawn down, they had room again. The goal's slots
    were the cheap ones, and those are gone - 21:00 at 0.343 is not one."""
    system = day(soc=50.0, done_kwh=10.7, cheap=(13, 14), cheap_at=0.30,
                 ordinary=0.39, overrides={21: 0.343, 22: 0.343})
    at(monkeypatch, AFTERNOON.replace(hour=20, minute=55))

    assert system.coordinator.goal_remaining_kwh() == pytest.approx(3.3)
    assert goal_slots(system.coordinator) == []


def test_ranked_over_the_whole_day_past_slots_included(day, monkeypatch):
    """At 14:00 the 13:00 hour is gone but still one of the day's two
    cheapest; 14:00 is the other, and the goal buys only that one."""
    system = day(cheap=(13, 14), cheap_at=0.30, ordinary=0.39, overrides={20: 0.32})
    at(monkeypatch, AFTERNOON.replace(hour=14))

    slots = goal_slots(system.coordinator)
    assert slots
    assert {s.start.hour for s in slots} == {14}


def test_a_cheaper_tomorrow_wins_over_a_dear_evening(day, monkeypatch):
    """Tomorrow's prices are out: its afternoon at 0.20 beats tonight's 0.33,
    so tonight's slots - among today's cheapest - are let go."""
    system = day(cheap=(), ordinary=0.39, overrides={22: 0.33, 23: 0.33, 37: 0.20, 38: 0.20})
    at(monkeypatch, AFTERNOON.replace(hour=15))

    assert goal_slots(system.coordinator) == []


def test_without_a_cheaper_tomorrow_the_evening_it_is(day, monkeypatch):
    system = day(cheap=(), ordinary=0.39, overrides={22: 0.33, 23: 0.33})
    at(monkeypatch, AFTERNOON.replace(hour=15))

    assert {s.start.hour for s in goal_slots(system.coordinator)} == {22, 23}


def test_the_plan_says_how_much_and_how_much_is_in(day):
    goal = day(done_kwh=4.0).coordinator.plan()["goal"]

    assert goal["kwh"] == pytest.approx(14.0)
    assert goal["done_kwh"] == pytest.approx(4.0)
    assert goal["remaining_kwh"] == pytest.approx(10.0)
    assert len(goal["hours"]) == 2


def test_no_goal_no_block(day):
    assert day(goal=0.0).coordinator.plan()["goal"] is None


async def test_the_tick_buys_until_the_day_s_amount_is_in(day, monkeypatch):
    system = day(soc=40.0, done_kwh=12.0)
    co = system.coordinator
    first = goal_slots(co)[0]
    at(monkeypatch, first.start + timedelta(minutes=1))
    system.hass.states.set(GRID_SENSOR, 300)

    await co._async_tick(None)
    assert co.active_policy == POLICY_DYNAMIC_CHARGE
    assert co.tick_log[-1]["buy_why"] == "day_goal"

    # past any level, the amount not yet in: still buying
    system.hass.states.set(system.soc(0), 55.0)
    system.hass.states.set(system.soc(1), 55.0)
    await co._async_tick(None)
    assert co.active_policy == POLICY_DYNAMIC_CHARGE
    assert co.tick_log[-1]["buy_why"] == "day_goal"

    co.test_done[0] = 14.0
    await co._async_tick(None)
    assert co.active_policy != POLICY_DYNAMIC_CHARGE
    assert co.tick_log[-1]["buy_why"] == "goal_done"


async def test_the_minimum_and_the_goal_together(day, monkeypatch):
    """Both set: the goal's slot fills on past the minimum, until the amount is in."""
    system = day(soc=40.0, done_kwh=12.0)
    co = system.coordinator
    co.buy_ceiling_min = 50.0
    first = goal_slots(co)[0]
    at(monkeypatch, first.start + timedelta(minutes=1))
    system.hass.states.set(GRID_SENSOR, 300)
    system.hass.states.set(system.soc(0), 60.0)
    system.hass.states.set(system.soc(1), 60.0)

    await co._async_tick(None)

    assert co.active_policy == POLICY_DYNAMIC_CHARGE
    assert co.tick_log[-1]["buy_why"] == "day_goal"


async def test_the_goal_survives_a_restart(build_system):
    system = build_system()
    await system.coordinator.async_set_day_goal(14.0)
    stored = system.coordinator._state_to_save()

    fresh = build_system()
    fresh.coordinator._store.data = stored
    await fresh.coordinator._async_restore()

    assert fresh.coordinator.day_goal_kwh == 14.0


# -- what has gone in -------------------------------------------------------


async def test_with_a_charging_sensor_it_counts_what_the_sensor_measured(build_system):
    """Sun included: the sensor sees every kWh that goes in."""
    system = build_system(charge_power=True, **{"full_charge_minutes": 240})
    co = system.coordinator
    co._roll_periods()
    co.periods["day"]["charged_wh"] = 9_000.0

    assert co.goal_done_kwh() == pytest.approx(9.0)


async def test_without_one_it_counts_what_it_commanded(build_system, monkeypatch):
    """No sensor, no sun counted - but the purchases are, or the goal would
    buy to the top every day."""
    clock = [1_000_000.0]
    monkeypatch.setattr(coordinator_module.time, "time", lambda: clock[0])
    system = build_system(grid=7000, **{"full_charge_minutes": 240})
    co = system.coordinator
    for cfg in co._units:
        co.unit_status[cfg.name].online = True
        co.unit_status[cfg.name].target = -3500
    co._accumulate_charge(7000.0)
    clock[0] += 60.0
    co._accumulate_charge(7000.0)

    assert co.goal_done_kwh() == pytest.approx(7.0 / 60.0, rel=0.01)


async def test_the_count_survives_a_restart(build_system):
    system = build_system()
    co = system.coordinator
    today = coordinator_module.dt_util.as_local(coordinator_module.dt_util.utcnow()).date().isoformat()
    co._goal_day = {"day": today, "wh": 5200.0}
    stored = co._state_to_save()

    fresh = build_system()
    fresh.coordinator._store.data = stored
    await fresh.coordinator._async_restore()

    assert fresh.coordinator.goal_done_kwh() == pytest.approx(5.2)


async def test_the_count_kept_by_the_amount_floor_carries_over(build_system):
    """#31 stored it as `floor_day`; an upgrade mid-day keeps the day's count."""
    today = coordinator_module.dt_util.as_local(coordinator_module.dt_util.utcnow()).date().isoformat()
    fresh = build_system()
    fresh.coordinator._store.data = {"floor_day": {"day": today, "wh": 3000.0}}
    await fresh.coordinator._async_restore()

    assert fresh.coordinator.goal_done_kwh() == pytest.approx(3.0)


def test_yesterday_s_count_does_not_count_today(build_system):
    co = build_system().coordinator
    co._goal_day = {"day": "2000-01-01", "wh": 14_000.0}

    assert co.goal_done_kwh() == 0.0


def test_the_slider(build_system):
    number = pytest.importorskip(
        "custom_components.battery_management.number",
        reason="needs a real Home Assistant for the number platform",
    )
    system = build_system()
    entity = number.DayGoalNumber(system.coordinator, system.entry)

    assert entity.native_value == 0.0
    assert entity.native_unit_of_measurement == "kWh"


async def test_the_goal_buys_past_the_sun_s_ceiling(day, monkeypatch):
    """Binding: the sun's room is the margin's business, not the goal's."""
    system = day(soc=55.0, done_kwh=4.0)
    co = system.coordinator
    monkeypatch.setattr(co, "_solar_headroom_ceiling", lambda: 50.0)
    first = goal_slots(co)[0]
    at(monkeypatch, first.start + timedelta(minutes=1))
    system.hass.states.set(GRID_SENSOR, 300)

    await co._async_tick(None)

    assert co.active_policy == POLICY_DYNAMIC_CHARGE
    assert co.tick_log[-1]["buy_why"] == "day_goal"

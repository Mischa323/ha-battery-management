"""Keeping the charge for the coming peak, and the forecast that decides it.

Asked for on 1 November: the packs carried the house through the afternoon at
EUR 0.40 and met the EUR 0.49 peak empty. Grid-zero gives the house whatever it
asks whenever it asks; nothing weighed *when* a stored kWh is worth most. Now
the plan walks the packs' charge forward slot by slot, and when that walk runs
them dry inside the coming peak, the cheapest slots before it - at least the
price margin below the peak - leave the house to the grid, so the charge is
there when it is dear.

Prices below: 13:00-17:00 at 0.40, the 18:00-22:00 peak at 0.45, 0.42
otherwise. Buying at 0.40 costs 0.40 / 0.88 + 0.03 = 0.485 out of the packs -
more than the peak - so nothing is bought and only the hold can help.
"""
from __future__ import annotations

from datetime import timedelta

import pytest

from custom_components.battery_management import coordinator as coordinator_module
from custom_components.battery_management.const import (
    POLICY_DYNAMIC_CHARGE,
    POLICY_SAVE_FOR_PEAK,
    TRADE_OFF,
)

from .conftest import GRID_SENSOR
from .test_trade_fill import AFTERNOON, afternoon, prices  # noqa: F401
from .test_trade_sell import trading  # noqa: F401

AFTERNOON_AT_040 = dict(cheap=(), ordinary=0.42, peak=(18, 19, 20, 21), peak_at=0.45,
                        overrides={13: 0.40, 14: 0.40, 15: 0.40, 16: 0.40})


@pytest.fixture
def house(afternoon):
    """13:00, a house drawing 2 kW every hour, no minimum, no goal, no trading."""

    def _build(*, soc=30.0, draw=2000.0, **day):
        system = afternoon(soc=(soc, soc), **(day or AFTERNOON_AT_040))
        co = system.coordinator
        co.trade_mode = TRADE_OFF
        co._battery_price = 8500.0
        co._battery_cycles = 10000
        co._price_margin = 0.03
        co.buy_ceiling_min = 0.0
        co.day_goal_kwh = 0.0
        co.draw_profile = {hour: draw for hour in range(24)}
        system.hass.states.set(GRID_SENSOR, draw)
        return system

    return _build


def forecast(system):
    co = system.coordinator
    return co.soc_forecast(co._price_forecast(), coordinator_module.dt_util.utcnow())


def holds(system):
    co = system.coordinator
    return co.peak_holds(co._price_forecast(), coordinator_module.dt_util.utcnow())


def hours_of(keys):
    return sorted(int(key[11:13]) for key in keys)


# -- the forecast -------------------------------------------------------------


def test_the_charge_runs_down_by_what_the_house_draws(house):
    """30 % of 28 kWh above a 5 % floor is 7 kWh; at 2 kW, through 94 % on
    the way out, that is gone a little after 16:00."""
    rows = forecast(house())["slots"]

    assert rows[0]["soc"] < 30.0
    assert all(a["soc"] >= b["soc"] for a, b in zip(rows, rows[1:]))
    empty = next(r for r in rows if r["short"] > 0)
    assert empty["slot"].start.hour == 16


def test_what_it_cannot_give_is_short(house):
    rows = forecast(house())["slots"]
    peak = [r for r in rows if r["slot"].start.hour in (18, 19, 20, 21)
            and r["slot"].start.date() == AFTERNOON.date()]

    assert sum(r["short"] for r in peak) == pytest.approx(8.0)


def test_a_held_slot_gives_nothing_and_keeps_the_charge(house):
    system = house()
    co = system.coordinator
    first = co._price_forecast()
    now = coordinator_module.dt_util.utcnow()
    kept = co.soc_forecast(first, now, frozenset({coordinator_module._slot_key(
        coordinator_module.slot_at(first, now))}))["slots"]

    assert kept[0]["given"] == 0.0
    assert kept[1]["soc"] > forecast(system)["slots"][1]["soc"]


def test_no_size_no_forecast(house):
    system = house()
    system.coordinator._full_charge_minutes = 0

    assert forecast(system) is None


# -- the hold -----------------------------------------------------------------


def test_the_afternoon_is_kept_for_the_peak(house):
    """The packs would be dry by 16:00 with the whole peak still to come: the
    afternoon at 0.40 goes to the grid, the charge to the evening at 0.45 -
    cheapest first, and 17:00 at 0.42 too: exactly the margin below the peak,
    and the peak still short without it."""
    assert hours_of(holds(house())) == [13, 14, 15, 16, 17]


def test_not_when_the_packs_reach_the_far_side_anyway(house):
    assert holds(house(soc=95.0)) == frozenset()


def test_not_when_the_peak_is_too_close_in_price(house):
    """A 0.42 peak is two cents above 0.40: under the three-cent margin."""
    day = dict(AFTERNOON_AT_040, peak_at=0.42)

    assert holds(house(**day)) == frozenset()


def test_not_inside_the_peak_itself(house, monkeypatch):
    system = house()
    moment = AFTERNOON.replace(hour=19)
    monkeypatch.setattr(coordinator_module.dt_util, "utcnow", lambda: moment)

    assert all(key[11:13] not in ("18", "19", "20", "21") or key[:10] != AFTERNOON.date().isoformat()
               for key in holds(system))


def test_only_in_dynamic_mode(house):
    system = house()
    system.coordinator.mode = "grid_zero"

    assert holds(system) == frozenset()


async def test_the_tick_does_not_discharge_in_a_held_slot(house):
    system = house()

    await system.coordinator._async_tick(None)

    assert system.coordinator.active_policy == POLICY_SAVE_FOR_PEAK
    assert system.coordinator.setpoint <= 0
    assert "bewaart voor de piek van 18:00" in system.coordinator.tick_log[-1]["doing"]


async def test_without_a_hold_it_follows_the_meter(house):
    system = house(soc=95.0)

    await system.coordinator._async_tick(None)

    assert system.coordinator.active_policy != POLICY_SAVE_FOR_PEAK
    assert system.coordinator.setpoint > 0


# -- what the plan says -------------------------------------------------------


def test_the_plan_carries_the_line_the_low_point_and_the_kept_hours(house):
    plan = house().coordinator.plan()
    block = plan["forecast"]

    assert len(block["soc"]) >= 24
    assert block["peak"]["start"].startswith(AFTERNOON.replace(hour=18).isoformat()[:13])
    assert len(block["hold_hours"]) == 5
    assert [h for h in plan["hours"] if h["hold"]]
    assert "_holds" not in block
    # what a card needs to fetch the history beside it
    assert [e["kwh"] for e in block["soc_entities"]] == [pytest.approx(14.0)] * 2
    assert all(e["entity"].startswith("sensor.") for e in block["soc_entities"])


def test_with_the_hold_the_forecast_is_held_too(house):
    """The line the card draws is the one with the hold in it."""
    block = house().coordinator.plan()["forecast"]
    at_peak = next(r for r in block["soc"] if r["start"].startswith(
        AFTERNOON.replace(hour=18).isoformat()[:13]))

    assert at_peak["soc"] > 20.0


async def test_the_trace_says_what_is_kept(house):
    system = house()
    system.coordinator.plan()

    await system.coordinator._async_tick(None)
    row = system.coordinator.tick_log[-1]

    assert row["plan_hold"] == "13:00-18:00"


def test_the_cheapest_are_kept_even_where_the_packs_would_be_dry(house):
    """At 20 % the packs are dry by 15:00, and 15:00-17:00 is the cheapest of
    the afternoon. Kept at 13:00 instead, the charge would only be spent at
    15:00 on its way to the peak - the cheap hours are the ones to keep."""
    day = dict(AFTERNOON_AT_040, overrides={13: 0.41, 14: 0.41, 15: 0.39, 16: 0.39})
    kept = hours_of(holds(house(soc=20.0, **day)))

    assert {15, 16} <= set(kept)


def test_a_purchase_before_the_peak_is_in_the_forecast(house):
    """15:00 at 0.20 pays for the peak: the packs fill there, and the line says so."""
    day = dict(AFTERNOON_AT_040, overrides={13: 0.40, 14: 0.40, 15: 0.20, 16: 0.40})
    rows = forecast(house(**day))["slots"]
    at = {r["slot"].start.hour: r["soc"] for r in rows if r["slot"].start.date() == AFTERNOON.date()}

    assert at[15] > at[14]


def test_the_peak_is_one_stretch(house):
    """18:00-20:00 and 21:00-23:00 dear, 20:00 well below them: the peak the
    hold is for ends at 20:00."""
    day = dict(cheap=(), ordinary=0.38, peak=(18, 19, 21, 22), peak_at=0.45,
               overrides={20: 0.39})
    system = house(**day)
    co = system.coordinator
    run = co._peak_run(co._price_forecast(), coordinator_module.dt_util.utcnow())

    assert [s.start.hour for s in run] == [18, 19]


def test_a_dip_inside_the_peak_is_still_the_peak(house):
    """1 November: 19:30 at 0.4873 between quarters at 0.489. Within the price
    margin of the peak, it is the peak."""
    day = dict(cheap=(), ordinary=0.38, peak=(18, 19, 21, 22), peak_at=0.45,
               overrides={20: 0.44})
    system = house(**day)
    co = system.coordinator
    run = co._peak_run(co._price_forecast(), coordinator_module.dt_util.utcnow())

    assert [s.start.hour for s in run] == [18, 19, 20, 21, 22]


def test_no_more_than_the_peak_is_short_of(house):
    """At 50 % the packs give 11.8 kWh: 10 for the afternoon leaves the peak
    6.2 short. Four afternoon hours kept cover it; 17:00 is not needed."""
    assert hours_of(holds(house(soc=50.0))) == [13, 14, 15, 16]


# -- in the peak, the night's buying is in the plan -----------------------------

NIGHT_AND_MORNING = dict(
    AFTERNOON_AT_040,
    overrides={**AFTERNOON_AT_040["overrides"],
               **{24 + h: 0.20 for h in (2, 3, 4)}, **{24 + h: 0.60 for h in (7, 8)}},
)


@pytest.fixture
def in_the_peak(house, monkeypatch):
    """19:00 in the 0.45 evening peak; the night at 0.20, a 0.60 morning peak."""

    def _build(**kwargs):
        system = house(**{"soc": 40.0, **NIGHT_AND_MORNING, **kwargs})
        monkeypatch.setattr(coordinator_module.dt_util, "utcnow",
                            lambda: AFTERNOON.replace(hour=19))
        system.hass.states.set(GRID_SENSOR, 2000)
        return system

    return _build


def test_in_the_peak_the_forecast_buys_in_the_night_after_it(in_the_peak):
    """8 November: in the evening peak the plan bought nothing - the next dear
    slot was the next quarter - and the forecast walked on as if the night
    would not buy either. The plan's own buying still waits for the peak to
    pass; the forecast expects the night."""
    co = in_the_peak().coordinator
    now = coordinator_module.dt_util.utcnow()
    slots = co._price_forecast()

    expected = hours_of(co._planned_flows(slots, now)[0])

    assert {2, 3, 4} <= set(expected)
    assert not {19, 20, 21} & set(expected)
    assert co._buy_slots(slots, now) == []


def test_so_the_morning_is_not_short(in_the_peak):
    rows = forecast(in_the_peak())["slots"]
    morning = [r for r in rows if r["slot"].start.hour in (7, 8)
               and r["slot"].start.date() > AFTERNOON.date()]

    assert sum(r["short"] for r in morning) == pytest.approx(0.0)


def test_so_the_evening_is_not_kept_for_the_morning(in_the_peak):
    """With the night's purchase in the forecast the morning is covered: no
    charge held back in tonight's peak at 0.45."""
    assert holds(in_the_peak()) == frozenset()


async def test_the_tick_still_buys_nothing_in_the_peak(in_the_peak):
    co = in_the_peak().coordinator

    await co._async_tick(None)

    assert co.active_policy != POLICY_DYNAMIC_CHARGE
    assert co.setpoint > 0

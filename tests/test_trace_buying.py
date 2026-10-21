"""The trace says what it is doing, and why it bought or did not.

Asked for on 19 October: make the trace clearer about what it does. Every
question about buying so far - "why only a quarter", "why not today", "why
50 % again tonight" - had to be answered by rebuilding the plan, the floor and
the house's draw from prices and SoC afterwards. Now each row carries them,
and a sentence in the owner's language opens it.
"""
from __future__ import annotations

import csv
import os
from datetime import timedelta

import pytest

from custom_components.battery_management import coordinator as coordinator_module
from custom_components.battery_management.const import CONF_TRACE
from custom_components.battery_management.trace import Trace

from .conftest import GRID_SENSOR
from .test_day_goal import DEAR_DAY, day  # noqa: F401
from .test_trade_fill import afternoon, prices  # noqa: F401
from .test_trade_sell import trading  # noqa: F401


async def at_first_goal_slot(system, monkeypatch):
    co = system.coordinator
    first = co._goal_slots(co._price_forecast(), coordinator_module.dt_util.utcnow())[0]
    at = first.start + timedelta(minutes=1)
    monkeypatch.setattr(coordinator_module.dt_util, "utcnow", lambda: at)
    system.hass.states.set(GRID_SENSOR, 300)
    await co._async_tick(None)
    return co.tick_log[-1]


async def test_buying_for_the_day_s_amount_says_so(day, monkeypatch):
    system = day(soc=40.0, done_kwh=4.0)

    row = await at_first_goal_slot(system, monkeypatch)

    assert row["buy_now"] is True
    assert row["buy_why"] == "day_goal"
    assert row["buy_slot"] == "goal"
    assert row["goal_kwh"] == pytest.approx(14.0)
    assert row["goal_done_kwh"] == pytest.approx(4.0)
    assert row["goal_left_kwh"] == pytest.approx(10.0)
    assert row["doing"].startswith("koopt ")
    assert "dagdoel (nog 10.0 kWh)" in row["doing"]


async def test_once_it_is_in_the_goal_s_slot_says_why_it_stopped(day, monkeypatch):
    system = day(soc=40.0, done_kwh=4.0)
    row = await at_first_goal_slot(system, monkeypatch)
    assert row["buy_now"] is True

    system.coordinator.test_done[0] = 14.0
    await system.coordinator._async_tick(None)
    row = system.coordinator.tick_log[-1]

    assert row["buy_now"] is False
    assert row["buy_why"] == "goal_done"


async def test_what_a_kwh_bought_now_costs_out_of_the_packs(day, monkeypatch):
    """0.338 through the 88 % round trip, plus 8500 / (10000 x 28) of wear."""
    row = await at_first_goal_slot(day(soc=40.0), monkeypatch)

    assert row["buy_cost_eur_kwh"] == pytest.approx(0.338 / 0.88 + 8500 / 280000, abs=1e-3)
    assert row["price_margin_eur_kwh"] == 0.03


async def test_outside_a_buy_slot_it_says_not_a_buy_slot(day):
    system = day(soc=40.0, done_kwh=14.0)

    await system.coordinator._async_tick(None)
    row = system.coordinator.tick_log[-1]

    assert row["buy_now"] is False
    assert row["buy_why"] == "not_a_buy_slot"


async def test_the_plan_as_it_stood_is_on_the_row(day):
    system = day(soc=40.0)
    co = system.coordinator
    co.plan()

    await co._async_tick(None)
    row = co.tick_log[-1]

    expected = coordinator_module._ranges(h for h in co.plan()["hours"] if h["buy"] and not h["past"])
    assert row["plan_buy"]
    assert row["plan_buy"] == expected


def test_quarters_in_a_row_are_one_range(monkeypatch):
    from datetime import datetime, timezone

    now = datetime(2026, 10, 19, 10, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(coordinator_module.dt_util, "utcnow", lambda: now)
    monkeypatch.setattr(coordinator_module.dt_util, "as_local", lambda v: v)

    def q(h, m):
        start = now.replace(hour=h, minute=m)
        return {"start": start.isoformat(), "end": (start + timedelta(minutes=15)).isoformat()}

    hours = [q(13, 0), q(13, 15), q(13, 30), q(22, 15)]
    tomorrow = (now + timedelta(days=1)).replace(hour=3)
    hours.append({"start": tomorrow.isoformat(), "end": (tomorrow + timedelta(minutes=15)).isoformat()})

    assert coordinator_module._ranges(hours) == "13:00-13:45 22:15-22:30 20-10 03:00-03:15"
    assert coordinator_module._ranges([]) == ""


async def test_grid_zero_says_what_goes_to_the_house(build_system):
    system = build_system(grid=500)

    await system.coordinator._async_tick(None)
    row = system.coordinator.tick_log[-1]

    assert row["doing"] == "levert 500 W aan het huis"
    assert row["buy_now"] is None


async def test_dry_run_says_it_is_one(build_system):
    system = build_system(grid=500, dry_run=True)

    await system.coordinator._async_tick(None)

    assert system.coordinator.tick_log[-1]["doing"] == "proefdraai: levert 500 W aan het huis"


async def test_the_house_is_the_meter_plus_the_packs(build_system):
    system = build_system(grid=500)
    co = system.coordinator
    co._other_controller_power = lambda: 300.0

    await co._async_tick(None)

    assert co.tick_log[-1]["house_w"] == 800


async def test_the_sentence_opens_the_row_on_disk(build_system, tmp_path):
    system = build_system(grid=500, **{CONF_TRACE: True})
    system.coordinator._trace = Trace(str(tmp_path), keep_days=14)

    await system.coordinator._async_tick(None)
    system.coordinator._trace.flush()

    (name,) = os.listdir(tmp_path)
    with open(os.path.join(tmp_path, name), encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        row = next(reader)
    assert reader.fieldnames[:2] == ["at", "doing"]
    assert row["doing"] == "levert 500 W aan het huis"
    assert "buy_why" in reader.fieldnames and "plan_buy" in reader.fieldnames


async def test_still_the_day_s_amount_on_the_slot_s_next_tick(day, monkeypatch):
    """The second tick finds the purchase under way - it is still for the goal."""
    system = day(soc=40.0, done_kwh=4.0)
    await at_first_goal_slot(system, monkeypatch)

    await system.coordinator._async_tick(None)
    row = system.coordinator.tick_log[-1]

    assert row["buy_now"] is True
    assert row["buy_slot"] == "latched"
    assert row["buy_why"] == "day_goal"


async def test_a_goal_slot_that_pays_anyway_says_it_pays(day, monkeypatch):
    """0.21 against a 0.45 evening clears the margin: the goal is not why."""
    system = day(soc=40.0, cheap=(13, 14, 15, 16), cheap_at=0.21,
                 peak=(18, 19, 20, 21), ordinary=0.30)

    row = await at_first_goal_slot(system, monkeypatch)

    assert row["buy_now"] is True
    assert row["buy_why"] == "pays"
    assert row["doing"].startswith("koopt ") and "loont" in row["doing"]


async def test_finishing_a_purchase_keeps_the_reason_it_started_for(afternoon):
    """Bought to sell tonight, and the second tick of the slot says so too -
    not just "finishing the slot"."""
    system = afternoon()
    co = system.coordinator

    await co._async_tick(None)
    assert co.tick_log[-1]["buy_why"] == "trade_fill"
    await co._async_tick(None)
    row = co.tick_log[-1]

    assert row["buy_slot"] == "latched"
    assert row["buy_why"] == "trade_fill"
    assert "om vanavond te verkopen" in row["doing"]

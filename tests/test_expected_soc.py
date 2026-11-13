"""What was expected, kept to set beside what came.

Asked for on 10 November: "waarom kan ik net als een entiteit in HA niet de
grafiek van de verwacht accu niveau openen, en kan je in de verwachting ook de
daadwerkelijke status toevoegen zodat we kunnen zien of het onder of boven de
verwachting zit". Each slot's charge is taken down once, the first time it is
within three hours, and an Expected charge sensor carries the one for now -
so Home Assistant keeps the expected line as history, like any entity.
"""
from __future__ import annotations

from datetime import timedelta

import pytest

from custom_components.battery_management import coordinator as coordinator_module
from custom_components.battery_management.const import EXPECTED_LEAD_HOURS

from .test_save_for_peak import AFTERNOON, AFTERNOON_AT_040, house  # noqa: F401
from .test_trade_fill import afternoon, prices  # noqa: F401
from .test_trade_sell import trading  # noqa: F401


def key(hour, day=AFTERNOON):
    return day.replace(hour=hour).astimezone(coordinator_module.timezone.utc).isoformat()


def test_the_next_three_hours_are_taken_down(house):
    co = house().coordinator
    co.plan()

    hours = sorted(int(k[11:13]) for k in co.expected_log)
    assert hours[0] == 13 and hours[-1] == 13 + EXPECTED_LEAD_HOURS


def test_and_never_overwritten(house, monkeypatch):
    """What was expected three hours before, not a minute before."""
    system = house()
    co = system.coordinator
    co.plan()
    then = co.expected_log[key(16)]

    monkeypatch.setattr(coordinator_module.dt_util, "utcnow",
                        lambda: AFTERNOON.replace(hour=15))
    system.hass.states.set(system.soc(0), 80.0)
    system.hass.states.set(system.soc(1), 80.0)
    co.plan()

    assert co.expected_log[key(16)] == then
    assert key(18) in co.expected_log


def test_the_plan_carries_it_beside_the_charge_now(house, monkeypatch):
    system = house()
    co = system.coordinator
    co.plan()
    monkeypatch.setattr(coordinator_module.dt_util, "utcnow",
                        lambda: AFTERNOON.replace(hour=15))
    block = co.plan()["forecast"]

    assert [r["start"][11:13] for r in block["expected"]] == ["13", "14", "15"]
    assert block["expected_now"] == co.expected_log[key(15)]
    assert block["actual_soc"] == pytest.approx(30.0)
    assert block["expected_lead_hours"] == EXPECTED_LEAD_HOURS


def test_two_days_are_kept(house):
    co = house().coordinator
    co.expected_log = {key(13, AFTERNOON - timedelta(days=3)): 50.0}

    co.plan()

    assert key(13, AFTERNOON - timedelta(days=3)) not in co.expected_log


async def test_it_survives_a_restart(house):
    system = house()
    system.coordinator.plan()
    stored = system.coordinator._state_to_save()

    fresh = house()
    fresh.coordinator._store.data = stored
    await fresh.coordinator._async_restore()

    assert fresh.coordinator.expected_log == system.coordinator.expected_log


def test_the_sensor(house):
    sensor = pytest.importorskip(
        "custom_components.battery_management.sensor",
        reason="needs a real Home Assistant for the sensor platform",
    )
    system = house()
    co = system.coordinator
    co.plan()
    entity = sensor.ExpectedSocSensor(co, system.entry)

    assert entity.native_value == co.expected_log[key(13)]
    attrs = entity.extra_state_attributes
    assert attrs["actual_soc"] == pytest.approx(30.0)
    assert attrs["deviation"] == pytest.approx(30.0 - co.expected_log[key(13)], abs=0.11)
    assert attrs["lead_hours"] == EXPECTED_LEAD_HOURS


def test_now_is_read_off_between_the_slot_before_and_this_one(house, monkeypatch):
    """Found on 13 November: what is taken down is the charge at a slot's
    end. Half way through 14:00-15:00 that is half way between the two."""
    co = house().coordinator
    co.expected_log = {key(13): 40.0, key(14): 30.0}
    moment = AFTERNOON.replace(hour=14, minute=30)
    monkeypatch.setattr(coordinator_module.dt_util, "utcnow", lambda: moment)

    assert co.expected_now() == pytest.approx(35.0)


def test_the_line_says_when_each_figure_is_for(house):
    block = house().coordinator.plan()["forecast"]
    first = block["soc"][0]

    assert first["start"][11:16] == "13:00" and first["at"][11:16] == "14:00"
    assert block["lowest"]["at"] > block["lowest"]["start"]
    assert all(row["at"] > row["start"] for row in block["expected"])


def test_the_plan_series_are_kept_out_of_the_recorder(house):
    """Found on 13 November: two days of quarters is some 50 kB of attributes
    and the recorder takes 16. What is left to it fits with room to spare."""
    import json

    sensor = pytest.importorskip(
        "custom_components.battery_management.sensor",
        reason="needs a real Home Assistant for the sensor platform",
    )
    plan = house().coordinator.plan()
    left = {k: v for k, v in plan.items() if k not in sensor.PlanSensor._unrecorded_attributes}

    assert {"hours", "forecast"} <= sensor.PlanSensor._unrecorded_attributes
    assert len(json.dumps(left, default=str)) < 16384 / 2

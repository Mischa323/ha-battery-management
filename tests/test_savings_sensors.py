"""The savings sensors, as Home Assistant sees them.

The arithmetic lives in `test_savings.py`. Pinned here: money is `TOTAL` with
a reset for the periods, never `TOTAL_INCREASING` (a day can come out
negative), and the state follows the footing of the day.
"""
from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from custom_components.battery_management import coordinator as coordinator_module

sensor = pytest.importorskip(
    "custom_components.battery_management.sensor",
    reason="needs a real Home Assistant for the sensor platform",
)


@pytest.fixture
def sensors(build_system, monkeypatch):
    moment = datetime(2026, 8, 20, 12, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(coordinator_module.dt_util, "now", lambda: moment)
    monkeypatch.setattr(coordinator_module.dt_util, "utcnow", lambda: moment)
    system = build_system(grid=1000)
    coordinator = system.coordinator
    coordinator.periods["day"].update(key="2026-08-20", saved_eur=1.234, saved_after_eur=2.345)
    coordinator.periods["month"].update(key="2026-08", saved_eur=12.5, saved_after_eur=20.0)
    coordinator.money.update(saved_eur=100.0, saved_after_eur=150.0)
    coordinator.money_since = "2026-06-01"
    return system, coordinator


def test_the_periods_read_on_today_s_footing(sensors):
    system, coordinator = sensors

    today = sensor.SavingsTodaySensor(coordinator, system.entry)
    month = sensor.SavingsThisMonthSensor(coordinator, system.entry)
    total = sensor.SavingsTotalSensor(coordinator, system.entry)

    assert today.native_value == 1.23
    assert month.native_value == 12.5
    assert total.native_value == 100.0

    coordinator._saldering_until = date(2026, 1, 1)
    assert today.native_value == 2.35
    assert total.native_value == 150.0


def test_money_can_go_down(sensors):
    system, coordinator = sensors

    for cls in (sensor.SavingsTodaySensor, sensor.SavingsThisMonthSensor, sensor.SavingsTotalSensor):
        entity = cls(coordinator, system.entry)
        assert entity.state_class == "total", cls.__name__
        assert entity.native_unit_of_measurement == "EUR"


def test_the_periods_declare_their_reset(sensors):
    system, coordinator = sensors

    month = sensor.SavingsThisMonthSensor(coordinator, system.entry)

    assert month.last_reset == coordinator.period_started_at("month")
    assert month.extra_state_attributes["total"]["saved_eur"] == 100.0


def test_the_payback_reads_the_owner_s_footing(sensors):
    system, coordinator = sensors
    coordinator._battery_price = 5000.0
    coordinator.money.update(counted_h=720.0, saved_eur=20.0, saved_after_eur=41.1)

    payback = sensor.PaybackSensor(coordinator, system.entry)

    assert payback.available is True
    assert payback.native_value == coordinator.payback()["years_without_saldering"]
    assert payback.native_unit_of_measurement == sensor.UnitOfTime.YEARS


def test_no_purchase_price_leaves_the_payback_unknown_not_unavailable(sensors):
    """Unknown, with its attributes: an unavailable entity loses them, and the
    card then could neither find the sensor nor say what it is waiting for."""
    system, coordinator = sensors
    coordinator._battery_price = 0.0

    payback = sensor.PaybackSensor(coordinator, system.entry)

    assert payback.available is True
    assert payback.native_value is None
    assert payback.extra_state_attributes["battery_price_eur"] == 0.0
    assert "years_to_go" in payback.extra_state_attributes


def test_the_trade_status_is_an_enum_of_its_states(sensors):
    system, coordinator = sensors

    status = sensor.TradeStatusSensor(coordinator, system.entry)

    assert status.native_value == "off"
    assert set(status.options) == {"off", "not_dynamic", "selling", "would_sell", "waiting"}
    assert "why" in status.extra_state_attributes

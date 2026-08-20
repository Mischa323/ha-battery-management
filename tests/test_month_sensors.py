"""The monthly charge counters, as Home Assistant sees them.

The arithmetic lives in `test_charge_energy.py`. What is pinned here is the
contract with Home Assistant's statistics engine, because getting it wrong is
the quiet kind of wrong: a `TOTAL` sensor whose value drops to nought on the
1st, without a `last_reset` moving with it, is read as a meter that has been
replaced. Nothing looks broken - the long-term sums are simply wrong, and only
a month later, on a figure nobody can check by eye.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from custom_components.battery_management import coordinator as coordinator_module

#: The stub in the root conftest is deliberately "just enough for
#: coordinator.py to import", and the entity platforms are well past that.
#: Asked of the whole module rather than of `homeassistant.components.sensor`,
#: so anything else it reaches for is covered by the same skip - and asked by
#: importing, because the stub registers packages with no `__spec__` and
#: `find_spec` raises on those instead of answering False. CI runs the suite
#: both stubbed and against a real Home Assistant; these run on the second pass.
sensor = pytest.importorskip(
    "custom_components.battery_management.sensor",
    reason="needs a real Home Assistant for the sensor platform",
)

ChargedThisMonthSensor = sensor.ChargedThisMonthSensor
ChargedFromGridThisMonthSensor = sensor.ChargedFromGridThisMonthSensor
ChargedTotalSensor = sensor.ChargedTotalSensor


@pytest.fixture
def sensors(build_system, monkeypatch):
    monkeypatch.setattr(
        coordinator_module.dt_util,
        "now",
        lambda: datetime(2026, 8, 20, 12, 0, tzinfo=timezone.utc),
    )
    system = build_system(grid=1000, charge_power=True)
    coordinator = system.coordinator
    coordinator.month_key = "2026-08"
    coordinator.month_charged_wh = 4321.0
    coordinator.month_charged_grid_wh = 1000.0
    coordinator.charged_wh = 98765.0
    coordinator.charged_grid_wh = 20000.0
    coordinator.month_history = {
        "2026-06": {"charged_kwh": 210.5, "grid_kwh": 60.25},
        "2026-07": {"charged_kwh": 180.0, "grid_kwh": 12.0},
    }
    return system, coordinator


def test_the_month_reads_in_kwh(sensors):
    system, coordinator = sensors

    total = ChargedThisMonthSensor(coordinator, system.entry)
    grid = ChargedFromGridThisMonthSensor(coordinator, system.entry)

    assert total.native_value == 4.321
    assert grid.native_value == 1.0


def test_the_lifetime_counter_is_left_alone(sensors):
    """The monthly pair is a second reading of the same meter, not a
    replacement: the Energy dashboard wants the one that only ever goes up."""
    system, coordinator = sensors

    lifetime = ChargedTotalSensor(coordinator, system.entry)

    assert lifetime.native_value == 98.765
    assert lifetime.state_class == "total_increasing"


def test_the_monthly_pair_declares_its_reset(sensors):
    """`TOTAL` plus a `last_reset` on the 1st, or the statistics go wrong."""
    system, coordinator = sensors

    for sensor in (
        ChargedThisMonthSensor(coordinator, system.entry),
        ChargedFromGridThisMonthSensor(coordinator, system.entry),
    ):
        assert sensor.state_class == "total"
        reset = sensor.last_reset
        assert reset is not None
        assert (reset.year, reset.month, reset.day) == (2026, 8, 1)
        assert reset.hour == 0 and reset.tzinfo is not None


def test_the_reset_moves_with_the_month(sensors):
    system, coordinator = sensors
    sensor = ChargedThisMonthSensor(coordinator, system.entry)
    assert sensor.last_reset.month == 8

    coordinator.month_key = "2026-09"

    assert sensor.last_reset.month == 9


def test_the_closed_months_ride_along(sensors):
    """Where the history is actually read from."""
    system, coordinator = sensors

    attributes = ChargedThisMonthSensor(coordinator, system.entry).extra_state_attributes

    assert attributes["month"] == "2026-08"
    assert list(attributes["history"]) == ["2026-06", "2026-07"]
    assert attributes["history"]["2026-06"]["charged_kwh"] == 210.5
    # published rather than left to be subtracted: 210.5 - 60.25
    assert attributes["history"]["2026-06"]["solar_kwh"] == 150.25


def test_the_sun_share_is_the_remainder_not_a_second_count(sensors):
    """Two independent counters would drift apart within a day, and then the
    split would be a split of something that is not the whole."""
    system, coordinator = sensors

    attributes = ChargedFromGridThisMonthSensor(
        coordinator, system.entry
    ).extra_state_attributes

    assert attributes["charged_from_solar_kwh"] == pytest.approx(3.321)


def test_nothing_is_published_without_a_charge_sensor(build_system):
    """Unavailable rather than nought - the same reasoning as the lifetime
    pair. A graph flat on the floor must not be able to mean "nobody is
    counting" as well as "nothing was charged"."""
    system = build_system(grid=1000)

    sensor = ChargedThisMonthSensor(system.coordinator, system.entry)

    assert sensor.available is False


def test_no_month_yet_means_no_reset_to_declare(build_system):
    """Before the first tick there is no month, and inventing one would date
    the reset to a month the packs were never counted in."""
    system = build_system(grid=1000, charge_power=True)
    system.coordinator.month_key = None

    assert ChargedThisMonthSensor(system.coordinator, system.entry).last_reset is None

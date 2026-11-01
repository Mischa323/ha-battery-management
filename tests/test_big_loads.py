"""The big consumers: out of the house's profile, and off the packs on request.

Asked for on 1 November, from the list of what other systems do (Predbat
takes the car out of its load history; evcc decides whether the home battery
may feed the car). The airco dehumidifying all night had already pushed the
standby measurement up once. Now power sensors for such consumers are set
under Control: their use is kept out of the learned profile and the standby
measurement, and a switch decides whether the packs cover them.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from custom_components.battery_management import coordinator as coordinator_module
from custom_components.battery_management.const import POLICY_BIG_LOAD

AIRCO, CAR = "sensor.airco_power", "sensor.car_charger_power"


@pytest.fixture
def site(build_system):
    def _build(**kwargs):
        system = build_system(big_loads=[AIRCO, CAR], **kwargs)
        system.hass.states.set(AIRCO, 0)
        system.hass.states.set(CAR, 0)
        return system

    return _build


def test_what_they_draw_is_added_up(site):
    system = site()
    system.hass.states.set(AIRCO, 800)
    system.hass.states.set(CAR, 1500)

    assert system.coordinator.big_load_w() == 2300


def test_a_sensor_in_kilowatts_is_read_as_such(site):
    system = site()
    system.hass.states.set(CAR, 7.4, {"unit_of_measurement": "kW"})

    assert system.coordinator.big_load_w() == pytest.approx(7400)


def test_one_that_cannot_be_read_counts_nothing(site):
    system = site()
    system.hass.states.set(AIRCO, "unavailable")
    system.hass.states.set(CAR, 1500)

    assert system.coordinator.big_load_w() == 1500


def test_none_configured_is_nothing(build_system):
    assert build_system().coordinator.big_load_w() == 0.0


def test_the_house_profile_leaves_them_out(site, monkeypatch):
    """Meter 2500 W, packs 0, airco 2000: the house draws 500."""
    system = site()
    co = system.coordinator
    system.hass.states.set(AIRCO, 2000)
    co._other_controller_power = lambda: 0.0
    monkeypatch.setattr(coordinator_module.dt_util, "utcnow",
                        lambda: datetime(2026, 11, 3, 19, 0, tzinfo=timezone.utc))

    co._track_house_draw(2500.0, 1000.0)
    co._track_house_draw(2500.0, 1060.0)

    assert co.draw_profile[19] == pytest.approx(500)
    assert co._house_draw_w == pytest.approx(500)
    # and the trace still sees the whole house
    assert co._house_now_w == pytest.approx(2500)


def test_the_standby_measurement_leaves_them_out(site, monkeypatch):
    """An airco dehumidifying at night is not standby."""
    system = site()
    co = system.coordinator
    system.hass.states.set(AIRCO, 400)
    co._other_controller_power = lambda: 0.0
    at = datetime(2026, 11, 3, 2, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(coordinator_module.dt_util, "utcnow", lambda: at)

    co._track_base_load(1000.0, 1000.0)
    co._track_base_load(1000.0, 1060.0)

    assert co._night["wh"] == pytest.approx(600 * 60 / 3600.0)


async def test_fed_by_the_packs_as_before(site):
    system = site(grid=3000)
    system.hass.states.set(CAR, 2000)

    await system.coordinator._async_tick(None)

    assert system.coordinator.setpoint > 1000
    assert system.coordinator.active_policy != POLICY_BIG_LOAD


async def test_switched_off_they_run_on_the_grid(site):
    """3000 W in the house, 2000 of it the car: the packs give 1000."""
    system = site(grid=3000)
    co = system.coordinator
    co.feed_big_loads = False
    system.hass.states.set(CAR, 2000)

    await co._async_tick(None)

    assert co.setpoint <= 1000
    assert co.active_policy == POLICY_BIG_LOAD
    assert "grote verbruiker (2000 W) via het net" in co.tick_log[-1]["doing"]


async def test_switched_off_but_idle_it_changes_nothing(site):
    system = site(grid=3000)
    co = system.coordinator
    co.feed_big_loads = False

    await co._async_tick(None)

    assert co.setpoint > 1000
    assert co.active_policy != POLICY_BIG_LOAD


async def test_the_switch_survives_a_restart(site):
    system = site()
    await system.coordinator.async_set_feed_big_loads(False)
    stored = system.coordinator._state_to_save()

    fresh = site()
    fresh.coordinator._store.data = stored
    await fresh.coordinator._async_restore()

    assert fresh.coordinator.feed_big_loads is False


def test_the_switch_entity(site):
    switch = pytest.importorskip(
        "custom_components.battery_management.switch",
        reason="needs a real Home Assistant for the switch platform",
    )
    system = site()
    entity = switch.FeedBigLoadsSwitch(system.coordinator, system.entry)

    assert entity.is_on is True
    assert entity.extra_state_attributes == {"big_load_w": 0}

"""Telling the owner when something is off - once.

Asked for on 1 November, from the list of what other systems do: a pack gone
for ten minutes, the meter unreadable for five, a purchase at peak price, and
the packs expected to run dry in the coming peak even with what is kept for
it. Always to Home Assistant's own notifications, and to a notify service -
the phone - when one is set. Once per episode, per slot, per peak: a message
every tick would be switched off within the hour.
"""
from __future__ import annotations

import pytest

from custom_components.battery_management import coordinator as coordinator_module
from custom_components.battery_management.const import (
    ALERT_NO_GRID_S,
    ALERT_OFFLINE_S,
    POLICY_DYNAMIC_CHARGE,
    POLICY_NO_GRID_DATA,
)

from .test_save_for_peak import AFTERNOON_AT_040, house  # noqa: F401
from .test_trade_fill import afternoon, prices  # noqa: F401
from .test_trade_sell import trading  # noqa: F401


@pytest.fixture
def clock(monkeypatch):
    now = [1_000_000.0]
    monkeypatch.setattr(coordinator_module.time, "time", lambda: now[0])
    return now


def online(system):
    for status in system.coordinator.unit_status.values():
        status.online = True
    return system.coordinator


def sent(system, domain="persistent_notification"):
    return [c for c in system.hass.services.calls if c.domain == domain]


async def test_a_pack_gone_for_ten_minutes_is_said_once(build_system, clock):
    system = build_system()
    co = online(system)
    co.unit_status["Batterij 2"].online = False

    await co._check_alerts()
    assert sent(system) == []

    clock[0] += ALERT_OFFLINE_S
    await co._check_alerts()
    clock[0] += 60
    await co._check_alerts()

    (call,) = sent(system)
    assert "Batterij 2" in call.data["title"]
    assert "10 minuten" in call.data["message"]


async def test_back_and_gone_again_is_said_again(build_system, clock):
    system = build_system()
    co = online(system)
    co.unit_status["Batterij 2"].online = False
    await co._check_alerts()
    clock[0] += ALERT_OFFLINE_S
    await co._check_alerts()

    co.unit_status["Batterij 2"].online = True
    await co._check_alerts()
    co.unit_status["Batterij 2"].online = False
    await co._check_alerts()
    clock[0] += ALERT_OFFLINE_S
    await co._check_alerts()

    assert len(sent(system)) == 2


async def test_to_the_phone_too_when_a_service_is_set(build_system, clock):
    system = build_system(notify_service="notify.mobile_app_telefoon")
    co = online(system)
    co.unit_status["Batterij 1"].online = False
    await co._check_alerts()
    clock[0] += ALERT_OFFLINE_S
    await co._check_alerts()

    (call,) = sent(system, "notify")
    assert call.service == "mobile_app_telefoon"
    assert "Batterij 1" in call.data["title"]


async def test_switched_off_says_nothing(build_system, clock):
    system = build_system(notify=False)
    co = online(system)
    co.unit_status["Batterij 1"].online = False
    await co._check_alerts()
    clock[0] += ALERT_OFFLINE_S
    await co._check_alerts()

    assert sent(system) == []


async def test_no_meter_for_five_minutes(build_system, clock):
    system = build_system()
    co = system.coordinator
    co.active_policy = POLICY_NO_GRID_DATA
    await co._check_alerts()
    clock[0] += ALERT_NO_GRID_S
    await co._check_alerts()

    (call,) = sent(system)
    assert call.data["title"] == "Battery Management: Geen meterstand"


async def test_a_purchase_at_peak_price(afternoon):
    """13:00 is the peak here: buying in it is worth knowing about."""
    system = afternoon(peak=(13, 14, 15, 16), peak_at=0.45, cheap=())
    co = system.coordinator
    co.active_policy = POLICY_DYNAMIC_CHARGE
    co._last_buy_why = "minimum"

    await co._check_alerts()
    await co._check_alerts()

    titles = [c.data["title"] for c in sent(system)]
    assert titles.count("Battery Management: Koopt tegen piekprijs") == 1
    message = next(c.data["message"] for c in sent(system) if "piekprijs" in c.data["title"])
    assert "€0.450" in message and "om het minimum te halen" in message


async def test_not_for_a_purchase_at_a_cheap_price(afternoon):
    system = afternoon()
    co = system.coordinator
    co.active_policy = POLICY_DYNAMIC_CHARGE

    await co._check_alerts()

    assert not [c for c in sent(system) if "piekprijs" in c.data["title"]]


async def test_dry_in_the_peak_even_with_what_is_kept(house):
    """At 10 % even the whole afternoon kept leaves the peak ~6.7 kWh short."""
    system = house(soc=10.0)

    await system.coordinator._check_alerts()
    await system.coordinator._check_alerts()

    (call,) = [c for c in sent(system) if "leeg in de piek" in c.data["title"]]
    assert "piek van 18:00" in call.data["message"]
    assert "kWh van het net" in call.data["message"]


async def test_not_when_what_is_kept_covers_the_peak(house):
    """At 50 % the afternoon kept covers it: nothing to say."""
    system = house(soc=50.0)

    await system.coordinator._check_alerts()

    assert not [c for c in sent(system) if "leeg in de piek" in c.data["title"]]


async def test_the_tick_checks(build_system, clock):
    system = build_system(grid=500)
    co = system.coordinator
    co._gone_since["pack_offline:Batterij 2"] = clock[0] - ALERT_OFFLINE_S
    system.hass.states.set(system.soc(1), None)

    await co._async_tick(None)

    assert any("Batterij 2" in c.data["title"] for c in sent(system))

"""1 October: topping up at EUR 0.34 before a morning peak the packs, at
85 %, would have crossed with charge to spare.

Two faults and one rule. The verdict flipped between "buy" and "wait" as the
house drew each percent (`test_hold_compare` pins that), a purchase cut short
by the hold kept the slot and held the packs at nought while the house
imported, and the margin on the hold applied even though meeting the peak
"emptier" cost nothing: the packs could carry the house through it alone.
"""
from __future__ import annotations

from datetime import timedelta

from custom_components.battery_management import coordinator as coordinator_module
from custom_components.battery_management.const import (
    CONF_CHEAP_HOURS,
    POLICY_BUY_WINDOW,
    POLICY_CHEAPER_LATER,
    POLICY_DYNAMIC_CHARGE,
)
from tests.conftest import GRID_SENSOR

from .test_plan import NOW, PRICES, planned  # noqa: F401

MIDNIGHT = NOW.replace(hour=0, minute=0, second=0, microsecond=0)
FIVE_THIRTY = MIDNIGHT + timedelta(hours=5, minutes=30)


def morning() -> list[dict]:
    """0.336 now and through the night, the peak at 08:00-11:00 at 0.45, and
    after it 0.30 - three and a half cents cheaper, under the five-cent margin."""
    slots = []
    for i in range(48):
        start = MIDNIGHT + timedelta(hours=i)
        hour = start.hour if start.day == MIDNIGHT.day else None
        if hour is None:
            price = 0.34
        elif 8 <= hour < 11:
            price = 0.45
        elif 11 <= hour < 16:
            price = 0.30
        else:
            price = 0.336
        slots.append({"start": start.isoformat(),
                      "end": (start + timedelta(hours=1)).isoformat(),
                      "value": price})
    return slots


def at_dawn(planned, monkeypatch, *, soc, draw=450.0):
    # 1.68 kWh of sun still to come into 14 kWh of packs: a ceiling of 88 %
    system = planned(remaining=1.68, soc=soc, **{CONF_CHEAP_HOURS: 2})
    monkeypatch.setattr(coordinator_module.dt_util, "utcnow", lambda: FIVE_THIRTY)
    system.hass.states.set(PRICES, 0.336, {"raw_today": morning()})
    system.hass.states.set(GRID_SENSOR, 300)
    system.coordinator._house_draw_w = draw
    return system


async def test_full_enough_packs_wait_for_any_cheaper_window(planned, monkeypatch):
    """At 85 % the packs see 450 W through to 11:00 many times over, so the
    3.6 cents after the peak are worth waiting for - margin or not."""
    system = at_dawn(planned, monkeypatch, soc=(85.0, 85.0))

    assert system.coordinator._can_bridge() is True
    assert system.coordinator._buy_ceiling() == (0.0, POLICY_CHEAPER_LATER)

    await system.coordinator._async_tick(None)
    assert system.coordinator.active_policy != POLICY_DYNAMIC_CHARGE
    assert system.coordinator.setpoint > 0  # covering the house, not idling


async def test_unknown_draw_keeps_the_margin(planned, monkeypatch):
    """Nothing measured, nothing assumed: then only five cents holds it."""
    system = at_dawn(planned, monkeypatch, soc=(85.0, 85.0), draw=None)

    assert system.coordinator._can_bridge() is False
    ceiling, reason = system.coordinator._buy_ceiling()
    assert reason != POLICY_CHEAPER_LATER
    assert ceiling > 80


async def test_packs_too_low_to_bridge_keep_the_margin(planned, monkeypatch):
    """At 10 % (0.7 kWh above the floor) the peak cannot be crossed on the
    packs, so a few cents later is not worth meeting it empty for."""
    system = at_dawn(planned, monkeypatch, soc=(10.0, 10.0))

    assert system.coordinator._can_bridge() is False
    assert system.coordinator._buy_ceiling()[1] != POLICY_CHEAPER_LATER


async def test_a_hold_mid_slot_lets_the_packs_cover_the_house(planned, monkeypatch):
    """A purchase begun in the slot, then the hold: the slot is no longer a
    buying slot, so grid-zero covers the house instead of the meter."""
    system = at_dawn(planned, monkeypatch, soc=(85.0, 85.0), draw=None)
    await system.coordinator._async_tick(None)
    assert system.coordinator.active_policy == POLICY_DYNAMIC_CHARGE

    system.coordinator._house_draw_w = 450.0
    system.hass.states.set(GRID_SENSOR, 500)
    await system.coordinator._async_tick(None)

    assert system.coordinator.active_policy != POLICY_BUY_WINDOW
    assert system.coordinator._buying_slot is None
    assert system.coordinator.setpoint > -7000
    # the meter here does not answer the packs, so the integrator climbs back
    # from -7000 at its own pace; held at nought by the slot it never could
    for _ in range(40):
        await system.coordinator._async_tick(None)
    assert system.coordinator.setpoint > 0

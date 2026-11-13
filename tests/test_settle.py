"""After a purchase or a sale, grid-zero waits for the meter to catch up.

Found on 13 November in the traces of 8-13 November: the tick after a buying
slot ended started from the purchase's own setpoint, so the packs went on at
half power for a tick ("laadt 3212 W zon-overschot op", at night) and then
overshot the other way while the meter still read their 7 kW - 24 times in
six days, 0.32 kWh charged outside a buying slot and 0.18 kWh sent to the
grid. Now it stands at nought for two ticks and follows the meter from there.
"""
from __future__ import annotations

from custom_components.battery_management import coordinator as coordinator_module
from custom_components.battery_management.const import POLICY_DYNAMIC_CHARGE, SETTLE_TICKS

from .conftest import GRID_SENSOR
from .test_trade_fill import AFTERNOON, afternoon, prices  # noqa: F401
from .test_trade_sell import trading  # noqa: F401


async def bought_then_past_the_slot(afternoon, monkeypatch):
    system = afternoon()
    co = system.coordinator
    await co._async_tick(None)
    assert co.active_policy == POLICY_DYNAMIC_CHARGE and co.setpoint < 0
    # 17:00: an ordinary hour, nothing to buy; the meter still shows the packs
    monkeypatch.setattr(coordinator_module.dt_util, "utcnow", lambda: AFTERNOON.replace(hour=17))
    system.hass.states.set(GRID_SENSOR, 7300)
    return system


async def test_the_tick_after_a_purchase_stands_at_nought(afternoon, monkeypatch):
    system = await bought_then_past_the_slot(afternoon, monkeypatch)
    co = system.coordinator

    await co._async_tick(None)

    assert co.setpoint == 0
    assert co.tick_log[-1]["sp_reason"] == "settle"
    assert "zon-overschot" not in co.tick_log[-1]["doing"]


async def test_then_it_follows_the_meter_again_from_nought(afternoon, monkeypatch):
    system = await bought_then_past_the_slot(afternoon, monkeypatch)
    co = system.coordinator
    for _ in range(SETTLE_TICKS):
        await co._async_tick(None)
        assert co.setpoint == 0

    system.hass.states.set(GRID_SENSOR, 300)     # the packs have stopped
    await co._async_tick(None)

    assert co.tick_log[-1]["sp_reason"] != "settle"
    assert 0 < co.setpoint <= 300

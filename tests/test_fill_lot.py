"""A kWh bought for the sale is sold against what it cost.

Reported on 10 November: the fill bought 12 kWh at EUR 0.40 in the night "om
vanavond te verkopen" - for the morning peak, in fact - and the morning's sale
then weighed each kWh against the refill after it, 0.47, and sold nothing.
The purchase was made because the pair cleared the margin; the sale now asks
the same question of the same kWh. And the trace names the peak it is for.

Prices as in test_trade_fill: 13:00-17:00 at 0.21, the 18:00-22:00 peak at
0.45 (an export value of 0.42), 0.30 otherwise. Against the 0.30 refill after
the peak a sale clears 0.42 - 0.30/0.88 - 0.03 = 0.049, under the owner's
five cents; against the 0.21 the fill paid it clears 0.15.
"""
from __future__ import annotations

from datetime import timedelta

import pytest

from custom_components.battery_management import coordinator as coordinator_module
from custom_components.battery_management.const import POLICY_DYNAMIC_CHARGE

from .test_trade_fill import AFTERNOON, afternoon, prices  # noqa: F401
from .test_trade_sell import trading  # noqa: F401

EVENING = AFTERNOON.replace(hour=18)
NOON_AFTER = (AFTERNOON + timedelta(days=1)).replace(hour=11)


def bought(co, kwh=7.0, price=0.21, sold=0.0, until=NOON_AFTER):
    co._fill_lot = {"until": until.isoformat(), "kwh": kwh, "eur": kwh * price, "sold": sold}


@pytest.fixture
def evening(afternoon, monkeypatch):
    """18:00, the peak, the packs at 95 % over a 70 % line."""

    def _build(**kwargs):
        system = afternoon(soc=(95.0, 95.0), **kwargs)
        monkeypatch.setattr(coordinator_module.dt_util, "utcnow", lambda: EVENING)
        return system

    return _build


def test_without_a_lot_the_evening_does_not_clear_the_margin(evening):
    verdict = evening().coordinator.trade_verdict()

    assert verdict["why"] == "margin_too_small"
    assert verdict["refill"] == pytest.approx(0.30)
    assert verdict["lot"] is None


def test_what_the_fill_bought_is_weighed_at_its_price(evening):
    co = evening().coordinator
    bought(co)

    verdict = co.trade_verdict()

    assert verdict["refill"] == pytest.approx(0.21)
    assert verdict["lot"] == pytest.approx(0.21)
    assert verdict["why"] is None
    assert verdict["margin"] > 0.10


def test_once_it_is_sold_the_refill_counts_again(evening):
    co = evening().coordinator
    bought(co, sold=7.0)

    assert co.trade_verdict()["why"] == "margin_too_small"


def test_never_more_than_is_above_the_line(evening):
    """At 70 % there is nothing above the line: the lot is gone, to the house."""
    co = evening().coordinator
    bought(co)
    for unit in range(2):
        co.hass.states.set(co._units[unit].soc_sensor, 70.0)

    assert co.fill_lot() is None


def test_after_its_window_it_is_forgotten(evening):
    co = evening().coordinator
    bought(co, until=EVENING - timedelta(minutes=1))

    assert co.fill_lot() is None
    assert co.trade_verdict()["why"] == "margin_too_small"


def test_the_plan_shows_the_sale_the_purchase_is_for(afternoon):
    """13:00, before the fill has bought: the evening it buys for is in the
    plan's selling, not just in its buying."""
    co = afternoon().coordinator

    lot = co.fill_lot()
    sells = co.sell_forecast()

    assert lot is not None and lot["price"] == pytest.approx(0.21)
    assert any(key[11:13] in ("18", "19", "20", "21") for key in sells)


def test_a_purchase_for_the_sale_is_counted(afternoon):
    co = afternoon().coordinator
    co._last_buy_why = "trade_fill"
    co.active_policy = POLICY_DYNAMIC_CHARGE

    co._track_fill_lot(7000.0, 1800.0)
    co._track_fill_lot(7000.0, 1800.0)

    assert co._fill_lot["kwh"] == pytest.approx(7.0)
    assert co._fill_lot["eur"] / co._fill_lot["kwh"] == pytest.approx(0.21)
    assert coordinator_module._lot_end(co._fill_lot) > AFTERNOON + timedelta(hours=12)


def test_another_purchase_is_not(afternoon):
    co = afternoon().coordinator
    co._last_buy_why = "pays"
    co.active_policy = POLICY_DYNAMIC_CHARGE

    co._track_fill_lot(7000.0, 3600.0)

    assert co._fill_lot == {}


async def test_the_lot_survives_a_restart(afternoon):
    system = afternoon()
    bought(system.coordinator)
    stored = system.coordinator._state_to_save()

    fresh = afternoon()
    fresh.coordinator._store.data = stored
    await fresh.coordinator._async_restore()

    assert fresh.coordinator._fill_lot == system.coordinator._fill_lot


def test_the_plan_says_which_peak_the_fill_is_for(afternoon):
    trade = afternoon().coordinator.plan()["trade"]

    assert trade["fill_to"] is not None
    assert trade["fill_for"].startswith(AFTERNOON.replace(hour=18).isoformat()[:13])

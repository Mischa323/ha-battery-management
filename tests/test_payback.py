"""In how many years the packs pay for themselves, and what selling is doing.

The arithmetic is `trading.simple_payback` and `trading.payback_remaining`;
pinned here is what the coordinator feeds them and when it refuses to say.
"""
from __future__ import annotations

from datetime import date

import pytest

from custom_components.battery_management.const import (
    CONF_BATTERY_PRICE,
    CONF_SALDERING_UNTIL,
    MODE_GRID_ZERO,
    TRADE_OFF,
    TRADE_SHADOW,
    TRADE_STATE_NOT_DYNAMIC,
    TRADE_STATE_OFF,
    TRADE_STATE_SELLING,
    TRADE_STATE_WAITING,
    TRADE_STATE_WOULD_SELL,
)

from .test_trade_sell import NOW, trading  # noqa: F401


def counted(system, *, days: float, saved: float, saved_after: float, actual: float = 0.0):
    system.coordinator.money.update(
        counted_h=days * 24.0,
        saved_eur=saved,
        saved_after_eur=saved_after,
        saved_actual_eur=actual,
    )


def test_the_owner_s_question_is_the_footing_without_saldering(trading):
    """EUR 5000 of packs; 30 days saved EUR 20 with saldering and EUR 41.10
    without - EUR 243 and EUR 500 a year, so 20.5 and 10 years."""
    system = trading()
    counted(system, days=30, saved=20.0, saved_after=41.1)

    payback = system.coordinator.payback()

    assert payback["known"] is True
    assert payback["years_without_saldering"] == pytest.approx(10.0, abs=0.05)
    assert payback["years_with_saldering"] == pytest.approx(20.5, abs=0.05)
    assert payback["yearly_saving_without_saldering_eur"] == pytest.approx(41.1 / 720 * 8760, abs=0.01)
    assert payback["reliable"] is True


def test_years_to_go_counts_what_is_saved_and_when_saldering_ends(trading):
    """From 5 August 2026 saldering has 149 days left, at 243 a year: 99 of
    the 4900 still to repay. The other 4801 at 500 a year is 9.6 years."""
    system = trading()
    counted(system, days=30, saved=20.0, saved_after=41.1, actual=100.0)

    years = system.coordinator.payback()["years_to_go"]

    left = (date(2027, 1, 1) - NOW.date()).days / 365.25
    per_year_with = 20.0 / 720 * 8760
    per_year_after = 41.1 / 720 * 8760
    expected = left + (4900 - per_year_with * left) / per_year_after
    assert years == pytest.approx(expected, abs=0.05)


def test_after_saldering_the_two_agree_on_the_rate(trading):
    system = trading(**{CONF_SALDERING_UNTIL: "2026-01-01"})
    counted(system, days=30, saved=20.0, saved_after=41.1, actual=0.0)

    payback = system.coordinator.payback()

    assert payback["years_to_go"] == pytest.approx(payback["years_without_saldering"], abs=0.05)


def test_no_purchase_price_no_payback(trading):
    system = trading(**{CONF_BATTERY_PRICE: 0})
    counted(system, days=30, saved=20.0, saved_after=41.1)

    payback = system.coordinator.payback()

    assert payback["known"] is False
    assert payback["years_without_saldering"] is None


def test_a_day_is_too_little_to_say_anything(trading):
    system = trading()
    counted(system, days=0.5, saved=1.0, saved_after=2.0)

    assert system.coordinator.payback()["known"] is False


def test_a_fortnight_is_shown_but_not_to_be_relied_on(trading):
    system = trading()
    counted(system, days=14, saved=10.0, saved_after=20.0)

    payback = system.coordinator.payback()

    assert payback["known"] is True
    assert payback["reliable"] is False
    assert payback["counted_days"] == 14.0


def test_a_saving_that_never_comes_never_pays_back(trading):
    system = trading()
    counted(system, days=30, saved=-5.0, saved_after=-2.0)

    payback = system.coordinator.payback()

    assert payback["years_without_saldering"] is None
    assert payback["years_to_go"] is None


def test_the_payback_attributes_keep_their_names(trading):
    system = trading()
    assert set(system.coordinator.payback()) == {
        "known", "years_without_saldering", "years_with_saldering", "years_to_go",
        "yearly_saving_with_saldering_eur", "yearly_saving_without_saldering_eur",
        "saved_so_far_eur", "battery_price_eur", "counted_days", "reliable",
        "since", "saldering_until",
    }


async def test_saved_so_far_is_counted_on_the_footing_of_the_day(trading, monkeypatch):
    """Before the end date a tick's saving goes in with saldering, after it
    without - what has really been saved is the mix."""
    from .test_savings import PACKS, hour
    from custom_components.battery_management import coordinator as coordinator_module
    from custom_components.battery_management.const import CONF_BATTERY_POWER_SENSOR

    clock = [1_000_000.0]
    monkeypatch.setattr(coordinator_module.time, "time", lambda: clock[0])

    def advance(seconds):
        clock[0] += seconds

    system = trading(**{CONF_BATTERY_POWER_SENSOR: PACKS})
    system.coordinator.enabled = False
    system.hass.states.set(PACKS, -1000)
    system.hass.states.set("sensor.p1_meter_power", 0)
    await hour(system, advance)
    money = system.coordinator.money
    assert money["saved_actual_eur"] == pytest.approx(money["saved_eur"])

    system.coordinator._saldering_until = date(2026, 1, 1)
    await hour(system, advance)
    assert money["saved_actual_eur"] == pytest.approx(
        money["saved_eur"] / 2 + money["saved_after_eur"] / 2, rel=0.02
    )


# -- the status -----------------------------------------------------------------


async def test_the_status_says_what_selling_is_doing(trading):
    system = trading()
    await system.coordinator._async_tick(None)
    assert system.coordinator.trade_state() == TRADE_STATE_SELLING

    shadow = trading(trade=TRADE_SHADOW)
    await shadow.coordinator._async_tick(None)
    assert shadow.coordinator.trade_state() == TRADE_STATE_WOULD_SELL

    modest = trading(peak=0.38, **{CONF_SALDERING_UNTIL: "2026-01-01"})
    await modest.coordinator._async_tick(None)
    assert modest.coordinator.trade_state() == TRADE_STATE_WAITING
    assert modest.coordinator.trade_attributes()["why"] == "margin_too_small"


async def test_off_is_off_however_it_got_there(trading):
    system = trading(trade=TRADE_OFF)
    assert system.coordinator.trade_state() == TRADE_STATE_OFF

    disabled = trading()
    disabled.coordinator.enabled = False
    assert disabled.coordinator.trade_state() == TRADE_STATE_OFF

    other = trading()
    other.coordinator.mode = MODE_GRID_ZERO
    assert other.coordinator.trade_state() == TRADE_STATE_NOT_DYNAMIC


async def test_the_status_carries_the_three_prices(trading):
    system = trading()
    await system.coordinator._async_tick(None)

    attributes = system.coordinator.trade_attributes()

    assert attributes["export_value_eur_kwh"] == pytest.approx(0.52)
    assert attributes["refill_eur_kwh"] == pytest.approx(0.15)
    assert attributes["wear_eur_kwh"] == pytest.approx(0.0298, abs=0.0001)
    assert attributes["min_margin_eur_kwh"] == 0.05
    assert set(attributes) == {
        "trade_mode", "export_value_eur_kwh", "refill_eur_kwh", "wear_eur_kwh",
        "margin_eur_kwh", "min_margin_eur_kwh", "why", "saldering",
        "saldering_until", "sell_floor",
    }

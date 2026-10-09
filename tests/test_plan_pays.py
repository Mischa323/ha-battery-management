"""How much the plan will really buy, and why not more.

Reported on 9 October: the card said "Laadt tot 89 %" and 19.7 kWh from the
grid, with the packs at 18 % - and one quarter planned. Prices were flat: only
a quarter that beats the dear hours by the price margin is bought on, and on
that day one did. The ceiling is what buying *may* reach; `grid_planned_kwh`
is what the planned hours can deliver.

And asked the same day: if tomorrow is dearer, does it buy more? It does - the
margin is measured against the dear hours of the next 24 hours, so a dear
morning tomorrow lets today's cheap afternoon pay.
"""
from __future__ import annotations

import pytest

from custom_components.battery_management.const import TRADE_OFF

from .test_trade_fill import afternoon, prices  # noqa: F401
from .test_trade_sell import trading  # noqa: F401

#: 13:00 at 0.18, the rest of the afternoon 0.21, 0.25 otherwise
FLAT = dict(cheap=(13,), cheap_at=0.18, overrides={14: 0.21, 15: 0.21, 16: 0.21},
            peak=(), ordinary=0.25)
#: the same, with tomorrow 06:00-12:00 at 0.35
DEAR_MORNING = dict(FLAT, overrides={**FLAT["overrides"], **{h: 0.35 for h in range(30, 36)}})


@pytest.fixture
def day(afternoon, monkeypatch):
    def _build(**prices_today):
        system = afternoon(soc=(18.0, 20.0), **prices_today)
        system.coordinator.trade_mode = TRADE_OFF
        # the sun leaves 11 % free, as on the day
        monkeypatch.setattr(system.coordinator, "_solar_headroom_ceiling", lambda: 89.0)
        return system

    return _build


def bought_hours(plan):
    return [h["start"] for h in plan["hours"] if h["buy"]]


def test_a_flat_day_buys_only_what_pays(day):
    """Room for 19.6 kWh; one hour beats the 0.25 evening by five cents."""
    plan = day(**FLAT).coordinator.plan()

    assert plan["expected"]["grid_kwh"] == pytest.approx(19.6)
    assert len(bought_hours(plan)) == 1
    assert plan["expected"]["grid_planned_kwh"] == pytest.approx(7.0)
    assert plan["price_margin"] == pytest.approx(0.05)


def test_a_dearer_tomorrow_makes_more_of_today_pay(day):
    """Tomorrow 06:00-12:00 at 0.35: the afternoon's 0.21 now beats the hours
    it is saving for by more than the margin, so it fills."""
    plan = day(**DEAR_MORNING).coordinator.plan()

    assert len(bought_hours(plan)) >= 3
    assert plan["expected"]["grid_planned_kwh"] == plan["expected"]["grid_kwh"]


def test_a_wider_margin_buys_less(day):
    """The five cents are a setting: at 0.10 not even 13:00 qualifies."""
    system = day(**FLAT)
    system.coordinator._price_margin = 0.10

    assert system.coordinator.plan()["expected"]["grid_planned_kwh"] == 0.0


def test_a_purchase_held_for_after_the_peak_still_counts(day):
    """Cheaper at 20:00 than all afternoon: nothing before the peak, the
    hours after it expected instead - and those are what it will buy."""
    plan = day(cheap=(20, 21, 22, 23), cheap_at=0.10, peak=(17, 18, 19),
               peak_at=0.45, ordinary=0.30).coordinator.plan()

    assert plan["waiting"] is not None
    assert plan["expected"]["grid_planned_kwh"] > 0

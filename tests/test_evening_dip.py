"""A quarter or two at peak level is not a peak to buy the minimum for.

Reported on 17 November: "waarom wilt hij nou weer vanavond op een neutrale
uur laden". The packs had bought for the minimum until 15:00 and stopped at
22 %; the house then drew them down. With the next day cheap (EUR 0.21-0.39,
0.30 on average), the 24 hours ahead made this afternoon and evening, at
0.35-0.39, the dear part. That stretch ended at 20:30; 20:45 fell a tenth of
a cent under the peak's line, and 21:00 alone rose over it again. The
minimum, bought "before the next peak", took that lone quarter for one and
planned 20:45 at 0.347 - a neutral hour, with 0.27-0.34 from 21:30 and a
night at 0.21-0.24 before the next real peak.

The same price at 14:30 was a good purchase: the long stretch of the evening
came after it. What decides is what follows the dip, not the dip.

Prices: today's quarters from the trace to 17:45, the evening as read off the
price card, tomorrow's hours off its own page (17-18 November, local time).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from custom_components.battery_management import coordinator as coordinator_module

from .conftest import GRID_SENSOR
from .test_save_for_peak import house  # noqa: F401
from .test_trade_fill import afternoon  # noqa: F401
from .test_trade_sell import ENERGY_TAX, MARKUP_AND_VAT, PRICE_SENSOR, trading  # noqa: F401

LOCAL = timezone(timedelta(hours=1))
TODAY = [
    .372, .372, .372, .372, .372, .367, .356, .355, .348, .348, .349, .347,
    .338, .337, .337, .337, .336, .337, .336, .337, .334, .334, .345, .367,
    .356, .381, .393, .406, .405, .406, .409, .411, .422, .418, .420, .423,
    .424, .425, .415, .402, .419, .403, .403, .386, .394, .377, .368, .356,
    .366, .359, .358, .356, .361, .359, .348, .337, .358, .357, .347, .346,
    .350, .349, .353, .357, .354, .363, .370, .371, .385, .389, .389, .386,
    .381, .376, .372, .372, .372, .357, .357, .361, .372, .365, .357, .347,
    .358, .343, .336, .325, .339, .329, .314, .300, .300, .292, .274, .267,
]
TOMORROW_BY_HOUR = [
    .243, .233, .220, .220, .213, .230, .253, .286, .299, .289, .273, .260,
    .263, .260, .276, .329, .365, .388, .391, .382, .375, .359, .349, .332,
]


def two_days() -> dict:
    allin, market, untaxed = [], [], []
    start = datetime(2026, 11, 17, tzinfo=LOCAL)
    values = TODAY + [v for v in TOMORROW_BY_HOUR for _ in range(4)]
    for i, value in enumerate(values):
        s = start + timedelta(minutes=15 * i)
        row = {"start": s.isoformat(), "end": (s + timedelta(minutes=15)).isoformat()}
        allin.append({**row, "value": value})
        market.append({**row, "value": round(value - ENERGY_TAX - MARKUP_AND_VAT, 4)})
        untaxed.append({**row, "value": round(value - ENERGY_TAX, 4)})
    return {"raw_today": allin, "market_prices": market, "untaxed_prices": untaxed}


def at(hour, minute=0):
    return datetime(2026, 11, 17, hour, minute, tzinfo=LOCAL)


@pytest.fixture
def evening(house, monkeypatch):
    """17 November at `moment`, the minimum at 25 %, a one-cent margin."""

    def _build(moment, soc):
        system = house(soc=soc, draw=1300.0)
        co = system.coordinator
        co._price_margin = 0.01
        co.buy_ceiling_min = 25.0
        monkeypatch.setattr(coordinator_module.dt_util, "utcnow",
                            lambda: moment.astimezone(timezone.utc))
        system.hass.states.set(PRICE_SENSOR, 0.35, two_days())
        system.hass.states.set(GRID_SENSOR, 1300)
        return system

    return _build


def chosen(system, moment):
    co = system.coordinator
    return [s.start.astimezone(LOCAL).strftime("%H:%M") for s in co._floor_slots(co._price_forecast(), moment)]


def test_at_three_the_lone_quarter_at_nine_is_not_the_next_peak(evening):
    system = evening(at(15, 0), soc=22.0)
    co = system.coordinator
    slots = co._price_forecast()
    lone = next(s for s in slots if s.start == at(21, 0))
    line = co._peak_level(slots, at(15, 0))

    assert lone.price >= line                     # at peak level, alone
    assert not co._peak_lasts(slots, lone, line)
    assert co._next_peak_start(slots, at(15, 0)) != at(21, 0)
    assert "20:45" not in chosen(system, at(15, 0))


def test_and_at_six_neither(evening):
    system = evening(at(18, 0), soc=13.0)

    assert "20:45" not in chosen(system, at(18, 0))


def test_at_half_past_two_the_minimum_is_still_bought_before_the_evening(evening):
    """The same 0.347 at 14:30, with the evening's long stretch to come from
    16:15: that is what the minimum is for."""
    system = evening(at(14, 30), soc=17.0)
    co = system.coordinator
    slots = co._price_forecast()

    assert co._next_peak_start(slots, at(14, 30)) == at(16, 15)
    assert {"14:30", "14:45"} & set(chosen(system, at(14, 30)))

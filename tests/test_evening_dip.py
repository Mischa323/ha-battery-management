"""A quarter's dip inside a peak is not the gap before the next one.

Reported on 17 November: "waarom wilt hij nou weer vanavond op een neutrale
uur laden". The packs had bought for the minimum until 15:00 and stopped at
22 %; the house then drew them down. With the next day cheap (EUR 0.21-0.39,
0.30 on average), the 24 hours ahead made this afternoon and evening, at
0.35-0.39, the peak - from 15:00 on. One quarter at 20:45 fell a tenth of a
cent under the peak's line before 21:00 rose over it again, and the minimum,
bought "before the next peak", took that quarter for the gap between two:
at 0.347, a neutral hour, with 0.27-0.34 from 21:30 and a night at 0.21-0.24
to come.

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


def test_at_three_the_dip_at_2045_is_inside_tonight_s_peak(evening):
    system = evening(at(15, 0), soc=22.0)
    co = system.coordinator
    slots = co._price_forecast()
    dip = next(s for s in slots if s.start == at(20, 45))
    line = co._peak_level(slots, at(15, 0))

    assert dip.price < line                       # under the line, by a hair
    assert co._in_stretch(slots, dip, line)       # and still the peak
    assert "20:45" not in chosen(system, at(15, 0))


def test_and_at_six_neither(evening):
    system = evening(at(18, 0), soc=13.0)

    assert "20:45" not in chosen(system, at(18, 0))


def test_an_hour_under_the_line_still_ends_a_peak(evening):
    """From 21:15 the evening stays under the line: that is the end of it."""
    system = evening(at(15, 0), soc=22.0)
    co = system.coordinator
    slots = co._price_forecast()
    line = co._peak_level(slots, at(15, 0))
    later = next(s for s in slots if s.start == at(21, 30))

    assert not co._in_stretch(slots, later, line)

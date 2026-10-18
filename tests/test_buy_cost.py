"""Buying counts the round trip and the wear, like selling does.

Reported on 18 October: on a day of 0.28-0.39 it topped up at 0.338 for an
evening at about 0.39. The price margin was a bare gap - 0.338 + 0.05 is under
0.39 - but a kWh bought at 0.338 costs 0.384 after the 12 % round trip, and
0.414 with three cents of wear: more than the grid would have charged that
evening. With a purchase price for the packs, a slot now qualifies only when
`price / 0.88 + wear + margin` stays under the dear hours it saves for, so
the margin is what is left over. Without one, there is no wear to count and
the margin keeps its old meaning.
"""
from __future__ import annotations

from custom_components.battery_management.const import TRADE_OFF

from .test_trade_fill import afternoon, prices  # noqa: F401
from .test_trade_sell import trading  # noqa: F401

#: 18 October in miniature: 13:00 at 0.338, everything else at 0.39
DEAR_DAY = dict(cheap=(13,), cheap_at=0.338, peak=(), ordinary=0.39)


def buys(system):
    return [h["start"] for h in system.coordinator.plan()["hours"] if h["buy"]]


def build(afternoon, monkeypatch, *, battery_price=5000.0, **day):
    system = afternoon(soc=(30.0, 30.0), **day)
    system.coordinator.trade_mode = TRADE_OFF
    system.coordinator._battery_price = battery_price
    monkeypatch.setattr(system.coordinator, "_solar_headroom_ceiling", lambda: 90.0)
    return system


def test_a_gap_the_round_trip_eats_is_not_bought_on(afternoon, monkeypatch):
    system = build(afternoon, monkeypatch, **DEAR_DAY)

    assert buys(system) == []


def test_a_gap_that_survives_it_is(afternoon, monkeypatch):
    """0.25 costs 0.314 once used, five cents under 0.39 with room to spare."""
    system = build(afternoon, monkeypatch, **dict(DEAR_DAY, cheap_at=0.25))

    assert buys(system) != []


def test_without_a_purchase_price_the_margin_is_the_bare_gap(afternoon, monkeypatch):
    """No wear to count without knowing what the packs cost: 0.338 + 0.05 is
    under 0.39, as before."""
    system = build(afternoon, monkeypatch, battery_price=0.0, **DEAR_DAY)

    assert buys(system) != []


def test_the_chart_s_green_is_the_same_test(afternoon, monkeypatch):
    """The cheap band says which hours are worth buying on; it must not paint
    an hour green that the buying refuses."""
    system = build(afternoon, monkeypatch, **DEAR_DAY)

    cheap, _dear = system.coordinator._day_bands(system.coordinator._price_forecast())
    assert cheap == set()


def test_the_plan_says_what_it_counts(afternoon, monkeypatch):
    with_price = build(afternoon, monkeypatch, **DEAR_DAY).coordinator.plan()
    without = build(afternoon, monkeypatch, battery_price=0.0, **DEAR_DAY).coordinator.plan()

    assert with_price["buy_cost"]["round_trip"] == 0.88
    assert 0.02 < with_price["buy_cost"]["wear_eur_kwh"] < 0.04
    assert without["buy_cost"] is None


def test_the_wear_counts_as_well_as_the_loss(afternoon, monkeypatch):
    """0.29 is 0.330 after the round trip - five cents under 0.39 - but 0.359
    with three cents of wear, and that is not."""
    system = build(afternoon, monkeypatch, **dict(DEAR_DAY, cheap_at=0.29))

    assert buys(system) == []


async def test_the_tick_does_not_top_up_at_a_loss(afternoon, monkeypatch):
    system = build(afternoon, monkeypatch, **DEAR_DAY)

    await system.coordinator._async_tick(None)

    assert system.coordinator.active_policy != "dynamic_charge"
    assert system.coordinator.setpoint >= 0


def test_the_hours_expected_after_a_hold_are_held_to_it_too(afternoon, monkeypatch):
    """A cheaper 20:00 (0.29) holds the afternoon (0.36) back - but 0.29 costs
    0.359 once used, plus three cents of wear, which does not beat 0.39 by
    five. The plan must not promise to buy there."""
    system = build(afternoon, monkeypatch, cheap=(20, 21, 22, 23), cheap_at=0.29,
                   peak=(17, 18, 19), peak_at=0.45, ordinary=0.39,
                   overrides={13: 0.36, 14: 0.36, 15: 0.36, 16: 0.36})
    plan = system.coordinator.plan()

    assert plan["waiting"] is not None
    assert plan["waiting"]["hours"] == []

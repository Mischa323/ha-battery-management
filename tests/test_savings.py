"""What the packs saved, and what selling earned.

The saving is a counterfactual on the meter: the same house without packs
would have drawn `grid + packs`, and the difference in what the two cost is
what the packs are worth that tick. Counted twice - export valued with the
energy tax back under saldering, and without - because the scheme ends and a
payback time must not quietly assume it does not.

The per-tick arithmetic is `trading.grid_cost`; what is pinned here is what
goes into it, and when nothing should be counted at all.
"""
from __future__ import annotations

import time

import pytest

from custom_components.battery_management import coordinator as coordinator_module
from custom_components.battery_management.const import (
    CONF_BATTERY_POWER_SENSOR,
    CONF_CHARGE_BELOW_SOC,
    MAX_ENERGY_GAP_INTERVALS,
    MONEY_FIELDS,
    TRADE_ON,
    TRADE_SHADOW,
)

from .conftest import GRID_SENSOR
from .test_trade_sell import (  # noqa: F401
    ENERGY_TAX,
    MARKUP_AND_VAT,
    PRICE_SENSOR,
    prices,
    trading,
)

PACKS = "sensor.battery_power"

#: the ordinary hour of the price fixture, and what it is made of
PRICE = 0.30
MARKET = PRICE - ENERGY_TAX - MARKUP_AND_VAT


@pytest.fixture
def clock(monkeypatch):
    now = [1_000_000.0]

    def advance(seconds: float) -> None:
        now[0] += seconds

    monkeypatch.setattr(coordinator_module.time, "time", lambda: now[0])
    return advance


@pytest.fixture
def house(trading, clock, monkeypatch):
    """An ordinary hour (0.30 all-in), a meter and a measured pack power."""

    def _build(*, grid: float, packs: float, trade=TRADE_ON, **options):
        system = trading(
            peak=PRICE, trade=trade, **{CONF_BATTERY_POWER_SENSOR: PACKS, **options}
        )
        system.hass.states.set(GRID_SENSOR, grid)
        system.hass.states.set(PACKS, packs)
        return system

    return _build


async def hour(system, advance, ticks: int = 60) -> None:
    """An hour in minute ticks; the first only starts the clock."""
    await system.coordinator._async_tick(None)
    for _ in range(ticks):
        advance(3600 / ticks)
        await system.coordinator._async_tick(None)


async def test_covering_the_house_saves_what_the_import_would_have_cost(house, clock):
    system = house(grid=0, packs=1000, enabled=False)

    await hour(system, clock)

    money = system.coordinator.money
    assert money["saved_eur"] == pytest.approx(PRICE, rel=0.01)
    assert money["saved_after_eur"] == pytest.approx(PRICE, rel=0.01)
    assert money["counted_h"] == pytest.approx(1.0)


async def test_storing_sun_costs_the_export_it_replaced(house, clock):
    """Negative now, repaid when it is used: a kWh kept is a kWh not sold.
    Which is exactly why the packs pay back faster once saldering has gone -
    the export they give up is worth less."""
    system = house(grid=0, packs=-1000, enabled=False)

    await hour(system, clock)

    money = system.coordinator.money
    assert money["saved_eur"] == pytest.approx(-(MARKET + ENERGY_TAX), rel=0.01)
    assert money["saved_after_eur"] == pytest.approx(-MARKET, rel=0.01)


async def test_a_partial_cover_counts_only_the_import_it_replaced(house, clock):
    """The packs give 1500 W while the house exports 500 W: 1000 W replaced
    import, and 500 W went out that would otherwise not have."""
    system = house(grid=-500, packs=1500, enabled=False)

    await hour(system, clock)

    money = system.coordinator.money
    assert money["saved_eur"] == pytest.approx(
        1.0 * PRICE + 0.5 * (MARKET + ENERGY_TAX), rel=0.01
    )
    assert money["saved_after_eur"] == pytest.approx(1.0 * PRICE + 0.5 * MARKET, rel=0.01)


async def test_nothing_is_counted_without_a_meter(house, clock):
    system = house(grid=0, packs=1000, enabled=False)
    system.hass.states.set(GRID_SENSOR, "unavailable")

    await hour(system, clock)

    assert system.coordinator.money["counted_h"] == 0.0


async def test_nothing_is_counted_without_pack_power(house, clock):
    system = house(grid=0, packs=1000, enabled=False)
    system.hass.states.set(PACKS, "unavailable")

    await hour(system, clock)

    assert system.coordinator.money["counted_h"] == 0.0


async def test_nothing_is_counted_without_the_export_price(trading, clock):
    """A third-party price sensor gives no market price, so an export cannot
    be valued - and a saving with half its sum missing is not a saving."""
    system = trading(side_lists=False, **{CONF_BATTERY_POWER_SENSOR: PACKS})
    system.hass.states.set(PACKS, 1000)
    system.coordinator.enabled = False

    await hour(system, clock)

    assert system.coordinator.money["counted_h"] == 0.0


async def test_an_outage_is_not_multiplied_out(house, clock):
    system = house(grid=0, packs=1000, enabled=False)

    await system.coordinator._async_tick(None)
    clock(system.coordinator._interval * (MAX_ENERGY_GAP_INTERVALS + 1))
    await system.coordinator._async_tick(None)

    assert system.coordinator.money["counted_h"] == 0.0


async def test_the_day_closes_with_its_money(house, clock, monkeypatch):
    system = house(grid=0, packs=1000, enabled=False)
    await hour(system, clock)
    day = system.coordinator.periods["day"]["key"]

    tomorrow = coordinator_module.dt_util.now() + coordinator_module.timedelta(days=1)
    monkeypatch.setattr(coordinator_module.dt_util, "now", lambda: tomorrow)
    clock(60)
    await system.coordinator._async_tick(None)

    attributes = system.coordinator.money_attributes("day")
    assert attributes["history"][day]["saved_eur"] == pytest.approx(PRICE, rel=0.02)
    assert attributes["saved_eur"] == pytest.approx(0.0, abs=0.01)
    # the lifetime figure does not reset with the day
    assert system.coordinator.money["saved_eur"] == pytest.approx(PRICE, rel=0.02)


async def test_the_money_attributes_keep_their_names(house, clock):
    system = house(grid=0, packs=1000, enabled=False)
    await hour(system, clock, ticks=2)

    attributes = system.coordinator.money_attributes("month")
    assert set(attributes) == {
        "period", "key", "total", "since", "saldering", "history", *MONEY_FIELDS,
    }
    assert set(system.coordinator.money_total_attributes()) == {
        "since", "saldering", "saldering_until", *MONEY_FIELDS,
    }


async def test_the_money_survives_a_restart(house, clock):
    system = house(grid=0, packs=1000, enabled=False)
    await hour(system, clock)
    stored = system.coordinator._state_to_save()

    fresh = house(grid=0, packs=0, enabled=False)
    fresh.coordinator._store.data = {**stored, "saved_at": time.time()}
    await fresh.coordinator._async_restore()

    assert fresh.coordinator.money == system.coordinator.money
    assert fresh.coordinator.money_since == system.coordinator.money_since
    assert fresh.coordinator.periods["month"]["saved_eur"] == pytest.approx(
        system.coordinator.periods["month"]["saved_eur"]
    )


async def test_the_state_shown_follows_the_footing_of_the_day(house, clock):
    system = house(grid=0, packs=-1000, enabled=False)
    await hour(system, clock)

    assert system.coordinator.period_saved_eur("day") == pytest.approx(
        -(MARKET + ENERGY_TAX), abs=0.01
    )
    system.coordinator._saldering_until = coordinator_module.date(2026, 1, 1)
    assert system.coordinator.period_saved_eur("day") == pytest.approx(-MARKET, abs=0.01)
    assert system.coordinator.saved_total_eur() == pytest.approx(-MARKET, abs=0.01)


# -- selling ------------------------------------------------------------------


async def test_a_real_sale_counts_the_export_at_its_margin(trading, clock):
    """The export, not the pack output: the part that covered the house would
    have covered it without any selling."""
    system = trading(**{CONF_BATTERY_POWER_SENSOR: PACKS})
    system.hass.states.set(GRID_SENSOR, -6700)
    system.hass.states.set(PACKS, 7000)

    await hour(system, clock)

    money = system.coordinator.money
    margin = system.coordinator.last_trade_verdict["margin"]
    assert money["traded_kwh"] == pytest.approx(6.7, rel=0.01)
    assert money["traded_eur"] == pytest.approx(6.7 * margin, rel=0.01)
    assert money["shadow_kwh"] == 0.0


async def test_shadow_counts_what_full_output_would_have_added(trading, clock):
    """The packs are covering 300 W of house; selling would have run them at
    7000 W, so 6700 W more would have gone out."""
    system = trading(trade=TRADE_SHADOW, soc=(95.0, 95.0), **{CONF_BATTERY_POWER_SENSOR: PACKS})
    system.hass.states.set(GRID_SENSOR, 0)
    system.hass.states.set(PACKS, 300)

    await hour(system, clock)

    money = system.coordinator.money
    assert money["shadow_kwh"] == pytest.approx(6.7, rel=0.01)
    assert money["shadow_eur"] > 0
    assert money["traded_kwh"] == 0.0


async def test_dry_run_selling_is_counted_as_shadow(trading, clock):
    """Nothing reached the packs, so nothing was sold."""
    system = trading(soc=(95.0, 95.0), dry_run=True, **{CONF_BATTERY_POWER_SENSOR: PACKS})
    system.hass.states.set(GRID_SENSOR, 0)
    system.hass.states.set(PACKS, 300)

    await hour(system, clock)

    assert system.coordinator.money["traded_kwh"] == 0.0
    assert system.coordinator.money["shadow_kwh"] == pytest.approx(6.7, rel=0.01)


async def test_shadow_stops_when_its_packs_would_be_empty(trading, clock):
    """Shadow never empties a pack, so it keeps its own account: 60 % down to
    a 50 % floor is 2.8 kWh of 28, and then it would have stopped."""
    system = trading(trade=TRADE_SHADOW, soc=(60.0, 60.0), **{CONF_BATTERY_POWER_SENSOR: PACKS})
    system.coordinator.sell_floor = 50
    system.hass.states.set(GRID_SENSOR, 0)
    system.hass.states.set(PACKS, 0)

    await hour(system, clock)

    assert system.coordinator.trade_would_sell is False
    assert system.coordinator.money["shadow_kwh"] == pytest.approx(2.8, abs=0.15)


async def test_buying_fills_shadow_s_packs_back_up(trading, clock):
    """Once the packs have bought, shadow's pretend-empty packs are as full as
    the real ones again - otherwise one evening's shadow sale would block
    every evening after it."""
    system = trading(
        trade=TRADE_SHADOW,
        soc=(60.0, 60.0),
        **{CONF_BATTERY_POWER_SENSOR: PACKS, CONF_CHARGE_BELOW_SOC: 70},
    )
    system.coordinator.sell_floor = 50
    system.hass.states.set(GRID_SENSOR, 0)
    system.hass.states.set(PACKS, 0)
    await hour(system, clock)
    assert system.coordinator._shadow_sold_kwh > 2.5

    # the same slot turned cheap: the packs buy
    system.hass.states.set(PRICE_SENSOR, 0.02, prices(peak=0.02))
    clock(60)
    await system.coordinator._async_tick(None)

    assert system.coordinator.trade_would_sell is False
    assert system.coordinator._shadow_sold_kwh == 0.0

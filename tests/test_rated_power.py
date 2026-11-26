"""A pack's size is what it is rated for, not what it offers this minute.

26 November: the number a pack is commanded through carries the most it will
take, and that is for the way it is going and tapers near full. Set to charge
at 98-99 % the packs offered 2.5-3.2 kW, set to discharge 3.5. That number
times the time to full is the packs' size, so they were 20-23 kWh one tick
and 28 the next. Seen small, the morning peak looked too big for them and the
whole morning was kept while the house bought at EUR 0.53. At 10:15 the plan
then flipped between selling and keeping on every tick, because every tick
switched the way the packs went.

The control still takes what a pack offers now. Its size, and the power the
plan counts on, take the most it has been seen to offer.
"""
from __future__ import annotations

import pytest

from .test_save_for_peak import holds, house  # noqa: F401
from .test_trade_fill import afternoon  # noqa: F401
from .test_trade_sell import trading  # noqa: F401


def offer(system, watts: float) -> None:
    """What the packs' numbers say they will take, as the Anker one does."""
    for index in range(2):
        state = system.hass.states.get(system.target(index))
        system.hass.states.set(system.target(index), state.state, {"max": watts})


def test_the_size_does_not_shrink_when_the_offer_tapers(house):
    system = house(soc=98.0)
    co = system.coordinator
    full = co.usable_capacity_kwh()

    offer(system, 2490)

    assert co.usable_capacity_kwh() == pytest.approx(full)
    # the control still takes what the pack offers now
    assert {co._unit_snapshot(cfg).unit_max for cfg in co._units} == {2490}


def test_what_is_kept_does_not_change_with_the_way_the_packs_go(house):
    """At 95 % the packs reach the far side of the evening peak: nothing is
    kept. Seen at a tapered 2.5 kW they were a third smaller, and the whole
    afternoon was kept for a peak they could carry anyway."""
    system = house(soc=95.0)
    assert holds(system) == frozenset()

    offer(system, 2490)

    assert holds(system) == frozenset()


async def test_the_rating_survives_a_restart(house, build_system):
    """Come back at 99 % and set to charge, and the first thing the packs
    offer is the tapered figure: the rating seen before is kept."""
    system = house(soc=98.0)
    full = system.coordinator.usable_capacity_kwh()
    await system.coordinator._async_tick(None)
    stored = system.coordinator._store.data

    revived = house(soc=99.0)
    offer(revived, 2490)
    revived.coordinator._rated_w.clear()
    revived.coordinator._store.data = stored
    await revived.coordinator._async_restore()

    assert revived.coordinator.usable_capacity_kwh() == pytest.approx(full)

"""Getting prices from a supplier directly, instead of from another integration.

Pointing at somebody else's sensor stays the default. This is the other route,
for a house where installing a second custom integration is the obstacle.

The network is faked at the session, so what is exercised here is everything
around the call: when it runs, what it does with a bad answer, and - the part
that matters most - that a supplier we cannot reach disables buying rather than
inventing a price.
"""
from __future__ import annotations

import time
from datetime import timedelta

import pytest

from homeassistant.util import dt as dt_util

from custom_components.battery_management.const import (
    CONF_PRICE_RESOLUTION,
    CONF_PRICE_SENSOR,
    CONF_PRICE_SOURCE,
    MAX_PRICE_AGE,
    MODE_DYNAMIC,
    RESOLUTION_HOURLY,
)
from custom_components.battery_management.suppliers import (
    SOURCE_ENTITY,
    SOURCE_NONE,
    SUPPLIER_FRANK,
)


class FakeResponse:
    def __init__(self, payload, status: int = 200) -> None:
        self._payload = payload
        self.status = status

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def raise_for_status(self):
        if self.status >= 400:
            raise RuntimeError(f"HTTP {self.status}")

    async def json(self):
        return self._payload


class FakeSession:
    """Records what was asked for and answers with whatever it was given.

    A refresh makes one request per day, so `replies` may hold a payload per
    call; the last one is repeated once they run out. That default keeps the
    tests that only care about today short, while
    `test_a_day_that_is_not_published_yet_does_not_sink_the_other` can still
    hand the two days different answers.
    """

    def __init__(
        self,
        payload=None,
        status: int = 200,
        boom: Exception | None = None,
        replies: list | None = None,
    ):
        self.replies = replies if replies is not None else [payload]
        self.status = status
        self.boom = boom
        self.calls: list[tuple[str, dict]] = []

    def post(self, url, json=None, timeout=None):
        self.calls.append((url, json))
        if self.boom is not None:
            raise self.boom
        reply = self.replies[min(len(self.calls) - 1, len(self.replies) - 1)]
        if isinstance(reply, Exception):
            raise reply
        return FakeResponse(reply, self.status)


def frank_payload(*prices: float) -> dict:
    """Hourly slots from midnight, priced in order, under today's alias."""
    return frank_answer(
        [
            {
                "from": f"2026-09-01T{hour:02d}:00:00.000Z",
                "till": f"2026-09-01T{hour + 1:02d}:00:00.000Z",
                "marketPrice": price,
                "energyTaxPrice": 0.13,
            }
            for hour, price in enumerate(prices)
        ]
    )


def frank_answer(rows: list) -> dict:
    """One day's answer. A refresh asks for each day in its own request."""
    return {"data": {"marketPrices": {"electricityPrices": rows}}}


#: Frank's reply for a date it has not published: a GraphQL error and a null
#: `data`, which is exactly why each day gets its own request.
FRANK_UNPUBLISHED = {
    "errors": [{"message": "No marketprices found for segment ELECTRICITY"}],
    "data": None,
}


def with_frank(build_system, session, **kwargs):
    system = build_system(grid=0, **{CONF_PRICE_SOURCE: SUPPLIER_FRANK}, **kwargs)
    use(system, session)
    return system


def use(system, session) -> None:
    """Hand the coordinator this session instead of Home Assistant's."""
    system.coordinator._session = lambda: session


# -- which route is configured -------------------------------------------------


async def test_a_supplier_is_enough_to_offer_dynamic_mode(build_system):
    system = build_system(grid=0, **{CONF_PRICE_SOURCE: SUPPLIER_FRANK})

    assert system.coordinator.prices_configured
    assert MODE_DYNAMIC in system.coordinator.available_modes


async def test_a_sensor_is_still_enough(build_system):
    system = build_system(
        grid=0,
        **{CONF_PRICE_SOURCE: SOURCE_ENTITY, CONF_PRICE_SENSOR: "sensor.prices"},
    )

    assert MODE_DYNAMIC in system.coordinator.available_modes


async def test_choosing_the_sensor_route_without_a_sensor_offers_nothing(build_system):
    system = build_system(grid=0, **{CONF_PRICE_SOURCE: SOURCE_ENTITY})

    assert MODE_DYNAMIC not in system.coordinator.available_modes


async def test_no_source_means_no_dynamic_mode(build_system):
    system = build_system(grid=0, **{CONF_PRICE_SOURCE: SOURCE_NONE})

    assert MODE_DYNAMIC not in system.coordinator.available_modes


async def test_an_entry_from_before_the_choice_existed_still_works(build_system):
    """It has a sensor and no source, which is what the sensor route means."""
    system = build_system(grid=0, **{CONF_PRICE_SENSOR: "sensor.prices"})

    assert system.coordinator.price_source == SOURCE_ENTITY
    assert MODE_DYNAMIC in system.coordinator.available_modes


# -- fetching ------------------------------------------------------------------


async def test_it_asks_the_supplier_and_keeps_the_answer(build_system):
    session = FakeSession(frank_payload(0.10, 0.05, 0.20))
    system = with_frank(build_system, session)

    await system.coordinator.async_refresh_prices()

    url, body = session.calls[0]
    assert "frank" in url
    assert body["operationName"] == "MarketPrices"
    assert system.coordinator.prices_fetched_at is not None
    assert system.coordinator.prices_error is None
    assert len(system.coordinator._price_attributes()["prices"]) == 3


async def test_each_day_is_asked_for_in_its_own_request(build_system):
    session = FakeSession(frank_payload(0.10))
    system = with_frank(build_system, session)

    await system.coordinator.async_refresh_prices()

    today = dt_util.now().date()
    assert [body["variables"]["date"] for _, body in session.calls] == [
        today.isoformat(),
        (today + timedelta(days=1)).isoformat(),
    ]


async def test_a_day_that_is_not_published_yet_does_not_sink_the_other(build_system):
    """The morning case, and the reason the two days are not one request.

    Frank answers a date it has no prices for with a GraphQL error and a null
    `data`. Today's prices must survive that completely - otherwise the
    integration would have no forecast at all until the afternoon, every day.
    """
    session = FakeSession(
        replies=[frank_payload(0.10, 0.05, 0.20), FRANK_UNPUBLISHED]
    )
    system = with_frank(build_system, session)

    await system.coordinator.async_refresh_prices()

    assert system.coordinator.prices_error is None
    assert len(system.coordinator._price_attributes()["prices"]) == 3


async def test_a_supplier_that_answers_nothing_at_all_is_still_an_error(build_system):
    """Tolerating a missing tomorrow must not tolerate a missing everything."""
    session = FakeSession(replies=[FRANK_UNPUBLISHED, FRANK_UNPUBLISHED])
    system = with_frank(build_system, session)

    await system.coordinator.async_refresh_prices()

    assert system.coordinator.prices_error == "no prices in the response"
    assert system.coordinator._price_attributes() is None


async def test_a_transport_failure_is_reported_as_itself(build_system):
    """Not as "no prices in the response": we never got a response to read."""
    session = FakeSession(
        replies=[frank_payload(0.10), OSError("no route to host")]
    )
    system = with_frank(build_system, session)

    await system.coordinator.async_refresh_prices()

    # today still arrived, so this is not an error at all
    assert system.coordinator.prices_error is None

    use(system, FakeSession(boom=OSError("no route to host")))
    await system.coordinator.async_refresh_prices()

    assert "no route to host" in system.coordinator.prices_error


async def test_an_unreachable_supplier_is_not_an_error_state(build_system):
    """No forecast disables buying and leaves grid-zero regulating, which is
    exactly how the integration behaves without a dynamic contract at all."""
    session = FakeSession(boom=OSError("no route to host"))
    system = with_frank(build_system, session)

    await system.coordinator.async_refresh_prices()

    assert system.coordinator.prices_error
    assert system.coordinator._price_forecast() is None


async def test_an_http_error_is_caught_too(build_system):
    session = FakeSession({}, status=503)
    system = with_frank(build_system, session)

    await system.coordinator.async_refresh_prices()

    assert "503" in system.coordinator.prices_error


async def test_an_answer_it_cannot_read_is_reported_not_kept(build_system):
    """Reaching them and understanding nothing must not look healthy."""
    session = FakeSession({"errors": [{"message": "schema changed"}]})
    system = with_frank(build_system, session)

    await system.coordinator.async_refresh_prices()

    assert system.coordinator.prices_error == "no prices in the response"
    assert system.coordinator._price_attributes() is None


async def test_a_failed_refresh_keeps_the_previous_answer(build_system):
    """Prices do not change retroactively, and the slots expire by themselves."""
    session = FakeSession(frank_payload(0.10, 0.05))
    system = with_frank(build_system, session)
    await system.coordinator.async_refresh_prices()

    use(system, FakeSession(boom=OSError("gone")))
    await system.coordinator.async_refresh_prices()

    assert len(system.coordinator._price_attributes()["prices"]) == 2


async def test_a_stale_cache_is_dropped_rather_than_ranked_on(build_system):
    session = FakeSession(frank_payload(0.10, 0.05))
    system = with_frank(build_system, session)
    await system.coordinator.async_refresh_prices()

    system.coordinator.prices_fetched_at = time.time() - MAX_PRICE_AGE - 1

    assert system.coordinator._price_attributes() is None


async def test_the_sensor_route_never_calls_out(build_system):
    session = FakeSession(frank_payload(0.10))
    system = build_system(
        grid=0,
        **{CONF_PRICE_SOURCE: SOURCE_ENTITY, CONF_PRICE_SENSOR: "sensor.prices"},
    )
    use(system, session)

    await system.coordinator.async_refresh_prices()

    assert session.calls == []


async def test_the_diagnostics_say_where_prices_come_from(build_system):
    session = FakeSession(frank_payload(0.10, 0.05))
    system = with_frank(build_system, session)
    await system.coordinator.async_refresh_prices()

    report = system.coordinator.diagnostics()

    assert report["settings"]["price_source"] == SUPPLIER_FRANK
    assert report["state"]["price_slots"] == 2
    assert report["state"]["prices_error"] is None


# -- for the Energy dashboard --------------------------------------------------


async def test_the_exchange_component_is_kept_apart_from_the_all_in_price(
    build_system,
):
    """Import is billed all-in, export is not. One number cannot be both, and a
    wrong number on an energy dashboard looks exactly like a right one."""
    session = FakeSession(
        frank_answer(
            [
                {
                    # a slot wide enough to cover whenever this runs
                    "from": "2020-01-01T00:00:00.000Z",
                    "till": "2099-01-01T00:00:00.000Z",
                    "marketPrice": 0.10,
                    "marketPriceTax": 0.021,
                    "sourcingMarkupPrice": 0.02,
                    "energyTaxPrice": 0.13,
                }
            ]
        )
    )
    system = with_frank(build_system, session)

    await system.coordinator.async_refresh_prices()

    assert system.coordinator.current_price()["price"] == 0.271
    assert system.coordinator.current_market_price() == 0.10


async def test_the_exchange_price_does_not_disturb_the_ranking(build_system):
    """It rides alongside under a key the shape parser deliberately ignores."""
    session = FakeSession(frank_payload(0.10, 0.05, 0.20))
    system = with_frank(build_system, session)

    await system.coordinator.async_refresh_prices()

    assert len(system.coordinator._price_forecast()) == 3


async def test_by_the_hour_folds_the_exchange_price_too(build_system):
    """Whichever resolution is chosen has to apply to both numbers.

    Folding only the all-in price would put an hourly mean next to the
    exchange price of one quarter - two numbers about different spans of
    time, side by side, each looking as authoritative as the other.
    """
    hour = dt_util.utcnow().replace(minute=0, second=0, microsecond=0)
    quarters = [
        {
            "from": (start := hour + timedelta(minutes=15 * i)).isoformat(),
            "till": (start + timedelta(minutes=15)).isoformat(),
            "marketPrice": price,
            "energyTaxPrice": 0.13,
        }
        # the next hour repeats the prices, so an hour that turns over while
        # the test runs cannot change the answer
        for i, price in enumerate((0.10, 0.20, 0.30, 0.40) * 2)
    ]
    system = with_frank(
        build_system,
        FakeSession(frank_answer(quarters)),
        **{CONF_PRICE_RESOLUTION: RESOLUTION_HOURLY},
    )

    await system.coordinator.async_refresh_prices()

    # the duration-weighted mean of the four quarters, in both numbers
    assert system.coordinator.current_market_price() == 0.25
    assert system.coordinator.current_price()["price"] == 0.38


async def test_a_third_party_sensor_has_no_exchange_price_to_offer(build_system):
    """It publishes whichever single number it publishes; do not invent one."""
    system = build_system(
        grid=0,
        **{CONF_PRICE_SOURCE: SOURCE_ENTITY, CONF_PRICE_SENSOR: "sensor.prices"},
    )
    system.hass.states.set(
        "sensor.prices",
        0.2,
        {"prices": [{"from": "2026-09-01T00:00:00Z", "price": 0.2}]},
    )

    assert system.coordinator.current_market_price() is None

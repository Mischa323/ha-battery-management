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

import pytest

from custom_components.battery_management.const import (
    CONF_PRICE_SENSOR,
    CONF_PRICE_SOURCE,
    MAX_PRICE_AGE,
    MODE_DYNAMIC,
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
    """Records what was asked for and answers with whatever it was given."""

    def __init__(self, payload=None, status: int = 200, boom: Exception | None = None):
        self.payload = payload
        self.status = status
        self.boom = boom
        self.calls: list[tuple[str, dict]] = []

    def post(self, url, json=None, timeout=None):
        self.calls.append((url, json))
        if self.boom is not None:
            raise self.boom
        return FakeResponse(self.payload, self.status)


def frank_payload(*prices: float) -> dict:
    """Hourly slots from midnight, priced in order."""
    return {
        "data": {
            "marketPricesElectricity": [
                {
                    "from": f"2026-09-01T{hour:02d}:00:00.000Z",
                    "till": f"2026-09-01T{hour + 1:02d}:00:00.000Z",
                    "marketPrice": price,
                    "energyTaxPrice": 0.13,
                }
                for hour, price in enumerate(prices)
            ]
        }
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

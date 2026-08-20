"""The buy ceiling's hand-set bounds, and the plan a dashboard renders.

The computed ceiling is only as good as the solar forecast behind it, and the
primary site's under-reads by half. So both ends have to be reachable without
waiting for a code change - and you have to be able to see what it intends
before you go turning knobs.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from custom_components.battery_management import coordinator as coordinator_module
from custom_components.battery_management.const import (
    CONF_CHARGE_BELOW_SOC,
    CONF_CHEAP_HOURS,
    CONF_EXPENSIVE_HOURS,
    CONF_FULL_CHARGE_MINUTES,
    CONF_PRICE_SENSOR,
    CONF_SOLAR_FORECAST_SENSORS,
    MODE_DYNAMIC,
    POLICY_DYNAMIC_CHARGE,
)

PRICES = "sensor.energy_prices"
FORECAST = "sensor.forecast_total"
NOW = datetime(2026, 8, 5, 12, 0, tzinfo=timezone.utc)


def price_attributes(cheap_hour: int, dear_hour: int) -> dict:
    midnight = NOW.replace(hour=0, minute=0, second=0, microsecond=0)
    slots = []
    for i in range(48):
        start = midnight + timedelta(hours=i)
        price = 0.20
        if start.hour == cheap_hour and start.day == NOW.day:
            price = 0.02
        elif start.hour == dear_hour and start.day == NOW.day:
            price = 0.60
        slots.append(
            {
                "start": start.isoformat(),
                "end": (start + timedelta(hours=1)).isoformat(),
                "value": price,
            }
        )
    return {"raw_today": slots}


@pytest.fixture
def planned(build_system, monkeypatch):
    def _build(*, remaining=1.0, soc=(20.0, 20.0), **options):
        monkeypatch.setattr(coordinator_module.dt_util, "utcnow", lambda: NOW)
        system = build_system(
            grid=300,
            units=(("093", soc[0]), ("052", soc[1])),
            **{
                CONF_PRICE_SENSOR: PRICES,
                CONF_SOLAR_FORECAST_SENSORS: [FORECAST],
                CONF_FULL_CHARGE_MINUTES: 120,  # 2 x 3500 W x 2 h = 14 kWh
                CONF_CHEAP_HOURS: 2,
                CONF_EXPENSIVE_HOURS: 3,
                **options,
            },
        )
        system.hass.states.set(PRICES, 0.02, price_attributes(12, 18))
        system.hass.states.set(FORECAST, remaining)
        system.coordinator.mode = MODE_DYNAMIC
        return system

    return _build


# -- the hand-set bounds -----------------------------------------------------


def test_wide_open_by_default(build_system):
    coordinator = build_system().coordinator

    assert coordinator.buy_ceiling_min == 0
    assert coordinator.buy_ceiling_max == 100


def test_a_maximum_caps_an_over_optimistic_calculation(planned):
    """The site's forecast under-reads by half, so the ceiling runs too high."""
    system = planned(remaining=1.0)
    assert round(system.coordinator.charge_ceiling()) == 93

    system.coordinator.buy_ceiling_max = 70.0

    assert system.coordinator.charge_ceiling() == 70.0


def test_a_minimum_overrides_a_gloomy_forecast(planned):
    """Lots of sun expected would say "buy nothing"; the floor says otherwise."""
    system = planned(remaining=20.0)
    assert system.coordinator.charge_ceiling() == 0.0

    system.coordinator.buy_ceiling_min = 30.0

    assert system.coordinator.charge_ceiling() == 30.0


async def test_the_bounds_take_effect_on_the_next_tick(planned):
    system = planned(remaining=20.0, soc=(20.0, 20.0))
    await system.coordinator._async_tick(None)
    assert system.coordinator.active_policy != POLICY_DYNAMIC_CHARGE

    await system.coordinator.async_set_buy_ceiling(low=50)
    await system.coordinator._async_tick(None)

    assert system.coordinator.active_policy == POLICY_DYNAMIC_CHARGE


async def test_the_bounds_are_clamped_to_a_percentage(planned):
    system = planned()

    await system.coordinator.async_set_buy_ceiling(low=-10, high=500)

    assert system.coordinator.buy_ceiling_min == 0
    assert system.coordinator.buy_ceiling_max == 100


def test_a_floor_above_the_ceiling_does_not_win(planned):
    """Nonsense input must not quietly invert the meaning of the two."""
    system = planned(remaining=1.0)
    system.coordinator.buy_ceiling_min = 90.0
    system.coordinator.buy_ceiling_max = 40.0

    assert system.coordinator.charge_ceiling() == 40.0


async def test_the_bounds_survive_a_restart(planned):
    system = planned()
    await system.coordinator.async_set_buy_ceiling(low=25, high=75)
    stored = system.coordinator._store.data

    system.coordinator.buy_ceiling_min = 0.0
    system.coordinator.buy_ceiling_max = 100.0
    system.coordinator._store.data = stored
    await system.coordinator._async_restore()

    assert system.coordinator.buy_ceiling_min == 25
    assert system.coordinator.buy_ceiling_max == 75


# -- the plan ----------------------------------------------------------------


def test_the_plan_names_the_hours_it_picked(planned):
    system = planned()

    plan = system.coordinator.plan()

    assert plan["has_prices"] is True
    assert len(plan["dear_hours"]) == 3
    assert any(h["price"] == 0.60 for h in plan["dear_hours"])

    # Two hours were asked for and only one is offered, which is the point.
    # This day is flat at 0.20 apart from one bargain, so the second-cheapest
    # hour costs exactly what the dear hours cost - buying on it saves nothing
    # and loses the round trip. It used to be named anyway, because the rank
    # took the cheapest two whatever they cost.
    assert len(plan["cheap_hours"]) == 1
    assert plan["cheap_hours"][0]["price"] == 0.02


def test_the_plan_carries_what_the_ceiling_was_computed_from(planned):
    system = planned(remaining=7.0)

    plan = system.coordinator.plan()

    assert plan["solar_remaining_kwh"] == 7.0
    assert plan["usable_capacity_kwh"] == pytest.approx(14.0)
    assert plan["charge_ceiling"] == pytest.approx(50.0)


def test_the_plan_says_so_when_there_are_no_prices(planned):
    system = planned()
    system.hass.states.set(PRICES, "unavailable")

    plan = system.coordinator.plan()

    assert plan["has_prices"] is False
    assert plan["cheap_hours"] == []
    assert plan["dear_hours"] == []


def test_the_plan_works_without_any_of_it_configured(build_system):
    """A dashboard card must not break on a site that configured nothing."""
    plan = build_system().coordinator.plan()

    assert plan["has_prices"] is False
    assert plan["solar_remaining_kwh"] is None
    assert plan["charge_ceiling"] is None


# -- the whole series, for the chart -------------------------------------------


def test_the_plan_carries_every_hour_not_just_the_chosen_ones(planned):
    """A chart needs the shape of the day, not the two ends of it."""
    system = planned()

    hours = system.coordinator.plan()["hours"]

    assert len(hours) > len(system.coordinator.plan()["cheap_hours"])
    assert all({"start", "end", "price", "role"} <= set(h) for h in hours)


def test_each_hour_says_which_decision_it_belongs_to(planned):
    """Computed here, not by a dashboard picking a threshold: "cheap" has to
    mean the hours this will actually buy on."""
    system = planned()

    roles = {h["role"] for h in system.coordinator.plan()["hours"]}

    assert roles <= {"cheap", "dear", "normal", "past"}
    assert "cheap" in roles and "dear" in roles


def test_the_cheap_hours_really_are_the_cheapest_ones(planned):
    system = planned()

    hours = system.coordinator.plan()["hours"]
    cheap = [h["price"] for h in hours if h["role"] == "cheap"]
    dear = [h["price"] for h in hours if h["role"] == "dear"]
    rest = [h["price"] for h in hours if h["role"] == "normal"]

    assert max(cheap) <= min(rest)
    assert min(dear) >= max(rest)


def test_the_series_is_in_time_order(planned):
    system = planned()

    starts = [h["start"] for h in system.coordinator.plan()["hours"]]

    assert starts == sorted(starts)


def test_no_prices_means_no_series_rather_than_an_empty_looking_chart(build_system):
    system = build_system(grid=0)

    plan = system.coordinator.plan()

    assert plan["has_prices"] is False
    assert plan["hours"] == []


# -- what this hour costs ------------------------------------------------------


def test_the_current_price_is_the_slot_we_are_in(planned):
    """The Plan sensor counts cheap hours; it never said what you are paying."""
    system = planned()

    now = system.coordinator.current_price()

    assert now["price"] == 0.02          # 12:00 is the cheap hour in the fixture
    assert now["role"] == "cheap"


def test_it_says_when_the_price_changes_and_to_what(planned):
    system = planned()

    now = system.coordinator.current_price()

    assert now["until"].startswith("2026-08-05T13:00")
    assert now["next_price"] == 0.20


def test_the_role_matches_what_the_chart_would_draw(planned):
    """Otherwise a dashboard would invent its own idea of "expensive"."""
    system = planned()

    hours = system.coordinator.plan()["hours"]
    current = next(h for h in hours if h["start"].startswith("2026-08-05T12:00"))

    assert current["role"] == system.coordinator.current_price()["role"]


def test_no_prices_means_no_current_price_rather_than_zero(build_system):
    system = build_system(grid=0)

    assert system.coordinator.current_price() is None


# -- the whole day, not just what is left of it --------------------------------


def test_the_series_starts_at_midnight_not_at_now(planned):
    """A chart that starts at "now" shows nothing of today by the evening,
    which is the opposite of what "today's prices" means."""
    system = planned()          # the fixture pins the clock at 12:00

    hours = system.coordinator.plan()["hours"]

    assert hours[0]["start"].endswith("T00:00:00+00:00")
    assert len([h for h in hours if h["past"]]) == 12


def test_an_hour_that_has_passed_unwatched_claims_no_decision(planned):
    """The ranking looks forward, so *re-ranking* a past hour would invent a
    decision that was never made. An hour we did watch keeps the verdict it
    was given at the time - see `test_price_history.py`; here nothing ever
    ticked, so there is nothing on record and "past" is the honest answer."""
    system = planned()

    past = [h for h in system.coordinator.plan()["hours"] if h["past"]]

    assert past
    assert all(h["role"] == "past" for h in past)


def test_the_hours_still_to_come_keep_their_roles(planned):
    system = planned()

    ahead = [h for h in system.coordinator.plan()["hours"] if not h["past"]]

    assert {h["role"] for h in ahead} >= {"cheap", "dear"}


# --- the band and the plan are two different facts, 2026-08-20 -------------
#
# Reported from the primary site: the chart showed one green bar where four
# were expected. It was not a bug in the ranking - `slots_to_buy` had narrowed
# correctly to the single hour the packs still had room for - but the chart had
# only one channel to say it in, so the cheap *stretch* the hour was picked out
# of had vanished. Both are now drawn: `role` is the band, `buy` is the plan.


def cheap_day(cheap_hours: tuple[int, ...], dear_hour: int) -> dict:
    """A day with several genuinely cheap hours, not just one bargain.

    The existing fixture has a single 0.02 hour on an otherwise flat day, which
    cannot tell the two sets apart: the margin filters everything else out, so
    the band and the plan are both exactly that hour. Distinguishing them needs
    a day where several hours clear the margin and the packs only need one.
    """
    midnight = NOW.replace(hour=0, minute=0, second=0, microsecond=0)
    slots = []
    for i in range(48):
        start = midnight + timedelta(hours=i)
        price = 0.40
        if start.day == NOW.day and start.hour in cheap_hours:
            # each a little cheaper than the last, so "which one first" is
            # unambiguous and the plan has an obvious right answer
            price = 0.05 + 0.01 * cheap_hours.index(start.hour)
        elif start.day == NOW.day and start.hour == dear_hour:
            price = 0.90
        slots.append(
            {
                "start": start.isoformat(),
                "end": (start + timedelta(hours=1)).isoformat(),
                "value": price,
            }
        )
    return {"raw_today": slots}


@pytest.fixture
def banded(build_system, monkeypatch):
    """Four cheap hours ahead, and packs with room for roughly one of them.

    `charge_below_soc` is raised to 90 on purpose. At the default of 40 an hour
    of charging is 50 points of state of charge, so every pack below the
    ceiling needs exactly one slot and every pack above it needs none - the
    need is a step, and a step cannot show that the plan tracks the packs while
    the band does not.
    """

    def _build(*, soc=(80.0, 80.0), **options):
        monkeypatch.setattr(coordinator_module.dt_util, "utcnow", lambda: NOW)
        system = build_system(
            grid=300,
            units=(("093", soc[0]), ("052", soc[1])),
            **{
                CONF_PRICE_SENSOR: PRICES,
                CONF_FULL_CHARGE_MINUTES: 120,
                CONF_CHEAP_HOURS: 4,
                CONF_EXPENSIVE_HOURS: 3,
                CONF_CHARGE_BELOW_SOC: 90,
                **options,
            },
        )
        # 13:00 through 16:00, all ahead of the 12:00 clock
        system.hass.states.set(PRICES, 0.40, cheap_day((13, 14, 15, 16), 20))
        system.coordinator.mode = MODE_DYNAMIC
        return system

    return _build


def test_the_band_keeps_all_four_cheap_hours(banded):
    """The regression: four asked for, four coloured, however few get bought."""
    system = banded()

    hours = system.coordinator.plan()["hours"]
    cheap = [h for h in hours if h["role"] == "cheap"]

    assert len(cheap) == 4
    assert {h["start"][11:16] for h in cheap} == {"13:00", "14:00", "15:00", "16:00"}


def test_the_plan_marks_only_the_hours_it_needs(banded):
    """Packs at 80 % need about one hour, so one bar gets the outline."""
    system = banded()

    hours = system.coordinator.plan()["hours"]
    buying = [h for h in hours if h["buy"]]

    assert len(buying) == 1
    # the cheapest of the band, not the one that happens to come first
    assert buying[0]["start"][11:16] == "13:00"
    assert buying[0]["role"] == "cheap"


def test_every_planned_hour_is_also_in_the_band(banded):
    """The outline can only ever sit on a green bar - it is a subset."""
    for soc in ((10.0, 10.0), (50.0, 50.0), (80.0, 80.0), (99.0, 99.0)):
        hours = banded(soc=soc).coordinator.plan()["hours"]

        assert all(h["role"] == "cheap" for h in hours if h["buy"]), soc


def test_emptier_packs_plan_more_of_the_band(banded):
    """The band does not move with the state of charge; the plan does."""
    full = banded(soc=(90.0, 90.0)).coordinator.plan()
    empty = banded(soc=(10.0, 10.0)).coordinator.plan()

    assert len(full["cheap_hours"]) == len(empty["cheap_hours"]) == 4
    assert len(empty["buy_hours"]) > len(full["buy_hours"])

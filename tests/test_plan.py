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
    CONF_FILL_BEFORE_DEAR_DAY,
    CONF_SOLAR_FORECAST_MAX,
    CONF_SOLAR_FORECAST_SENSORS,
    MODE_DYNAMIC,
    POLICY_CHEAPER_TOMORROW,
    POLICY_DYNAMIC_CHARGE,
    POLICY_SOLAR_HEADROOM,
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
    """A past hour keeps its price colour, and claims nothing about the packs.

    The line is between a price and a decision. The colour is a price - it is
    ranked over the calendar day, and a day that has started has all its prices
    known, so recomputing it invents nothing. The *decision* cannot be
    recomputed: the need shrinks as the packs fill, so re-planning this morning
    against this evening's need would un-mark exactly the hours the charging
    was aimed at. Nothing ticked here, so there is no decision on record and
    both flags must stay false however cheap the hour looks.
    """
    system = planned()

    past = [h for h in system.coordinator.plan()["hours"] if h["past"]]

    assert past
    # prices are still prices: a gone hour is coloured from the day's ranking
    # rather than dropped into the "past" placeholder role it used to get
    assert not any(h["role"] == "past" for h in past)
    assert any(h["role"] == "dear" for h in past)
    # but nothing was decided during them, and nothing may be invented now
    assert not any(h["buy"] for h in past)
    assert not any(h["bought"] for h in past)


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


# --- the band holds still through the day, 2026-08-20 ----------------------
#
# Reported from the primary site: six green bars where four were configured,
# and climbing. The band was ranked over a rolling 24 h window, so it slid
# rightwards through the day while every hour it had already passed over kept
# the green it had been given. Ranked per calendar day it is exactly N, all
# day, and it is a price fact rather than a decision - the blue ring carries
# the decisions and is still recorded as it is taken.


#: tomorrow's bargains fall at different hours from today's, so "tomorrow got
#: its own ranking" cannot pass by accident on a set that happens to match
TOMORROW_CHEAP = (5, 6, 7, 8)


def spread_day(cheap_at: tuple[int, ...]) -> dict:
    """Two published days, each with its own cheap hours.

    Both days need a real spread. A flat day is correctly painted with nothing
    at all - the margin refuses to call its least-expensive hours cheap - so a
    fixture with a flat tomorrow would test the margin rather than the banding.
    """
    midnight = NOW.replace(hour=0, minute=0, second=0, microsecond=0)
    slots = []
    for offset in range(48):
        start = midnight + timedelta(hours=offset)
        hour = start.hour
        today = start.day == NOW.day
        wanted = cheap_at if today else TOMORROW_CHEAP
        price = 0.40
        if hour in wanted:
            price = 0.05 + 0.001 * wanted.index(hour)
        slots.append(
            {
                "start": start.isoformat(),
                "end": (start + timedelta(hours=1)).isoformat(),
                "value": price,
            }
        )
    return {"raw_today": slots}


@pytest.fixture
def through_the_day(build_system, monkeypatch):
    """The same published day, read at whatever hour the test asks for."""

    def _at(hour: int, cheap_at=(2, 3, 13, 14)):
        clock = NOW.replace(hour=hour)
        monkeypatch.setattr(coordinator_module.dt_util, "utcnow", lambda: clock)
        system = build_system(
            grid=300,
            units=(("093", 50.0), ("052", 50.0)),
            **{
                CONF_PRICE_SENSOR: PRICES,
                CONF_CHEAP_HOURS: 4,
                CONF_EXPENSIVE_HOURS: 3,
                CONF_FULL_CHARGE_MINUTES: 120,
            },
        )
        system.hass.states.set(PRICES, 0.40, spread_day(cheap_at))
        system.coordinator.mode = MODE_DYNAMIC
        return system

    return _at


def green_on_today(system) -> list[str]:
    today = NOW.strftime("%Y-%m-%d")
    return [
        h["start"][11:16]
        for h in system.coordinator.plan()["hours"]
        if h["role"] == "cheap" and h["start"][:10] == today
    ]


def test_the_band_is_exactly_cheap_hours_wide(through_the_day):
    """Four configured, four drawn. This is the reported fault."""
    assert len(green_on_today(through_the_day(hour=12))) == 4


def test_the_band_does_not_grow_as_the_day_goes_on(through_the_day):
    """The whole complaint: by teatime there were six, and rising.

    Same published prices, read at six different hours. The set must be
    identical every time - not merely the same size, the same hours.
    """
    seen = {hour: green_on_today(through_the_day(hour=hour))
            for hour in (0, 4, 8, 12, 16, 22)}

    assert all(v == seen[0] for v in seen.values()), seen
    assert seen[0] == ["02:00", "03:00", "13:00", "14:00"]


def test_the_morning_keeps_its_colour_in_the_evening(through_the_day):
    """Ranking the whole day is only allowed because the colour is a price, and
    a day that has started has all its prices known. What must not come back is
    a *decision* about a past hour - that is asserted separately."""
    evening = through_the_day(hour=22)

    green = green_on_today(evening)

    assert "02:00" in green and "03:00" in green


def test_tomorrow_gets_its_own_band(through_the_day):
    """Per calendar day, so paging the chart forward shows tomorrow's cheapest
    hours rather than a window that happens to straddle midnight."""
    system = through_the_day(hour=22)
    tomorrow = (NOW + timedelta(days=1)).strftime("%Y-%m-%d")

    ahead = [
        h["start"][11:16] for h in system.coordinator.plan()["hours"]
        if h["start"][:10] == tomorrow and h["role"] == "cheap"
    ]

    assert ahead == [f"{h:02d}:00" for h in TOMORROW_CHEAP]
    # and today's band is untouched by it, at 22:00 as at any other hour
    assert green_on_today(system) == ["02:00", "03:00", "13:00", "14:00"]


def test_the_sensor_counts_today_not_everything_published(through_the_day):
    """`cheap_hours` on the plan is today's band. Counting tomorrow's in would
    double the Plan sensor's figure the moment a supplier publishes the next
    day, which happens every afternoon."""
    plan = through_the_day(hour=22).coordinator.plan()

    assert len(plan["cheap_hours"]) == 4
    assert all(h["start"][:10] == NOW.strftime("%Y-%m-%d") for h in plan["cheap_hours"])


# -- how full, given what tomorrow costs --------------------------------------
#
# Asked for on 2026-09-19, alongside the fix that stops the buying being
# deferred across the peak. Whether to buy before the peak is settled by the
# peak; how *full* to buy is a fair question for tomorrow's prices to answer.
#
#   a much cheaper day coming  -> take only what tonight needs, top up then
#   a much dearer day coming   -> fill up while it is cheap
#
# The first is guarded on the owner having stated a floor. "Buy at least to"
# ships at 0, and reading that as "buy to nothing" is exactly the fault this
# was reported alongside - so with no floor set, nothing is lowered.


def two_days(today: float, tomorrow: float, dear_hour: int = 18) -> dict:
    """A flat price each day, so only the step between them is in play."""
    midnight = NOW.replace(hour=0, minute=0, second=0, microsecond=0)
    slots = []
    for i in range(48):
        start = midnight + timedelta(hours=i)
        price = today if start.day == NOW.day else tomorrow
        if start.hour == dear_hour and start.day == NOW.day:
            price = max(today, tomorrow) + 0.40
        slots.append(
            {
                "start": start.isoformat(),
                "end": (start + timedelta(hours=1)).isoformat(),
                "value": price,
            }
        )
    return {"raw_today": slots}


def priced(planned, today, tomorrow, **options):
    system = planned(**options)
    system.hass.states.set(PRICES, today, two_days(today, tomorrow))
    return system


def test_a_much_cheaper_tomorrow_reads_as_one(planned):
    system = priced(planned, 0.30, 0.10)

    assert system.coordinator.next_day_step() == pytest.approx(0.20, abs=0.02)


def test_a_cheaper_tomorrow_takes_only_what_the_floor_asks_for(planned):
    """The packs still get through tonight; the rest waits for the cheap day."""
    system = priced(planned, 0.30, 0.10, remaining=0.0)
    system.coordinator.buy_ceiling_min = 40.0

    assert system.coordinator.charge_ceiling() == 40.0


def test_without_a_floor_a_cheaper_tomorrow_changes_nothing(planned):
    """"Buy at least to" ships at 0, and lowering the ceiling to nothing is how
    the packs came to be flat at breakfast. No floor, no lowering."""
    system = priced(planned, 0.30, 0.10, remaining=0.0)
    system.coordinator.buy_ceiling_min = 0.0

    assert system.coordinator.charge_ceiling() == 100.0


def test_a_dearer_tomorrow_does_not_move_the_ceiling_either(planned):
    """Only the lowering half is in.

    Raising the ceiling on a dearer tomorrow would override both
    `charge_below_soc` and the solar headroom on most autumn days, and buying
    room the sun was going to fill does not make tomorrow cheaper - it exports
    the afternoon instead of storing it. Worth having, worth asking about
    first; pinned here so adding it later is a deliberate act.
    """
    system = priced(planned, 0.10, 0.30, remaining=7.0)
    system.coordinator.buy_ceiling_min = 40.0

    assert system.coordinator.next_day_step() < 0
    assert system.coordinator.charge_ceiling() == 50.0  # 7 kWh of 14 kWh packs


def test_a_similar_tomorrow_leaves_the_ceiling_alone(planned):
    """Within the margin the two days are the same day, and nothing moves.

    No sun left, so the solar ceiling is 100 and that is what should survive -
    the point is that a penny between the days does not move it either way.
    """
    system = priced(planned, 0.30, 0.29, remaining=0.0)
    system.coordinator.buy_ceiling_min = 40.0

    assert system.coordinator.next_day_step() == pytest.approx(0.01, abs=0.005)
    assert system.coordinator.charge_ceiling() == 100.0


def test_the_sun_outranks_the_next_day_either_way(planned):
    """Room the sun is expected to fill is free energy, and no price step on
    either side is a reason to buy it."""
    system = priced(planned, 0.10, 0.30, remaining=14.0)

    # 14 kWh of sun into 14 kWh of packs: buy nothing, whatever tomorrow costs
    assert system.coordinator.charge_ceiling() == 0.0


# -- the reported day, end to end ---------------------------------------------


def reported_day() -> dict:
    """2026-09-19, in shape: cheap now, a peak tonight, a cheaper day after.

    The packs sat at 5 % for four and a quarter hours, charged for 75 minutes
    and stopped at 31 % on a quarter boundary - price unchanged, ceiling at
    79 %, market price at -0.0012, and no dear quarter yet that day. Tomorrow's
    cheaper hours had taken the budget, and tomorrow arrives after the peak
    they were needed for.
    """
    midnight = NOW.replace(hour=0, minute=0, second=0, microsecond=0)
    slots = []
    for i in range(48):
        start = midnight + timedelta(hours=i)
        if start.day != NOW.day:
            price = 0.08          # the cheaper day, beyond the peak
        elif 16 <= start.hour < 19:
            price = 0.45          # tonight's peak
        elif 12 <= start.hour < 16:
            price = 0.13          # cheap, and on this side of it
        else:
            price = 0.25
        slots.append(
            {
                "start": start.isoformat(),
                "end": (start + timedelta(hours=1)).isoformat(),
                "value": price,
            }
        )
    return {"raw_today": slots}


async def test_it_buys_before_the_peak_though_tomorrow_is_cheaper(planned):
    """The fault, through the coordinator rather than the arithmetic.

    Ranked over the whole window, tomorrow's 0.08 wins and nothing is bought -
    and the packs go into a 0.45 evening on whatever they happen to hold. The
    two were never alternatives: energy bought tomorrow cannot serve tonight.
    """
    system = planned(remaining=0.0, soc=(20.0, 20.0))
    system.hass.states.set(PRICES, 0.13, reported_day())

    await system.coordinator._async_tick(None)

    assert system.coordinator.active_policy == POLICY_DYNAMIC_CHARGE
    assert system.coordinator.setpoint < 0


async def test_with_no_peak_ahead_the_cheaper_day_still_wins(planned):
    """The bound is a peak, not a curfew. Take the peak away and the ranking
    reaches into tomorrow again, which is what it is for."""
    flat = reported_day()
    for slot in flat["raw_today"]:
        if slot["value"] == 0.45:
            slot["value"] = 0.13
    system = planned(remaining=0.0, soc=(20.0, 20.0),
                     **{CONF_EXPENSIVE_HOURS: 0})
    system.hass.states.set(PRICES, 0.13, flat)

    await system.coordinator._async_tick(None)

    assert system.coordinator.active_policy != POLICY_DYNAMIC_CHARGE


async def test_the_sun_still_stops_the_buying_before_the_peak(planned):
    """The peak bound says *when* it may buy, never *how much*.

    Bounding the window at the peak makes buying more likely, not less - which
    is the point, but it is also the way this could have started filling packs
    that the sun was about to fill for free. The solar ceiling is untouched and
    still has the last word: a full day of sun into packs that hold exactly
    that much means buy nothing, peak ahead or not.
    """
    system = planned(remaining=14.0, soc=(20.0, 20.0))
    system.hass.states.set(PRICES, 0.13, reported_day())

    await system.coordinator._async_tick(None)

    assert system.coordinator.charge_ceiling() == 0.0
    assert system.coordinator.active_policy != POLICY_DYNAMIC_CHARGE


async def test_it_buys_up_to_the_sun_ceiling_and_no_further(planned):
    """Half a day of sun leaves half the packs to buy, and the peak bound
    decides only that it is bought today rather than after the peak."""
    system = planned(remaining=7.0, soc=(20.0, 20.0))
    system.hass.states.set(PRICES, 0.13, reported_day())

    await system.coordinator._async_tick(None)

    # 7 kWh of sun into 14 kWh of packs: buy to 50 %, leave the rest to the roof
    assert system.coordinator.charge_ceiling() == 50.0
    assert system.coordinator.active_policy == POLICY_DYNAMIC_CHARGE


async def test_packs_above_the_sun_ceiling_are_left_alone(planned):
    """Same day, same peak ahead, but the room is already the sun's."""
    system = planned(remaining=7.0, soc=(60.0, 60.0))
    system.hass.states.set(PRICES, 0.13, reported_day())

    await system.coordinator._async_tick(None)

    assert system.coordinator.active_policy != POLICY_DYNAMIC_CHARGE


# -- the reason, kept apart from the sun --------------------------------------
#
# `_buy_ceiling` used to answer "and was the sun the reason", and that flag was
# doing two jobs: naming the policy, and standing for "a solar ceiling exists
# at all". Lowering the ceiling for a *price* reason cleared it, which switched
# on `_sun_is_enough` - a fallback meant only for having no solar ceiling in
# the first place. Silent for a site that leaves the plain threshold at 0, and
# wrong for one that sets it.


def test_a_price_lowered_ceiling_does_not_wake_the_plain_threshold(planned):
    """The bug. With `solar_forecast_max` set, clearing the flag handed the
    decision to a fallback that had no business being consulted."""
    system = planned(remaining=6.0, **{CONF_SOLAR_FORECAST_MAX: 4.0})
    system.hass.states.set(PRICES, 0.13, reported_day())
    system.coordinator.buy_ceiling_min = 40.0

    ceiling, reason = system.coordinator._buy_ceiling()

    assert ceiling == 40.0
    assert reason == POLICY_CHEAPER_TOMORROW


async def test_and_it_still_buys(planned):
    """The visible half of the same bug: 6 kWh of sun clears a 4 kWh threshold,
    so the fallback would have said "sun is enough" and bought nothing."""
    system = planned(soc=(10.0, 10.0), remaining=6.0,
                     **{CONF_SOLAR_FORECAST_MAX: 4.0})
    system.hass.states.set(PRICES, 0.13, reported_day())
    system.coordinator.buy_ceiling_min = 40.0

    await system.coordinator._async_tick(None)

    assert system.coordinator.active_policy == POLICY_DYNAMIC_CHARGE


def test_the_sun_keeps_the_credit_when_nothing_lowers_it(planned):
    """No cheaper day, so the ceiling is the sun's and says so."""
    system = priced(planned, 0.30, 0.30, remaining=10.5)

    ceiling, reason = system.coordinator._buy_ceiling()

    assert ceiling == 25.0  # 10.5 kWh of sun into 14 kWh of packs
    assert reason == POLICY_SOLAR_HEADROOM


def test_a_floor_above_the_sun_ceiling_still_wins_and_keeps_its_name(planned):
    """Pre-existing and deliberate: "Buy at least to" is a floor under the
    computed ceiling, so it raises a gloomy forecast as well as capping a
    cheaper tomorrow. Nothing was lowered here, so the sun keeps the credit."""
    system = priced(planned, 0.30, 0.30, remaining=10.5)
    system.coordinator.buy_ceiling_min = 40.0

    ceiling, reason = system.coordinator._buy_ceiling()

    assert ceiling == 40.0
    assert reason == POLICY_SOLAR_HEADROOM


def test_a_cheaper_tomorrow_that_lowers_nothing_does_not_take_the_credit(planned):
    """The floor sits *above* the sun's ceiling, so a cheaper tomorrow has
    nothing left to take off. Naming it anyway would send someone looking at
    prices for a ceiling the roof is holding down."""
    system = planned(remaining=10.5)
    system.hass.states.set(PRICES, 0.13, reported_day())
    system.coordinator.buy_ceiling_min = 40.0

    ceiling, reason = system.coordinator._buy_ceiling()

    assert system.coordinator.next_day_step() > 0   # the lowering did run
    assert ceiling == 40.0
    assert reason == POLICY_SOLAR_HEADROOM


def test_with_no_forecast_at_all_the_reason_is_neither(planned):
    """Then the bare SoC threshold is holding it, and the fallback is exactly
    what should be consulted."""
    system = planned(**{CONF_FULL_CHARGE_MINUTES: 0})

    assert system.coordinator._buy_ceiling()[1] is None


# -- filling up before a dearer day -------------------------------------------
#
# Asked for, then asked to be optional: "het moet wel een optie zijn of je dat
# wilt". Off by default, because it overrides "only top up below" - a threshold
# somebody chose - and a setting like that should be opted into rather than
# arrive with an update.
#
# The safety is that it only acts where `reason is None`: the bare SoC
# threshold is holding the ceiling, not the sun. Room the roof is expected to
# fill is free energy, and buying it because tomorrow looks dear does not make
# tomorrow cheaper - it exports the afternoon instead of storing it.


def dear_tomorrow(planned, **options):
    """Today cheap, tomorrow dearer, and no solar forecast to speak for it."""
    system = planned(**{CONF_FULL_CHARGE_MINUTES: 120, CONF_CHARGE_BELOW_SOC: 40,
                        **options})
    system.hass.states.set(FORECAST, "unknown")
    system.hass.states.set(PRICES, 0.10, two_days(0.10, 0.40))
    return system


def test_off_by_default_the_threshold_still_holds(planned):
    system = dear_tomorrow(planned)

    assert system.coordinator.next_day_step() < 0
    assert system.coordinator._buy_ceiling() == (40.0, None)


def test_switched_on_it_fills_before_the_dearer_day(planned):
    system = dear_tomorrow(planned, **{CONF_FILL_BEFORE_DEAR_DAY: True})

    assert system.coordinator._buy_ceiling() == (100.0, None)


def test_it_never_buys_the_room_the_sun_was_going_to_fill(planned):
    """The line that does not move. 7 kWh of sun into 14 kWh of packs leaves
    the ceiling at 50, and a dear tomorrow is not a reason to buy the other
    half - that half is free."""
    system = planned(**{CONF_FILL_BEFORE_DEAR_DAY: True})
    system.hass.states.set(FORECAST, 7.0)
    system.hass.states.set(PRICES, 0.10, two_days(0.10, 0.40))

    ceiling, reason = system.coordinator._buy_ceiling()

    assert system.coordinator.next_day_step() < 0
    assert (ceiling, reason) == (50.0, POLICY_SOLAR_HEADROOM)


def test_a_similar_tomorrow_does_not_set_it_off(planned):
    system = planned(**{CONF_FULL_CHARGE_MINUTES: 120, CONF_CHARGE_BELOW_SOC: 40,
                        CONF_FILL_BEFORE_DEAR_DAY: True})
    system.hass.states.set(FORECAST, "unknown")
    system.hass.states.set(PRICES, 0.30, two_days(0.30, 0.31))

    assert system.coordinator._buy_ceiling() == (40.0, None)


def test_it_is_capped_by_the_hand_set_maximum(planned):
    """"Buy at most to" is still the last word on how full."""
    system = dear_tomorrow(planned, **{CONF_FILL_BEFORE_DEAR_DAY: True})
    system.coordinator.buy_ceiling_max = 80.0

    assert system.coordinator._buy_ceiling()[0] == 80.0

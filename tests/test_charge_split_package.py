"""The shipped charge-split package, and the chain it forms with the card.

This is four Home Assistant helpers feeding each other by entity id, ending in
two ids the card searches for by suffix. Every link is a plain string, so any
rename breaks it silently: the helpers keep working, the card simply stops
finding them and the split quietly disappears off the dashboard. Nothing
raises, nothing logs, and the number that vanishes is the one nobody notices
missing.

So the chain is asserted end to end, and the suffixes are read out of the card
itself rather than typed here twice.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "packages/battery_management_charge_split.yaml"
CARD = ROOT / "custom_components/battery_management/www/battery-management-card.js"


@pytest.fixture(scope="module")
def package() -> dict:
    return yaml.safe_load(PACKAGE.read_text(encoding="utf8"))


def entity_id(name: str) -> str:
    """What Home Assistant will call a helper given its friendly name.

    Home Assistant's own `slugify` is not importable under the stubbed test
    run, and these names are plain ASCII words, so the rule that matters -
    lower case, spaces to underscores - is applied directly.
    """
    return "sensor." + re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")


def test_the_template_sensors_feed_the_integrations(package):
    """Watts in, and the right watts."""
    produced = {entity_id(s["name"]) for s in package["template"][0]["sensor"]}
    consumed = {s["source"] for s in package["sensor"]}

    assert consumed == produced


def test_the_integrations_feed_the_daily_meters(package):
    produced = {entity_id(s["name"]) for s in package["sensor"]}
    consumed = {m["source"] for m in package["utility_meter"].values()}

    assert consumed == produced


def test_the_card_can_find_what_the_package_produces(package):
    """The last link, and the one most likely to rot.

    The card matches on a suffix so that a reader who renamed the pair still
    gets their split. That only holds while the package keeps producing ids
    ending that way - and the card would say nothing if it stopped.
    """
    suffixes = re.findall(r'bySuffix\("([^"]+)"\)', CARD.read_text(encoding="utf8"))
    assert len(suffixes) == 2, suffixes

    # the utility meter's entity id comes from its key, not its friendly name
    meters = {f"sensor.{key}" for key in package["utility_meter"]}
    assert {m for m in meters if any(m.endswith(s) for s in suffixes)} == meters


@pytest.mark.parametrize("source", ["sensor.accu_laadvermogen_totaal", "sensor.accu_laadvermogen_uit_net"])
def test_both_integrations_hold_their_value_between_bursts(package, source):
    """Two settings that are wrong by default for these sensors.

    The packs publish in bursts 10-30 s apart (gotcha 2). Trapezium
    interpolation would draw a ramp between two such readings that never
    happened, and without `max_sub_interval` the integral does not advance at
    all while the source sits still - so a pack charging steadily at 400 W for
    ten minutes would contribute nothing.

    Both failures produce a plausible-looking number rather than an error,
    which is why they are pinned rather than left to review.
    """
    helper = next(s for s in package["sensor"] if s["source"] == source)

    assert helper["method"] == "left"
    assert helper["max_sub_interval"] == "00:01:00"


def test_the_split_is_capped_at_what_the_packs_drew(package):
    """The arithmetic, in the one place it lives.

    Grid-sourced charging is `min(what the packs draw, what the meter imports)`,
    floored at zero so that exporting reads as "all of it was sun" rather than
    as a negative. Written as a Jinja min/max pair, so this checks both are
    still there - dropping the floor would let export subtract from the total.
    """
    grid = next(
        s for s in package["template"][0]["sensor"]
        if s["name"] == "Accu laadvermogen uit net"
    )
    state = " ".join(grid["state"].split())

    assert "[net, 0] | max" in state
    assert "| min" in state
    # and it is the meter, not a pack sensor, that gets floored
    assert "sensor.p1_meter_power" in state

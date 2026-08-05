"""The shipped automation blueprints.

Time windows come from Home Assistant's own Schedule helper rather than a
scheduler inside the integration, so these blueprints are the seam between the
two. They are user-facing config at family sites, where nobody is going to debug
a template - so validate them the way Home Assistant does, fill them in with
real inputs, and check the result is a valid automation.

Needs pytest-homeassistant-custom-component; skipped where that cannot be
installed. CI runs it on the real-Home-Assistant leg.
"""
from __future__ import annotations

import pathlib

import pytest

pytest.importorskip("pytest_homeassistant_custom_component")

from homeassistant.components.automation.config import (  # noqa: E402
    AUTOMATION_BLUEPRINT_SCHEMA,
    async_validate_config_item,
)
from homeassistant.components.blueprint.models import (  # noqa: E402
    Blueprint,
    BlueprintInputs,
)
from homeassistant.core import HomeAssistant  # noqa: E402
from homeassistant.util.yaml import loader as yaml_loader  # noqa: E402

BLUEPRINT_DIR = (
    pathlib.Path(__file__).resolve().parents[1]
    / "blueprints"
    / "automation"
    / "battery_management"
)

MODE_ENTITY = "select.battery_management_mode"

#: every blueprint, with a filled-in set of inputs a real user would give
CASES = {
    "mode_during_schedule.yaml": {
        "schedule_entity": "schedule.night",
        "mode_entity": MODE_ENTITY,
        "mode_during": "charge_only",
        "mode_outside": "grid_zero",
    },
    "be_full_by_time.yaml": {
        "deadline": "18:00:00",
        "minutes_sensor": "sensor.battery_management_minutes_to_full",
        "fast_charge_switch": "switch.battery_management_fast_charge_emergency",
        "margin": 20,
        "weekdays": ["mon", "tue", "wed", "thu", "fri", "sat", "sun"],
    },
    "mode_between_times.yaml": {
        "start_time": "23:00:00",
        "end_time": "07:00:00",
        "weekdays": ["mon", "tue", "wed", "thu", "fri"],
        "mode_entity": MODE_ENTITY,
        "mode_during": "pause",
        "mode_outside": "grid_zero",
    },
}


def load(name: str) -> Blueprint:
    path = BLUEPRINT_DIR / name
    return Blueprint(
        yaml_loader.load_yaml(str(path)),
        expected_domain="automation",
        path=str(path),
        schema=AUTOMATION_BLUEPRINT_SCHEMA,
    )


def test_every_blueprint_on_disk_is_covered():
    """A new blueprint must not slip in untested."""
    on_disk = {p.name for p in BLUEPRINT_DIR.glob("*.yaml")}
    assert on_disk == set(CASES)


@pytest.mark.parametrize("name", sorted(CASES))
def test_blueprint_is_valid(name):
    blueprint = load(name)

    assert blueprint.domain == "automation"
    assert blueprint.name
    # an import URL is what lets a family site add it from GitHub
    assert blueprint.metadata["source_url"].startswith("https://github.com/")


@pytest.mark.parametrize("name", sorted(CASES))
def test_blueprint_substitutes_into_a_valid_automation(name):
    """Catches a broken template or a stale input reference."""
    blueprint = load(name)
    inputs = BlueprintInputs(
        blueprint, {"use_blueprint": {"path": name, "input": CASES[name]}}
    )

    inputs.validate()
    config = inputs.async_substitute()

    assert config["mode"] == "single"
    triggers = config.get("triggers") or config.get("trigger")
    actions = config.get("actions") or config.get("action")
    assert triggers and actions
    # each blueprint must actually act on the integration it is written for
    targets = {a.get("target", {}).get("entity_id") for a in actions}
    assert targets & {MODE_ENTITY, CASES[name].get("fast_charge_switch")}


@pytest.mark.parametrize("name", sorted(CASES))
async def test_blueprint_passes_home_assistant_validation(hass: HomeAssistant, name):
    blueprint = load(name)
    inputs = BlueprintInputs(
        blueprint, {"use_blueprint": {"path": name, "input": CASES[name]}}
    )
    inputs.validate()

    # the same check Home Assistant runs when loading an automation
    validated = await async_validate_config_item(
        hass, "battery_management_blueprint_test", inputs.async_substitute()
    )
    assert validated is not None


def test_no_blueprint_offers_a_mode_that_does_not_exist():
    """A label pointing at a removed mode is a silent no-op at 23:00 at a
    family site. The reverse is fine: not every mode belongs in a schedule -
    "external plan" and "dynamic" are chosen for a season, not for an hour.
    """
    from custom_components.battery_management.const import MODE_GRID_ZERO, MODES

    for name in CASES:
        blueprint = load(name)
        for key in ("mode_during", "mode_outside"):
            if key not in blueprint.inputs:
                continue  # not every blueprint switches modes
            offered = {
                o["value"]
                for o in blueprint.inputs[key]["selector"]["select"]["options"]
            }
            assert offered <= set(MODES), f"{name}:{key} offers a dead mode"
            # whatever else it offers, you must be able to get back to normal
            assert MODE_GRID_ZERO in offered, f"{name}:{key}"

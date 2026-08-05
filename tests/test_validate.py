"""Config-flow validation rules."""
from __future__ import annotations

import pytest

from custom_components.battery_management.const import (
    CONF_CHARGE_LIMIT,
    CONF_DISCHARGE_LIMIT,
    CONF_FLOW_SELECT,
    CONF_MODE_SELECT,
    CONF_SOC_SENSOR,
    CONF_TARGET_NUMBER,
    CONF_UNIT_NAME,
)
from custom_components.battery_management.validate import validate_unit

from .conftest import FakeState, unit_config

# a percentage entity, as a real charging_limit reports itself
PERCENT = FakeState(100, {"unit_of_measurement": "%", "max": 100})
# the trap: a watt entity, which is what target_grid_power is
WATTS = FakeState(0, {"unit_of_measurement": "W", "max": 3500})


def states(mapping):
    return lambda entity_id: mapping.get(entity_id)


def test_accepts_a_correctly_filled_unit():
    cfg = unit_config("Batterij 01", "sim_01", with_limits=False)
    cfg[CONF_CHARGE_LIMIT] = "number.sim_01_charging_limit"
    cfg[CONF_DISCHARGE_LIMIT] = "number.sim_01_discharge_limit"

    errors = validate_unit(
        cfg,
        [],
        states(
            {
                "number.sim_01_charging_limit": PERCENT,
                "number.sim_01_discharge_limit": PERCENT,
            }
        ),
    )

    assert errors == {}


def test_accepts_a_unit_without_the_optional_limits():
    assert validate_unit(unit_config("Batterij 01", "sim_01", with_limits=False), []) == {}


def test_rejects_a_limit_pointing_at_the_target_entity():
    """The exact misconfiguration that made both packs ping-pong every tick."""
    cfg = unit_config("Batterij 01", "sim_01", with_limits=False)
    target = cfg[CONF_TARGET_NUMBER]
    cfg[CONF_CHARGE_LIMIT] = target
    cfg[CONF_DISCHARGE_LIMIT] = target

    errors = validate_unit(cfg, [], states({target: WATTS}))

    assert errors[CONF_CHARGE_LIMIT] == "duplicate_entity"
    assert errors[CONF_DISCHARGE_LIMIT] == "duplicate_entity"
    assert errors[CONF_TARGET_NUMBER] == "duplicate_entity"


def test_rejects_a_limit_that_is_a_power_entity():
    cfg = unit_config("Batterij 01", "sim_01", with_limits=False)
    cfg[CONF_CHARGE_LIMIT] = "number.some_other_power_entity"

    errors = validate_unit(cfg, [], states({"number.some_other_power_entity": WATTS}))

    assert errors == {CONF_CHARGE_LIMIT: "limit_not_percentage"}


def test_rejects_a_limit_whose_maximum_exceeds_100_percent():
    cfg = unit_config("Batterij 01", "sim_01", with_limits=False)
    cfg[CONF_CHARGE_LIMIT] = "number.bogus"
    bogus = FakeState(50, {"max": 3500})  # no unit, but cannot be a percentage

    errors = validate_unit(cfg, [], states({"number.bogus": bogus}))

    assert errors == {CONF_CHARGE_LIMIT: "limit_not_percentage"}


def test_accepts_a_limit_entity_that_reports_no_unit_at_all():
    """Stay permissive: an odd firmware must not be blocked."""
    cfg = unit_config("Batterij 01", "sim_01", with_limits=False)
    cfg[CONF_CHARGE_LIMIT] = "number.quiet"

    errors = validate_unit(cfg, [], states({"number.quiet": FakeState(90)}))

    assert errors == {}


def test_accepts_a_limit_entity_that_is_not_in_the_state_machine_yet():
    cfg = unit_config("Batterij 01", "sim_01", with_limits=False)
    cfg[CONF_CHARGE_LIMIT] = "number.not_loaded"

    assert validate_unit(cfg, [], states({})) == {}


def test_rejects_the_two_selects_being_the_same_entity():
    cfg = unit_config("Batterij 01", "sim_01", with_limits=False)
    cfg[CONF_FLOW_SELECT] = cfg[CONF_MODE_SELECT]

    errors = validate_unit(cfg, [])

    assert errors[CONF_MODE_SELECT] == "duplicate_entity"
    assert errors[CONF_FLOW_SELECT] == "duplicate_entity"


@pytest.mark.parametrize("name", ["Batterij 01", "batterij 01", "  Batterij 01  "])
def test_rejects_a_name_another_unit_already_uses(name):
    """unit_status is keyed by name, so duplicates would collapse two packs."""
    cfg = unit_config(name, "sim_02", with_limits=False)

    errors = validate_unit(cfg, ["Batterij 01"])

    assert errors == {CONF_UNIT_NAME: "duplicate_name"}


@pytest.mark.parametrize("name", ["", "   "])
def test_rejects_an_empty_name(name):
    cfg = unit_config(name, "sim_01", with_limits=False)

    assert validate_unit(cfg, []) == {CONF_UNIT_NAME: "name_required"}


def test_soc_sensor_may_not_double_as_a_limit():
    cfg = unit_config("Batterij 01", "sim_01", with_limits=False)
    cfg[CONF_CHARGE_LIMIT] = cfg[CONF_SOC_SENSOR]

    errors = validate_unit(cfg, [])

    assert errors[CONF_CHARGE_LIMIT] == "duplicate_entity"
    assert errors[CONF_SOC_SENSOR] == "duplicate_entity"


# -- the selects must accept what we will send them ---------------------------

MODE_SELECT_OK = FakeState(
    "self_consumption",
    {"options": ["self_consumption", "third_party_control"]},
)
FLOW_SELECT_OK = FakeState("charge", {"options": ["charge", "discharge"]})
MODE_SELECT_RENAMED = FakeState(
    "Self consumption", {"options": ["Self consumption", "Third party"]}
)


def test_accepts_selects_that_offer_the_expected_options():
    cfg = unit_config("Batterij 01", "sim_01", with_limits=False)

    errors = validate_unit(
        cfg,
        [],
        states({cfg[CONF_MODE_SELECT]: MODE_SELECT_OK, cfg[CONF_FLOW_SELECT]: FLOW_SELECT_OK}),
    )

    assert errors == {}


def test_a_mode_select_offering_a_different_list_is_accepted():
    """Real hardware: the two units at the primary site disagree.

    Unit 093 offers six modes; unit 052 offers only two, because its own P1
    meter is not connected so the firmware hides self-consumption entirely.
    Which option means what is chosen from the entity's own list in the next
    wizard step, so this must not be rejected here.
    """
    cfg = unit_config("Batterij 02", "sim_02", with_limits=False)
    meterless = FakeState(
        "third_party_control", {"options": ["third_party_control", "custom_mode"]}
    )

    assert validate_unit(cfg, [], states({cfg[CONF_MODE_SELECT]: meterless})) == {}


def test_the_flow_select_is_still_checked_literally():
    """charge / discharge are written as-is, so they must exist."""
    cfg = unit_config("Batterij 01", "sim_01", with_limits=False)
    renamed = FakeState("Laden", {"options": ["Laden", "Ontladen"]})

    errors = validate_unit(cfg, [], states({cfg[CONF_FLOW_SELECT]: renamed}))

    assert errors == {CONF_FLOW_SELECT: "missing_options"}


def test_rejects_a_flow_select_that_cannot_discharge():
    cfg = unit_config("Batterij 01", "sim_01", with_limits=False)
    half = FakeState("charge", {"options": ["charge"]})

    errors = validate_unit(cfg, [], states({cfg[CONF_FLOW_SELECT]: half}))

    assert errors == {CONF_FLOW_SELECT: "missing_options"}


def test_a_select_that_publishes_no_options_is_accepted():
    """Permissive: do not block an unusual integration on a guess."""
    cfg = unit_config("Batterij 01", "sim_01", with_limits=False)
    quiet = FakeState("something", {})

    assert validate_unit(cfg, [], states({cfg[CONF_MODE_SELECT]: quiet})) == {}


# -- the shadow settings ------------------------------------------------------


def test_the_grid_meter_is_refused_as_battery_power():
    """The obvious wrong pick: net demand = grid + battery, so this doubles it,
    the setpoint runs away, and a month of shadow data is quietly worthless."""
    from custom_components.battery_management.const import CONF_BATTERY_POWER_SENSOR
    from custom_components.battery_management.validate import validate_shadow

    errors = validate_shadow(
        {CONF_BATTERY_POWER_SENSOR: "sensor.p1_meter_power"}, "sensor.p1_meter_power"
    )

    assert errors == {CONF_BATTERY_POWER_SENSOR: "battery_power_is_grid"}


def test_a_real_battery_power_sensor_is_accepted():
    from custom_components.battery_management.const import CONF_BATTERY_POWER_SENSOR
    from custom_components.battery_management.validate import validate_shadow

    assert (
        validate_shadow(
            {CONF_BATTERY_POWER_SENSOR: "sensor.packs_power"}, "sensor.p1_meter_power"
        )
        == {}
    )


def test_leaving_it_empty_is_the_normal_case():
    from custom_components.battery_management.validate import validate_shadow

    assert validate_shadow({}, "sensor.p1_meter_power") == {}

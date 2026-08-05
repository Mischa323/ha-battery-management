"""Resolving a unit's entities from one Anker device."""
from __future__ import annotations

from custom_components.battery_management.const import (
    CONF_CHARGE_LIMIT,
    CONF_DISCHARGE_LIMIT,
    CONF_FLOW_SELECT,
    CONF_MODE_SELECT,
    CONF_SOC_SENSOR,
    CONF_TARGET_NUMBER,
)
from custom_components.battery_management.discovery import match_unit_entities

# the real entity ids from the primary site, unit 093
PREFIX = "anker_solix_solarbank_max_ac_093"
REAL_DEVICE = [
    f"select.{PREFIX}_operating_mode_device_runs_by_command_in_third_party_controlled",
    f"select.{PREFIX}_grid_flow",
    f"number.{PREFIX}_target_grid_power",
    f"sensor.{PREFIX}_soc",
    f"number.{PREFIX}_charging_limit",
    f"number.{PREFIX}_discharge_limit",
    # noise a real device also exposes
    f"sensor.{PREFIX}_battery_discharging_power",
    f"sensor.{PREFIX}_ac_output",
    f"binary_sensor.{PREFIX}_charging",
    f"number.{PREFIX}_backup_reserve",
]


def test_resolves_a_real_anker_device():
    matches = match_unit_entities(REAL_DEVICE)

    assert matches == {
        CONF_MODE_SELECT: f"select.{PREFIX}_operating_mode_device_runs_by_command_in_third_party_controlled",
        CONF_FLOW_SELECT: f"select.{PREFIX}_grid_flow",
        CONF_TARGET_NUMBER: f"number.{PREFIX}_target_grid_power",
        CONF_SOC_SENSOR: f"sensor.{PREFIX}_soc",
        CONF_CHARGE_LIMIT: f"number.{PREFIX}_charging_limit",
        CONF_DISCHARGE_LIMIT: f"number.{PREFIX}_discharge_limit",
    }


def test_resolves_the_area_named_second_unit():
    """Unit 052 is named after its area, not the model."""
    matches = match_unit_entities(
        [
            "select.tuin_batterij_02_operating_mode_device_runs_by_command_in_third_party_controlled",
            "select.tuin_batterij_02_grid_flow",
            "number.tuin_batterij_02_target_grid_power",
            "sensor.tuin_batterij_02_soc",
            "number.tuin_batterij_02_charging_limit",
            "number.tuin_batterij_02_discharge_limit",
        ]
    )

    assert matches[CONF_TARGET_NUMBER] == "number.tuin_batterij_02_target_grid_power"
    assert matches[CONF_SOC_SENSOR] == "sensor.tuin_batterij_02_soc"


def test_discharge_limit_is_not_mistaken_for_the_charge_limit():
    """'discharge_limit' contains 'charge_limit' - the classic trap."""
    matches = match_unit_entities(
        ["number.x_charging_limit", "number.x_discharge_limit"]
    )

    assert matches[CONF_CHARGE_LIMIT] == "number.x_charging_limit"
    assert matches[CONF_DISCHARGE_LIMIT] == "number.x_discharge_limit"


def test_target_power_is_not_mistaken_for_the_grid_flow_select():
    """Both contain 'grid'; only the select may win the flow field."""
    matches = match_unit_entities(
        ["number.x_target_grid_power", "select.x_grid_flow"]
    )

    assert matches[CONF_FLOW_SELECT] == "select.x_grid_flow"
    assert matches[CONF_TARGET_NUMBER] == "number.x_target_grid_power"


def test_ambiguity_leaves_the_field_empty():
    """Two equally plausible candidates: let the user choose, do not guess."""
    matches = match_unit_entities(["sensor.a_soc", "sensor.b_soc"])

    assert CONF_SOC_SENSOR not in matches


def test_a_more_specific_match_beats_a_vaguer_one():
    matches = match_unit_entities(
        ["sensor.x_state_of_charge", "sensor.x_soc"]
    )

    assert matches[CONF_SOC_SENSOR] == "sensor.x_soc"


def test_missing_optional_entities_are_simply_absent():
    matches = match_unit_entities(
        [
            "select.x_operating_mode",
            "select.x_grid_flow",
            "number.x_target_grid_power",
            "sensor.x_soc",
        ]
    )

    assert CONF_CHARGE_LIMIT not in matches
    assert CONF_DISCHARGE_LIMIT not in matches
    assert len(matches) == 4


def test_an_unrelated_device_yields_nothing():
    assert match_unit_entities(["sensor.washing_machine_power", "switch.lamp"]) == {}

"""The config entry -> coordinator contract.

Regression guard: the per-unit dicts are stored under the CONF_* key names, not
under `UnitConfig`'s field names. Constructing the coordinator with the exact
shape the config flow writes must work, or the integration cannot start at all.
"""
from __future__ import annotations

from custom_components.battery_management.const import (
    CONF_GRID_POWER,
    CONF_KP,
    CONF_UNITS,
)
from custom_components.battery_management.coordinator import BatteryCoordinator

from .conftest import FakeEntry, FakeHass, unit_config


def test_accepts_the_shape_the_config_flow_stores():
    entry = FakeEntry(
        {
            CONF_GRID_POWER: "sensor.p1_meter_power",
            CONF_UNITS: [
                unit_config("Batterij 01", "anker_solix_solarbank_max_ac_093"),
                unit_config("Batterij 02", "tuin_batterij_02"),
            ],
        }
    )

    coordinator = BatteryCoordinator(FakeHass(), entry)

    units = coordinator._units
    assert [u.name for u in units] == ["Batterij 01", "Batterij 02"]
    assert units[0].flow_select == "select.anker_solix_solarbank_max_ac_093_grid_flow"
    assert units[1].target_number == "number.tuin_batterij_02_target_grid_power"


def test_accepts_units_without_the_optional_limit_entities():
    entry = FakeEntry(
        {
            CONF_GRID_POWER: "sensor.p1_meter_power",
            CONF_UNITS: [unit_config("Batterij 01", "unit_a", with_limits=False)],
        }
    )

    coordinator = BatteryCoordinator(FakeHass(), entry)

    assert coordinator._units[0].charge_limit is None
    assert coordinator._units[0].discharge_limit is None


def test_options_override_the_original_setup_values():
    entry = FakeEntry(
        data={
            CONF_GRID_POWER: "sensor.p1_meter_power",
            CONF_UNITS: [unit_config("Batterij 01", "unit_a")],
            CONF_KP: 0.25,
        },
        options={CONF_KP: 0.4},
    )

    coordinator = BatteryCoordinator(FakeHass(), entry)

    assert coordinator._kp == 0.4


def test_the_manifest_keys_are_sorted_the_way_hassfest_wants():
    """domain, name, then alphabetical. Caught in CI otherwise, not here."""
    import json
    import pathlib

    manifest = pathlib.Path(__file__).resolve().parents[1] / (
        "custom_components/battery_management/manifest.json"
    )
    keys = list(json.loads(manifest.read_text(encoding="utf-8")))

    assert keys[:2] == ["domain", "name"]
    assert keys[2:] == sorted(keys[2:])

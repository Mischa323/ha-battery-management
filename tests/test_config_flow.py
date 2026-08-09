"""The wizard and the options flow, driven by Home Assistant itself.

These need `pytest-homeassistant-custom-component`, which pulls in a real Home
Assistant, so they are skipped where that cannot be installed (the maintainer's
Windows box). CI runs them on the real-Home-Assistant leg of the matrix.
"""
from __future__ import annotations

import pytest

pytest.importorskip("pytest_homeassistant_custom_component")

from homeassistant import config_entries  # noqa: E402
from homeassistant.core import HomeAssistant  # noqa: E402
from homeassistant.data_entry_flow import FlowResultType  # noqa: E402

from custom_components.battery_management.const import (  # noqa: E402
    CONF_CHARGE_LIMIT,
    CONF_DISCHARGE_LIMIT,
    CONF_FLOW_SELECT,
    CONF_GRID_POWER,
    CONF_CHEAP_HOURS,
    CONF_KP,
    CONF_MODE_CONTROL,
    CONF_MODE_SAFE,
    CONF_MODE_SELECT,
    CONF_PRICE_SENSOR,
    CONF_SOC_SENSOR,
    CONF_TARGET_NUMBER,
    CONF_UNIT_COUNT,
    CONF_UNIT_NAME,
    CONF_PHASE_DETECT,
    CONF_PHASE_LIMIT_AMPS,
    CONF_PHASE_MARGIN,
    CONF_PHASE_PROBE_SECONDS,
    CONF_PHASE_REDETECT,
    CONF_PHASE_SENSORS,
    CONF_PHASE_VOLTAGE,
    CONF_UNITS,
    DOMAIN,
)


@pytest.fixture(autouse=True)
def _auto_enable(enable_custom_integrations):
    yield


def unit_input(index: int, **overrides) -> dict:
    prefix = f"sim_{index:02d}"
    data = {
        CONF_UNIT_NAME: f"Batterij {index}",
        CONF_MODE_SELECT: f"select.{prefix}_operating_mode",
        CONF_FLOW_SELECT: f"select.{prefix}_grid_flow",
        CONF_TARGET_NUMBER: f"number.{prefix}_target_grid_power",
        CONF_SOC_SENSOR: f"sensor.{prefix}_soc",
    }
    data.update(overrides)
    return data


async def start_wizard(hass: HomeAssistant, unit_count: int) -> dict:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"

    return await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_GRID_POWER: "sensor.p1_meter_power", CONF_UNIT_COUNT: unit_count},
    )


async def skip_device_step(hass: HomeAssistant, result: dict) -> dict:
    """The device picker is optional; take the manual route."""
    assert result["step_id"] == "unit_device"
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["step_id"] == "unit"
    return result


async def add_unit(hass: HomeAssistant, result: dict, index: int, **overrides) -> dict:
    """Walk one unit all the way through: entities, then what its modes mean."""
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], unit_input(index, **overrides)
    )
    if result["type"] is not FlowResultType.FORM or result["step_id"] != "unit_modes":
        return result  # rejected, or already finished
    return await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_MODE_CONTROL: "third_party_control", CONF_MODE_SAFE: "self_consumption"},
    )


async def test_wizard_completes_with_three_units(hass: HomeAssistant):
    """N>2 has only ever been exercised in `_distribute`, not through the flow."""
    result = await start_wizard(hass, 3)

    for index in (1, 2, 3):
        result = await skip_device_step(hass, result)
        result = await add_unit(hass, result, index)

    assert result["type"] is FlowResultType.CREATE_ENTRY
    units = result["data"][CONF_UNITS]
    assert [u[CONF_UNIT_NAME] for u in units] == [
        "Batterij 1",
        "Batterij 2",
        "Batterij 3",
    ]
    assert units[2][CONF_TARGET_NUMBER] == "number.sim_03_target_grid_power"


async def test_wizard_completes_with_a_single_unit(hass: HomeAssistant):
    result = await start_wizard(hass, 1)
    result = await skip_device_step(hass, result)
    result = await add_unit(hass, result, 1)

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert len(result["data"][CONF_UNITS]) == 1


async def test_wizard_refuses_a_limit_pointing_at_the_target(hass: HomeAssistant):
    result = await start_wizard(hass, 2)
    result = await skip_device_step(hass, result)

    bad = unit_input(1)
    bad[CONF_CHARGE_LIMIT] = bad[CONF_TARGET_NUMBER]
    result = await hass.config_entries.flow.async_configure(result["flow_id"], bad)

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "unit"
    assert result["errors"][CONF_CHARGE_LIMIT] == "duplicate_entity"


async def test_wizard_refuses_two_units_with_the_same_name(hass: HomeAssistant):
    result = await start_wizard(hass, 2)
    result = await skip_device_step(hass, result)
    result = await add_unit(hass, result, 1, **{CONF_UNIT_NAME: "Batterij"})
    result = await skip_device_step(hass, result)
    result = await add_unit(hass, result, 2, **{CONF_UNIT_NAME: "Batterij"})

    assert result["type"] is FlowResultType.FORM
    assert result["errors"][CONF_UNIT_NAME] == "duplicate_name"


async def test_a_rejected_unit_can_be_corrected_and_the_wizard_continues(
    hass: HomeAssistant,
):
    result = await start_wizard(hass, 1)
    result = await skip_device_step(hass, result)

    # a wrong-domain pick is already stopped by the selector schema, so use the
    # mistake that actually gets through: two number entities, same one twice
    bad = unit_input(1)
    bad[CONF_DISCHARGE_LIMIT] = bad[CONF_TARGET_NUMBER]
    result = await hass.config_entries.flow.async_configure(result["flow_id"], bad)
    assert result["type"] is FlowResultType.FORM
    assert result["errors"][CONF_DISCHARGE_LIMIT] == "duplicate_entity"

    result = await add_unit(hass, result, 1)
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def _create_entry(hass: HomeAssistant, unit_count: int = 2):
    result = await start_wizard(hass, unit_count)
    for index in range(1, unit_count + 1):
        result = await skip_device_step(hass, result)
        result = await add_unit(hass, result, index)
    await hass.async_block_till_done()
    return hass.config_entries.async_entries(DOMAIN)[0]


async def test_options_menu_offers_every_section(hass: HomeAssistant):
    entry = await _create_entry(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)

    assert result["type"] is FlowResultType.MENU
    assert set(result["menu_options"]) == {
        "tuning",
        "units",
        "dynamic",
        "phases",
        "shadow",
    }


async def test_options_dynamic_stores_a_price_sensor(hass: HomeAssistant):
    entry = await _create_entry(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "dynamic"}
    )
    assert result["step_id"] == "dynamic"

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {CONF_PRICE_SENSOR: "sensor.energy_prices", CONF_CHEAP_HOURS: 4},
    )
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options[CONF_PRICE_SENSOR] == "sensor.energy_prices"
    assert entry.options[CONF_CHEAP_HOURS] == 4


async def test_options_dynamic_can_clear_the_price_sensor_again(hass: HomeAssistant):
    """An emptied picker must clear, not silently keep the old sensor."""
    entry = await _create_entry(hass)

    for payload in (
        {CONF_PRICE_SENSOR: "sensor.energy_prices"},
        {CONF_CHEAP_HOURS: 4},
    ):
        result = await hass.config_entries.options.async_init(entry.entry_id)
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"next_step_id": "dynamic"}
        )
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], payload
        )
        await hass.async_block_till_done()

    assert CONF_PRICE_SENSOR not in entry.options


async def test_options_tuning_saves_without_touching_the_units(hass: HomeAssistant):
    entry = await _create_entry(hass)
    original_units = entry.data[CONF_UNITS]

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "tuning"}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_KP: 0.4}
    )
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options[CONF_KP] == 0.4
    assert entry.data[CONF_UNITS] == original_units


async def test_options_units_can_repair_a_wrong_entity(hass: HomeAssistant):
    """The whole point: fixing a mis-picked entity without deleting the entry."""
    entry = await _create_entry(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "units"}
    )
    assert result["step_id"] == "units"

    for index in (1, 2):
        fixed = unit_input(index)
        fixed[CONF_CHARGE_LIMIT] = f"number.sim_{index:02d}_charging_limit"
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], fixed
        )
        # each unit's entities are followed by what its modes mean
        assert result["step_id"] == "unit_modes"
        result = await hass.config_entries.options.async_configure(
            result["flow_id"],
            {
                CONF_MODE_CONTROL: "third_party_control",
                CONF_MODE_SAFE: "self_consumption",
            },
        )
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.data[CONF_UNITS][0][CONF_CHARGE_LIMIT] == (
        "number.sim_01_charging_limit"
    )
    assert entry.data[CONF_UNITS][1][CONF_CHARGE_LIMIT] == (
        "number.sim_02_charging_limit"
    )


async def test_options_units_rejects_a_bad_repair(hass: HomeAssistant):
    entry = await _create_entry(hass, unit_count=1)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "units"}
    )
    bad = unit_input(1)
    bad[CONF_CHARGE_LIMIT] = bad[CONF_TARGET_NUMBER]
    result = await hass.config_entries.options.async_configure(result["flow_id"], bad)

    assert result["type"] is FlowResultType.FORM
    assert result["errors"][CONF_CHARGE_LIMIT] == "duplicate_entity"


async def test_the_mode_step_offers_what_the_entity_actually_has(hass: HomeAssistant):
    """Unit 052 at the primary site: no P1 meter, so no self-consumption mode.

    The wizard must offer that unit's own two options rather than a fixed pair,
    and must accept an empty hand-back — the only safe letting-go a meterless
    pack has is being commanded 0 W.
    """
    hass.states.async_set(
        "select.sim_01_operating_mode",
        "third_party_control",
        {"options": ["third_party_control", "custom_mode"]},
    )

    result = await start_wizard(hass, 1)
    result = await skip_device_step(hass, result)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], unit_input(1)
    )
    assert result["step_id"] == "unit_modes"
    assert "custom_mode" in result["description_placeholders"]["options"]
    assert "self_consumption" not in result["description_placeholders"]["options"]

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_MODE_CONTROL: "third_party_control"}
    )
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    unit = result["data"][CONF_UNITS][0]
    assert unit[CONF_MODE_CONTROL] == "third_party_control"
    assert not unit.get(CONF_MODE_SAFE)  # nothing to return to, and that is fine


async def test_the_mode_mapping_can_be_corrected_afterwards(hass: HomeAssistant):
    """It decides what a pack does when the coordinator lets go, so it has to
    stay reachable — it was only in the wizard, which meant a wrong choice
    could not be fixed without deleting the entry."""
    entry = await _create_entry(hass, unit_count=1)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "units"}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], unit_input(1)
    )
    assert result["step_id"] == "unit_modes"

    # clear the hand-back: a pack with no meter of its own has nothing to
    # return to, and must simply be commanded 0
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_MODE_CONTROL: "third_party_control"}
    )
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    unit = entry.data[CONF_UNITS][0]
    assert unit[CONF_MODE_CONTROL] == "third_party_control"
    assert not unit.get(CONF_MODE_SAFE)


async def test_options_phases_stores_the_fuse_settings(hass: HomeAssistant):
    """Opt-in: the sensors are what switch the protection on."""
    entry = await _create_entry(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "phases"}
    )
    assert result["step_id"] == "phases"

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            CONF_PHASE_SENSORS: [
                "sensor.p1_meter_power_phase_1",
                "sensor.p1_meter_power_phase_2",
                "sensor.p1_meter_power_phase_3",
            ],
            CONF_PHASE_LIMIT_AMPS: 35,
            CONF_PHASE_VOLTAGE: 230,
            CONF_PHASE_MARGIN: 5,
            CONF_PHASE_DETECT: True,
            CONF_PHASE_REDETECT: False,
            CONF_PHASE_PROBE_SECONDS: 25,
        },
    )
    await hass.async_block_till_done()

    assert entry.options[CONF_PHASE_LIMIT_AMPS] == 35
    assert len(entry.options[CONF_PHASE_SENSORS]) == 3


async def test_clearing_the_phase_sensors_switches_the_protection_off(
    hass: HomeAssistant,
):
    """Otherwise it would keep guarding with yesterday's entities."""
    entry = await _create_entry(hass)
    hass.config_entries.async_update_entry(
        entry, options={CONF_PHASE_SENSORS: ["sensor.p1_meter_power_phase_1"]}
    )

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"next_step_id": "phases"}
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_PHASE_LIMIT_AMPS: 25}
    )
    await hass.async_block_till_done()

    assert not entry.options.get(CONF_PHASE_SENSORS)

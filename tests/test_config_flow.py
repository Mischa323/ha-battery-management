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
    CONF_KP,
    CONF_MODE_SELECT,
    CONF_SOC_SENSOR,
    CONF_TARGET_NUMBER,
    CONF_UNIT_COUNT,
    CONF_UNIT_NAME,
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


async def test_wizard_completes_with_three_units(hass: HomeAssistant):
    """N>2 has only ever been exercised in `_distribute`, not through the flow."""
    result = await start_wizard(hass, 3)

    for index in (1, 2, 3):
        result = await skip_device_step(hass, result)
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], unit_input(index)
        )

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
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], unit_input(1)
    )

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
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], unit_input(1, **{CONF_UNIT_NAME: "Batterij"})
    )
    result = await skip_device_step(hass, result)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], unit_input(2, **{CONF_UNIT_NAME: "Batterij"})
    )

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

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], unit_input(1)
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def _create_entry(hass: HomeAssistant, unit_count: int = 2):
    result = await start_wizard(hass, unit_count)
    for index in range(1, unit_count + 1):
        result = await skip_device_step(hass, result)
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], unit_input(index)
        )
    await hass.async_block_till_done()
    return hass.config_entries.async_entries(DOMAIN)[0]


async def test_options_menu_offers_tuning_and_units(hass: HomeAssistant):
    entry = await _create_entry(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)

    assert result["type"] is FlowResultType.MENU
    assert set(result["menu_options"]) == {"tuning", "units"}


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

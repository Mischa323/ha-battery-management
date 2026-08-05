"""Config flow for Solarbank Coordinator."""
from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.core import callback
from homeassistant.helpers import selector

from .const import (
    CONF_BIAS,
    CONF_CHARGE_LIMIT,
    CONF_DEADBAND,
    CONF_DISCHARGE_LIMIT,
    CONF_FLOW_SELECT,
    CONF_GRID_POWER,
    CONF_INTERVAL,
    CONF_KP,
    CONF_MIN_OUTPUT,
    CONF_MODE_SELECT,
    CONF_SOC_SENSOR,
    CONF_TARGET_NUMBER,
    CONF_UNIT_COUNT,
    CONF_UNIT_MAX,
    CONF_UNIT_NAME,
    CONF_UNITS,
    DEFAULT_BIAS,
    DEFAULT_DEADBAND,
    DEFAULT_INTERVAL,
    DEFAULT_KP,
    DEFAULT_MIN_OUTPUT,
    DEFAULT_UNIT_MAX,
    DOMAIN,
)

_SENSOR = selector.EntitySelector(selector.EntitySelectorConfig(domain="sensor"))
_SELECT = selector.EntitySelector(selector.EntitySelectorConfig(domain="select"))
_NUMBER = selector.EntitySelector(selector.EntitySelectorConfig(domain="number"))
_OPT_NUMBER = selector.EntitySelector(
    selector.EntitySelectorConfig(domain="number")
)


def _options_schema(defaults: dict) -> vol.Schema:
    return vol.Schema(
        {
            vol.Optional(CONF_BIAS, default=defaults.get(CONF_BIAS, DEFAULT_BIAS)): int,
            vol.Optional(CONF_DEADBAND, default=defaults.get(CONF_DEADBAND, DEFAULT_DEADBAND)): int,
            vol.Optional(CONF_KP, default=defaults.get(CONF_KP, DEFAULT_KP)): vol.Coerce(float),
            vol.Optional(CONF_INTERVAL, default=defaults.get(CONF_INTERVAL, DEFAULT_INTERVAL)): int,
            vol.Optional(CONF_MIN_OUTPUT, default=defaults.get(CONF_MIN_OUTPUT, DEFAULT_MIN_OUTPUT)): int,
            vol.Optional(CONF_UNIT_MAX, default=defaults.get(CONF_UNIT_MAX, DEFAULT_UNIT_MAX)): int,
        }
    )


class SolarbankConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle the setup wizard."""

    VERSION = 1

    def __init__(self) -> None:
        self._data: dict[str, Any] = {}
        self._units: list[dict] = []
        self._unit_total = 2
        self._unit_index = 0

    async def async_step_user(self, user_input: dict | None = None) -> ConfigFlowResult:
        if user_input is not None:
            self._unit_total = user_input.pop(CONF_UNIT_COUNT)
            self._data.update(user_input)
            return await self.async_step_unit()

        schema = vol.Schema(
            {
                vol.Required(CONF_GRID_POWER): _SENSOR,
                vol.Required(CONF_UNIT_COUNT, default=2): vol.All(int, vol.Range(min=1, max=6)),
                vol.Optional(CONF_BIAS, default=DEFAULT_BIAS): int,
                vol.Optional(CONF_DEADBAND, default=DEFAULT_DEADBAND): int,
                vol.Optional(CONF_KP, default=DEFAULT_KP): vol.Coerce(float),
                vol.Optional(CONF_INTERVAL, default=DEFAULT_INTERVAL): int,
                vol.Optional(CONF_MIN_OUTPUT, default=DEFAULT_MIN_OUTPUT): int,
                vol.Optional(CONF_UNIT_MAX, default=DEFAULT_UNIT_MAX): int,
            }
        )
        return self.async_show_form(step_id="user", data_schema=schema)

    async def async_step_unit(self, user_input: dict | None = None) -> ConfigFlowResult:
        if user_input is not None:
            self._units.append(user_input)
            self._unit_index += 1
            if self._unit_index >= self._unit_total:
                self._data[CONF_UNITS] = self._units
                return self.async_create_entry(
                    title="Solarbank Coordinator", data=self._data
                )
            return await self.async_step_unit()

        schema = vol.Schema(
            {
                vol.Required(CONF_UNIT_NAME, default=f"Batterij {self._unit_index + 1}"): str,
                vol.Required(CONF_MODE_SELECT): _SELECT,
                vol.Required(CONF_FLOW_SELECT): _SELECT,
                vol.Required(CONF_TARGET_NUMBER): _NUMBER,
                vol.Required(CONF_SOC_SENSOR): _SENSOR,
                vol.Optional(CONF_CHARGE_LIMIT): _OPT_NUMBER,
                vol.Optional(CONF_DISCHARGE_LIMIT): _OPT_NUMBER,
            }
        )
        return self.async_show_form(
            step_id="unit",
            data_schema=schema,
            description_placeholders={
                "index": str(self._unit_index + 1),
                "total": str(self._unit_total),
            },
        )

    @staticmethod
    @callback
    def async_get_options_flow(entry: ConfigEntry) -> OptionsFlow:
        return SolarbankOptionsFlow(entry)


class SolarbankOptionsFlow(OptionsFlow):
    """Tune the control parameters without re-doing the wizard."""

    def __init__(self, entry: ConfigEntry) -> None:
        self._entry = entry

    async def async_step_init(self, user_input: dict | None = None) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)
        defaults = {**self._entry.data, **self._entry.options}
        return self.async_show_form(
            step_id="init", data_schema=_options_schema(defaults)
        )

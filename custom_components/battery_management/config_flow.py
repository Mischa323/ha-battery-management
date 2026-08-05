"""Config flow for Battery Management."""
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
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import selector

from .const import (
    CONF_BIAS,
    CONF_CHARGE_LIMIT,
    CONF_DEADBAND,
    CONF_DEVICE,
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
from .discovery import match_unit_entities
from .validate import validate_unit

_SENSOR = selector.EntitySelector(selector.EntitySelectorConfig(domain="sensor"))
_SELECT = selector.EntitySelector(selector.EntitySelectorConfig(domain="select"))
_NUMBER = selector.EntitySelector(selector.EntitySelectorConfig(domain="number"))


def _unit_schema() -> vol.Schema:
    """The per-unit entity picker, shared by setup and reconfiguration."""
    return vol.Schema(
        {
            vol.Required(CONF_UNIT_NAME): str,
            vol.Required(CONF_MODE_SELECT): _SELECT,
            vol.Required(CONF_FLOW_SELECT): _SELECT,
            vol.Required(CONF_TARGET_NUMBER): _NUMBER,
            vol.Required(CONF_SOC_SENSOR): _SENSOR,
            vol.Optional(CONF_CHARGE_LIMIT): _NUMBER,
            vol.Optional(CONF_DISCHARGE_LIMIT): _NUMBER,
        }
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


class BatteryManagementConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle the setup wizard."""

    VERSION = 1

    def __init__(self) -> None:
        self._data: dict[str, Any] = {}
        self._units: list[dict] = []
        self._unit_total = 2
        self._unit_index = 0
        self._suggestion: dict[str, Any] = {}

    async def async_step_user(self, user_input: dict | None = None) -> ConfigFlowResult:
        if user_input is not None:
            self._unit_total = user_input.pop(CONF_UNIT_COUNT)
            self._data.update(user_input)
            return await self.async_step_unit_device()

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

    def _resolve_device(self, device_id: str) -> dict[str, Any]:
        """Pre-fill this unit's pickers from one Anker device's entities."""
        entity_registry = er.async_get(self.hass)
        entities = er.async_entries_for_device(
            entity_registry, device_id, include_disabled_entities=False
        )
        suggestion: dict[str, Any] = match_unit_entities(
            [entity.entity_id for entity in entities]
        )

        device = dr.async_get(self.hass).async_get(device_id)
        if device and (device.name_by_user or device.name):
            suggestion[CONF_UNIT_NAME] = device.name_by_user or device.name
        return suggestion

    async def async_step_unit_device(
        self, user_input: dict | None = None
    ) -> ConfigFlowResult:
        """Offer to resolve the six entities from a device, before the pickers."""
        if user_input is not None:
            device_id = user_input.get(CONF_DEVICE)
            self._suggestion = self._resolve_device(device_id) if device_id else {}
            return await self.async_step_unit()

        return self.async_show_form(
            step_id="unit_device",
            data_schema=vol.Schema(
                {vol.Optional(CONF_DEVICE): selector.DeviceSelector()}
            ),
            description_placeholders={
                "index": str(self._unit_index + 1),
                "total": str(self._unit_total),
            },
        )

    async def async_step_unit(self, user_input: dict | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            errors = validate_unit(
                user_input,
                [u[CONF_UNIT_NAME] for u in self._units],
                self.hass.states.get,
            )
            if not errors:
                self._units.append(user_input)
                self._unit_index += 1
                if self._unit_index >= self._unit_total:
                    self._data[CONF_UNITS] = self._units
                    return self.async_create_entry(
                        title="Battery Management", data=self._data
                    )
                self._suggestion = {}
                return await self.async_step_unit_device()

        suggested = user_input or {
            CONF_UNIT_NAME: f"Batterij {self._unit_index + 1}",
            **self._suggestion,
        }
        return self.async_show_form(
            step_id="unit",
            data_schema=self.add_suggested_values_to_schema(_unit_schema(), suggested),
            errors=errors,
            description_placeholders={
                "index": str(self._unit_index + 1),
                "total": str(self._unit_total),
            },
        )

    @staticmethod
    @callback
    def async_get_options_flow(entry: ConfigEntry) -> OptionsFlow:
        return BatteryManagementOptionsFlow(entry)


class BatteryManagementOptionsFlow(OptionsFlow):
    """Tune the control parameters, or correct a unit's entities."""

    def __init__(self, entry: ConfigEntry) -> None:
        self._entry = entry
        self._units: list[dict] = [dict(u) for u in entry.data.get(CONF_UNITS, [])]
        self._unit_index = 0

    async def async_step_init(self, user_input: dict | None = None) -> ConfigFlowResult:
        return self.async_show_menu(step_id="init", menu_options=["tuning", "units"])

    async def async_step_tuning(self, user_input: dict | None = None) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)
        defaults = {**self._entry.data, **self._entry.options}
        return self.async_show_form(
            step_id="tuning", data_schema=_options_schema(defaults)
        )

    async def async_step_units(self, user_input: dict | None = None) -> ConfigFlowResult:
        """Walk the units one by one, prefilled with what is configured now."""
        errors: dict[str, str] = {}

        if user_input is not None:
            others = [
                u[CONF_UNIT_NAME]
                for i, u in enumerate(self._units)
                if i != self._unit_index
            ]
            errors = validate_unit(user_input, others, self.hass.states.get)
            if not errors:
                self._units[self._unit_index] = user_input
                self._unit_index += 1
                if self._unit_index >= len(self._units):
                    # the entities live in entry.data, not in the options
                    self.hass.config_entries.async_update_entry(
                        self._entry,
                        data={**self._entry.data, CONF_UNITS: self._units},
                    )
                    return self.async_create_entry(
                        title="", data=dict(self._entry.options)
                    )
                return await self.async_step_units()

        current = self._units[self._unit_index]
        return self.async_show_form(
            step_id="units",
            data_schema=self.add_suggested_values_to_schema(
                _unit_schema(), user_input or current
            ),
            errors=errors,
            description_placeholders={
                "index": str(self._unit_index + 1),
                "total": str(len(self._units)),
                "name": current.get(CONF_UNIT_NAME, ""),
            },
        )

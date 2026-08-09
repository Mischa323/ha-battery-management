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
    CONF_FAST_CHARGE_HOLD,
    CONF_BATTERY_POWER_SENSOR,
    CONF_CHARGE_BELOW_SOC,
    CONF_CHEAP_HOURS,
    CONF_EXTERNAL_TIMEOUT,
    CONF_FULL_CHARGE_MINUTES,
    CONF_PRICE_SENSOR,
    CONF_SOLAR_FORECAST_MAX,
    CONF_SHADOW_SIMULATE,
    CONF_DISCHARGE_ANYWAY_SOC,
    CONF_EXPENSIVE_HOURS,
    CONF_SOLAR_FORECAST_SENSORS,
    CONF_SOLAR_PRODUCED_SENSOR,
    CONF_DISCHARGE_LIMIT,
    CONF_FLOW_SELECT,
    CONF_GRID_POWER,
    CONF_INTERVAL,
    CONF_KP,
    CONF_MIN_OUTPUT,
    CONF_MODE_CONTROL,
    CONF_MODE_SAFE,
    CONF_MODE_SELECT,
    CONF_PHASE_DETECT,
    CONF_PHASE_LIMIT_AMPS,
    CONF_PHASE_MARGIN,
    CONF_PHASE_PROBE_SECONDS,
    CONF_PHASE_REDETECT,
    CONF_PHASE_SENSORS,
    CONF_PHASE_VOLTAGE,
    CONF_SOC_SENSOR,
    CONF_TARGET_NUMBER,
    CONF_UNIT_COUNT,
    CONF_UNIT_MAX,
    CONF_UNIT_NAME,
    CONF_UNIT_PHASE,
    CONF_UNITS,
    DEFAULT_BIAS,
    DEFAULT_DEADBAND,
    DEFAULT_FAST_CHARGE_HOLD,
    DEFAULT_CHARGE_BELOW_SOC,
    DEFAULT_CHEAP_HOURS,
    DEFAULT_EXTERNAL_TIMEOUT,
    DEFAULT_FULL_CHARGE_MINUTES,
    DEFAULT_SHADOW_SIMULATE,
    DEFAULT_DISCHARGE_ANYWAY_SOC,
    DEFAULT_EXPENSIVE_HOURS,
    DEFAULT_SOLAR_FORECAST_MAX,
    DEFAULT_INTERVAL,
    DEFAULT_KP,
    DEFAULT_MIN_OUTPUT,
    DEFAULT_PHASE_DETECT,
    DEFAULT_PHASE_LIMIT_AMPS,
    DEFAULT_PHASE_MARGIN,
    DEFAULT_PHASE_PROBE_SECONDS,
    DEFAULT_PHASE_REDETECT,
    DEFAULT_PHASE_VOLTAGE,
    DEFAULT_UNIT_MAX,
    DEVICE_MODE_SELF,
    DEVICE_MODE_THIRD_PARTY,
    DOMAIN,
)
from .discovery import match_unit_entities
from .validate import validate_shadow, validate_unit

_SENSOR = selector.EntitySelector(selector.EntitySelectorConfig(domain="sensor"))
_SELECT = selector.EntitySelector(selector.EntitySelectorConfig(domain="select"))
_NUMBER = selector.EntitySelector(selector.EntitySelectorConfig(domain="number"))


def _amount(minimum, maximum, unit: str, step: float = 1):
    """A plain box with a unit on it.

    `vol.Range` on a bare int makes Home Assistant infer a slider with an
    enable-checkbox in front of it, which reads as an optional feature rather
    than a number you type. Say what is wanted instead of letting it guess.
    """
    return selector.NumberSelector(
        selector.NumberSelectorConfig(
            min=minimum,
            max=maximum,
            step=step,
            unit_of_measurement=unit,
            mode=selector.NumberSelectorMode.BOX,
        )
    )


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
            # 0 = work it out by probing. Anyone who has read the meter cupboard
            # can just say so, and what they say wins over any measurement.
            vol.Optional(CONF_UNIT_PHASE, default=0): vol.All(
                int, vol.Range(min=0, max=3)
            ),
        }
    )


def _mode_options(hass, entity_id: str) -> list[str]:
    """This select's own options, or a sensible pair if it is not loaded."""
    state = hass.states.get(entity_id)
    options = state.attributes.get("options") if state else None
    if isinstance(options, (list, tuple)) and options:
        return [str(o) for o in options]
    return [DEVICE_MODE_SELF, DEVICE_MODE_THIRD_PARTY]


def _mode_schema(options: list[str], defaults: dict) -> vol.Schema:
    """Which of this unit's own options means what.

    Populated from the entity, never from a fixed list: the two units at the
    primary site do not offer the same modes - the one without its own P1 meter
    has no self-consumption to return to at all.
    """
    picker = selector.SelectSelector(
        selector.SelectSelectorConfig(
            options=options, mode=selector.SelectSelectorMode.DROPDOWN
        )
    )
    schema: dict = {
        vol.Required(
            CONF_MODE_CONTROL,
            default=defaults.get(
                CONF_MODE_CONTROL,
                DEVICE_MODE_THIRD_PARTY
                if DEVICE_MODE_THIRD_PARTY in options
                else options[0],
            ),
        ): picker
    }
    # optional on purpose: leaving it empty means "never change the mode, just
    # command 0", which is the only safe hand-back a meterless unit has
    safe_default = defaults.get(CONF_MODE_SAFE)
    if safe_default is None and DEVICE_MODE_SELF in options:
        safe_default = DEVICE_MODE_SELF
    schema[
        vol.Optional(CONF_MODE_SAFE, description={"suggested_value": safe_default})
    ] = picker
    return vol.Schema(schema)


def _options_schema(defaults: dict) -> vol.Schema:
    return vol.Schema(
        {
            vol.Optional(CONF_BIAS, default=defaults.get(CONF_BIAS, DEFAULT_BIAS)): int,
            vol.Optional(CONF_DEADBAND, default=defaults.get(CONF_DEADBAND, DEFAULT_DEADBAND)): int,
            vol.Optional(CONF_KP, default=defaults.get(CONF_KP, DEFAULT_KP)): vol.Coerce(float),
            vol.Optional(CONF_INTERVAL, default=defaults.get(CONF_INTERVAL, DEFAULT_INTERVAL)): int,
            vol.Optional(CONF_MIN_OUTPUT, default=defaults.get(CONF_MIN_OUTPUT, DEFAULT_MIN_OUTPUT)): int,
            vol.Optional(CONF_UNIT_MAX, default=defaults.get(CONF_UNIT_MAX, DEFAULT_UNIT_MAX)): int,
            vol.Optional(
                CONF_FAST_CHARGE_HOLD,
                default=defaults.get(CONF_FAST_CHARGE_HOLD, DEFAULT_FAST_CHARGE_HOLD),
            ): bool,
            vol.Optional(
                CONF_FULL_CHARGE_MINUTES,
                default=defaults.get(
                    CONF_FULL_CHARGE_MINUTES, DEFAULT_FULL_CHARGE_MINUTES
                ),
            ): int,
            vol.Optional(
                CONF_EXTERNAL_TIMEOUT,
                default=defaults.get(CONF_EXTERNAL_TIMEOUT, DEFAULT_EXTERNAL_TIMEOUT),
            ): int,
        }
    )


def _dynamic_schema(defaults: dict) -> vol.Schema:
    """Everything the Dynamic mode needs. All optional - without a price sensor
    the mode is simply not offered, and the rest of the integration is
    unaffected."""
    return vol.Schema(
        {
            vol.Optional(
                CONF_PRICE_SENSOR,
                description={"suggested_value": defaults.get(CONF_PRICE_SENSOR)},
            ): _SENSOR,
            vol.Optional(
                CONF_CHEAP_HOURS,
                default=defaults.get(CONF_CHEAP_HOURS, DEFAULT_CHEAP_HOURS),
            ): vol.Coerce(float),
            vol.Optional(
                CONF_CHARGE_BELOW_SOC,
                default=defaults.get(CONF_CHARGE_BELOW_SOC, DEFAULT_CHARGE_BELOW_SOC),
            ): int,
            vol.Optional(
                CONF_EXPENSIVE_HOURS,
                default=defaults.get(CONF_EXPENSIVE_HOURS, DEFAULT_EXPENSIVE_HOURS),
            ): vol.Coerce(float),
            vol.Optional(
                CONF_DISCHARGE_ANYWAY_SOC,
                default=defaults.get(
                    CONF_DISCHARGE_ANYWAY_SOC, DEFAULT_DISCHARGE_ANYWAY_SOC
                ),
            ): int,
            # several: Forecast.Solar publishes one sensor per roof plane
            vol.Optional(
                CONF_SOLAR_FORECAST_SENSORS,
                description={
                    "suggested_value": defaults.get(CONF_SOLAR_FORECAST_SENSORS)
                },
            ): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="sensor", multiple=True)
            ),
            vol.Optional(
                CONF_SOLAR_PRODUCED_SENSOR,
                description={
                    "suggested_value": defaults.get(CONF_SOLAR_PRODUCED_SENSOR)
                },
            ): _SENSOR,
            vol.Optional(
                CONF_SOLAR_FORECAST_MAX,
                default=defaults.get(
                    CONF_SOLAR_FORECAST_MAX, DEFAULT_SOLAR_FORECAST_MAX
                ),
            ): vol.Coerce(float),
        }
    )


def _phases_schema(defaults: dict) -> vol.Schema:
    """Fuse protection. Empty sensor list = the whole feature is off."""
    return vol.Schema(
        {
            vol.Optional(
                CONF_PHASE_SENSORS,
                description={"suggested_value": defaults.get(CONF_PHASE_SENSORS)},
            ): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="sensor", multiple=True)
            ),
            vol.Optional(
                CONF_PHASE_LIMIT_AMPS,
                default=defaults.get(
                    CONF_PHASE_LIMIT_AMPS, DEFAULT_PHASE_LIMIT_AMPS
                ),
            ): _amount(6, 100, "A"),
            vol.Optional(
                CONF_PHASE_VOLTAGE,
                default=defaults.get(CONF_PHASE_VOLTAGE, DEFAULT_PHASE_VOLTAGE),
            ): _amount(100, 300, "V"),
            vol.Optional(
                CONF_PHASE_MARGIN,
                default=defaults.get(CONF_PHASE_MARGIN, DEFAULT_PHASE_MARGIN),
            ): _amount(0, 50, "%"),
            vol.Optional(
                CONF_PHASE_DETECT,
                default=defaults.get(CONF_PHASE_DETECT, DEFAULT_PHASE_DETECT),
            ): bool,
            vol.Optional(
                CONF_PHASE_REDETECT,
                default=defaults.get(CONF_PHASE_REDETECT, DEFAULT_PHASE_REDETECT),
            ): bool,
            vol.Optional(
                CONF_PHASE_PROBE_SECONDS,
                default=defaults.get(
                    CONF_PHASE_PROBE_SECONDS, DEFAULT_PHASE_PROBE_SECONDS
                ),
            ): _amount(10, 120, "s", step=5),
        }
    )


def _shadow_schema(defaults: dict) -> vol.Schema:
    """Settings for running alongside another controller without touching it."""
    return vol.Schema(
        {
            vol.Optional(
                CONF_SHADOW_SIMULATE,
                default=defaults.get(CONF_SHADOW_SIMULATE, DEFAULT_SHADOW_SIMULATE),
            ): bool,
            vol.Optional(
                CONF_BATTERY_POWER_SENSOR,
                description={
                    "suggested_value": defaults.get(CONF_BATTERY_POWER_SENSOR)
                },
            ): _SENSOR,
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
        self._pending_unit: dict[str, Any] = {}

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
                self._pending_unit = user_input
                return await self.async_step_unit_modes()

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

    async def async_step_unit_modes(
        self, user_input: dict | None = None
    ) -> ConfigFlowResult:
        """Map this unit's own mode options onto what we need them to mean."""
        unit = self._pending_unit
        options = _mode_options(self.hass, unit[CONF_MODE_SELECT])

        if user_input is not None:
            unit = {**unit, **user_input}
            self._units.append(unit)
            self._pending_unit = {}
            self._unit_index += 1
            if self._unit_index >= self._unit_total:
                self._data[CONF_UNITS] = self._units
                return self.async_create_entry(
                    title="Battery Management", data=self._data
                )
            self._suggestion = {}
            return await self.async_step_unit_device()

        return self.async_show_form(
            step_id="unit_modes",
            data_schema=_mode_schema(options, unit),
            description_placeholders={
                "name": unit.get(CONF_UNIT_NAME, ""),
                "options": ", ".join(options),
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
        self._pending_unit: dict = {}

    async def async_step_init(self, user_input: dict | None = None) -> ConfigFlowResult:
        return self.async_show_menu(
            step_id="init",
            menu_options=["tuning", "units", "dynamic", "phases", "shadow"],
        )

    async def async_step_tuning(self, user_input: dict | None = None) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)
        defaults = {**self._entry.data, **self._entry.options}
        return self.async_show_form(
            step_id="tuning", data_schema=_options_schema(defaults)
        )

    async def async_step_dynamic(
        self, user_input: dict | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            # an emptied picker must actually clear, not fall back to the old one
            merged = {**self._entry.options}
            for key in (
                CONF_PRICE_SENSOR,
                CONF_SOLAR_FORECAST_SENSORS,
                CONF_SOLAR_PRODUCED_SENSOR,
            ):
                merged.pop(key, None)
            merged.update(user_input)
            return self.async_create_entry(title="", data=merged)

        defaults = {**self._entry.data, **self._entry.options}
        return self.async_show_form(
            step_id="dynamic", data_schema=_dynamic_schema(defaults)
        )

    async def async_step_unit_modes(
        self, user_input: dict | None = None
    ) -> ConfigFlowResult:
        current = self._units[self._unit_index]
        unit = {**current, **self._pending_unit}
        options = _mode_options(self.hass, unit[CONF_MODE_SELECT])

        if user_input is not None:
            merged = {**unit}
            merged.pop(CONF_MODE_SAFE, None)  # an emptied picker must clear
            merged.update(user_input)
            self._units[self._unit_index] = merged
            self._pending_unit = {}
            self._unit_index += 1
            if self._unit_index >= len(self._units):
                # the entities live in entry.data, not in the options
                self.hass.config_entries.async_update_entry(
                    self._entry, data={**self._entry.data, CONF_UNITS: self._units}
                )
                return self.async_create_entry(title="", data=dict(self._entry.options))
            return await self.async_step_units()

        return self.async_show_form(
            step_id="unit_modes",
            data_schema=_mode_schema(options, unit),
            description_placeholders={
                "name": unit.get(CONF_UNIT_NAME, ""),
                "options": ", ".join(options),
            },
        )

    async def async_step_phases(
        self, user_input: dict | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            # emptying the sensor list must switch the protection off, not
            # silently keep guarding with yesterday's entities
            merged = {**self._entry.options}
            merged.pop(CONF_PHASE_SENSORS, None)
            merged.update(user_input)
            return self.async_create_entry(title="", data=merged)

        defaults = {**self._entry.data, **self._entry.options}
        return self.async_show_form(
            step_id="phases", data_schema=_phases_schema(defaults)
        )

    async def async_step_shadow(
        self, user_input: dict | None = None
    ) -> ConfigFlowResult:
        defaults = {**self._entry.data, **self._entry.options}
        errors: dict[str, str] = {}

        if user_input is not None:
            errors = validate_shadow(user_input, defaults.get(CONF_GRID_POWER))
            if not errors:
                merged = {**self._entry.options}
                merged.pop(CONF_BATTERY_POWER_SENSOR, None)
                merged.update(user_input)
                return self.async_create_entry(title="", data=merged)
            defaults = {**defaults, **user_input}

        return self.async_show_form(
            step_id="shadow", data_schema=_shadow_schema(defaults), errors=errors
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
                # carry the mode mapping through to its own step, exactly as the
                # wizard does - it is the setting that decides what a pack does
                # when the coordinator lets go, so it must stay correctable
                self._pending_unit = user_input
                return await self.async_step_unit_modes()

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

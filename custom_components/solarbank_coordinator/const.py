"""Constants for the Solarbank Coordinator integration."""
from __future__ import annotations

DOMAIN = "solarbank_coordinator"

# --- Config entry keys -------------------------------------------------------
CONF_GRID_POWER = "grid_power_sensor"      # sensor: + = import, - = export (W)
CONF_UNIT_COUNT = "unit_count"
CONF_UNITS = "units"

# Per-unit entity keys (collected once per battery in the config flow)
CONF_UNIT_NAME = "name"
CONF_MODE_SELECT = "operating_mode_select"     # select.* (self_consumption / third_party_control)
CONF_FLOW_SELECT = "grid_flow_select"          # select.* (charge / discharge)
CONF_TARGET_NUMBER = "target_power_number"     # number.* (0..max W)
CONF_SOC_SENSOR = "soc_sensor"                 # sensor.* (%)
CONF_CHARGE_LIMIT = "charge_limit_number"      # number.* (%) - optional
CONF_DISCHARGE_LIMIT = "discharge_limit_number"  # number.* (%) - optional

# --- Options (tunable, sane defaults) ---------------------------------------
CONF_BIAS = "bias"                 # W, aim for a small import so we never export
CONF_DEADBAND = "deadband"         # W, ignore errors smaller than this
CONF_KP = "kp"                     # proportional gain for the integrator
CONF_INTERVAL = "interval"         # seconds between control ticks
CONF_MIN_OUTPUT = "min_output"     # W, below this a unit is idled (avoid micro-cycling)
CONF_UNIT_MAX = "unit_max"         # W, hard ceiling per unit (Max AC ~3500)

DEFAULT_BIAS = 30
DEFAULT_DEADBAND = 100
DEFAULT_KP = 0.25
DEFAULT_INTERVAL = 15
DEFAULT_MIN_OUTPUT = 150
DEFAULT_UNIT_MAX = 3500

# --- Operating-mode option strings (as exposed by the Anker SOLIX Official
#     Modbus integration). Adjust here if a firmware/integration renames them. -
MODE_SELF = "self_consumption"
MODE_THIRD_PARTY = "third_party_control"
FLOW_CHARGE = "charge"
FLOW_DISCHARGE = "discharge"

# Fallback SoC limits when the limit entities are not provided
FALLBACK_CHARGE_LIMIT = 100.0
FALLBACK_DISCHARGE_LIMIT = 5.0

# Grace period (s) after enabling before the coordinator writes commands
STARTUP_GRACE = 5

UNAVAILABLE_STATES = ("unavailable", "unknown", "none", None, "")

"""Constants for the Battery Management integration."""
from __future__ import annotations

DOMAIN = "battery_management"

# --- Config entry keys -------------------------------------------------------
CONF_GRID_POWER = "grid_power_sensor"      # sensor: + = import, - = export (W)
CONF_UNIT_COUNT = "unit_count"
CONF_UNITS = "units"

# Per-unit entity keys (collected once per battery in the config flow)
CONF_DEVICE = "device_id"                      # only used to pre-fill the pickers
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
CONF_FAST_CHARGE_HOLD = "fast_charge_hold"   # keep the packs full once charged

# Fast charge exists to prepare for something - a storm, an outage. Switching
# itself off at full and letting the packs discharge again defeats that, so the
# default is to hold. Set to False for the old "top up, then carry on" behaviour.
DEFAULT_FAST_CHARGE_HOLD = True

# --- SoC reserve -------------------------------------------------------------
# A floor the user keeps in reserve, e.g. for the evening peak. Applies in every
# mode, and is expressed as a raise of each unit's own discharge limit rather
# than as a separate clamp - so the SoC weighting tapers off towards it instead
# of falling off a cliff. 0 = off, which is the default: nothing is mandatory.
DEFAULT_SOC_RESERVE = 0

# --- Active policy: what decided this tick -----------------------------------
# Answers "why is the battery doing this?" without digging through logs, which
# matters most at the sites the owner does not live at.
POLICY_DISABLED = "disabled"            # kill-switch off
POLICY_NO_GRID_DATA = "no_grid_data"    # grid sensor unreadable
POLICY_FAST_CHARGE = "fast_charge"      # emergency override running
POLICY_FAST_CHARGE_HOLD = "fast_charge_hold"  # charged, now kept full on purpose
POLICY_SOC_RESERVE = "soc_reserve"      # would discharge, but the reserve says no
POLICY_PACKS_EMPTY = "packs_empty"      # would discharge, but nothing left
POLICY_PACKS_FULL = "packs_full"        # would charge, but nowhere to put it
POLICY_DEADBAND = "deadband"            # error too small to act on
POLICY_GRID_ZERO = "grid_zero"          # regulating normally, nothing limiting

POLICIES = [
    POLICY_GRID_ZERO,
    POLICY_DEADBAND,
    POLICY_SOC_RESERVE,
    POLICY_PACKS_EMPTY,
    POLICY_PACKS_FULL,
    POLICY_FAST_CHARGE,
    POLICY_FAST_CHARGE_HOLD,
    POLICY_NO_GRID_DATA,
    POLICY_DISABLED,
]

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

# --- Persisted runtime state -------------------------------------------------
STORAGE_VERSION = 1
# How long a stored setpoint stays usable. Beyond this the house situation has
# moved on, so the integrator restarts from 0 rather than re-applying a stale
# command. The on/off state itself is restored regardless of age.
MAX_SETPOINT_AGE = 300  # seconds
# Debounce for writing runtime state to disk; the setpoint changes every tick.
SAVE_DELAY = 30  # seconds

UNAVAILABLE_STATES = ("unavailable", "unknown", "none", None, "")

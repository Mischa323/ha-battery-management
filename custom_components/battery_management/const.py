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

# Which option on this unit's mode select means what. Not hard-coded, because
# the two units at the primary site do not even offer the same list: the one
# without its own P1 meter cannot do self-consumption at all, so the firmware
# hides that option. Different firmware or a different language would break a
# fixed string in the same way, and at a site nobody can reach.
CONF_MODE_CONTROL = "mode_control"   # the option that hands us the wheel
CONF_MODE_SAFE = "mode_safe"         # the option that gives it back; may be empty

# --- Options (tunable, sane defaults) ---------------------------------------
CONF_BIAS = "bias"                 # W, aim for a small import so we never export
CONF_DEADBAND = "deadband"         # W, ignore errors smaller than this
CONF_KP = "kp"                     # proportional gain for the integrator
CONF_INTERVAL = "interval"         # seconds between control ticks
CONF_MIN_OUTPUT = "min_output"     # W, below this a unit is idled (avoid micro-cycling)
CONF_UNIT_MAX = "unit_max"         # W, hard ceiling per unit (Max AC ~3500)
CONF_FAST_CHARGE_HOLD = "fast_charge_hold"   # keep the packs full once charged
CONF_FULL_CHARGE_MINUTES = "full_charge_minutes"  # empty -> full at max power

# A hardware property, not a preference: how long one pack takes from empty to
# its charge limit at `unit_max`. There is no way to derive it - state of charge
# is a percentage and the packs never report their capacity - so it is measured
# once and typed in. 0 means unknown, which simply hides the estimate rather
# than guessing: a wrong "be full by" time is worse than no time at all.
DEFAULT_FULL_CHARGE_MINUTES = 0

# --- Dynamic tariff ----------------------------------------------------------
# One entity picker, not per-supplier support: any sensor publishing upcoming
# prices works, and a site can change supplier by pointing somewhere else.
# Without a price sensor the Dynamic mode is not even offered.
CONF_PRICE_SENSOR = "price_sensor"
CONF_CHEAP_HOURS = "cheap_hours"                # hours per day to grid-charge on
CONF_CHARGE_BELOW_SOC = "charge_below_soc"      # only top up when emptier than this
CONF_SOLAR_FORECAST_SENSOR = "solar_forecast_sensor"   # kWh expected today
CONF_SOLAR_FORECAST_MAX = "solar_forecast_max"  # skip if more sun than this

DEFAULT_CHEAP_HOURS = 3
DEFAULT_CHARGE_BELOW_SOC = 40
DEFAULT_SOLAR_FORECAST_MAX = 0        # 0 = ignore the forecast entirely
# Rank the cheap hours over a rolling window rather than everything published:
# with tomorrow already known, a 48 h ranking can decide nothing today is worth
# charging on and leave the packs flat through this evening's peak.
PRICE_WINDOW_HOURS = 24

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
POLICY_MODE_CHARGE_ONLY = "mode_charge_only"        # mode forbids discharging
POLICY_MODE_DISCHARGE_ONLY = "mode_discharge_only"  # mode forbids charging
POLICY_MODE_PAUSE = "mode_pause"                    # mode holds everything at 0
POLICY_DYNAMIC_CHARGE = "dynamic_charge"      # buying now because it is cheap
POLICY_EXTERNAL = "external_plan"             # following someone else's plan
POLICY_EXTERNAL_STALE = "external_stale"      # plan went quiet, regulating ourselves
POLICY_DYNAMIC_NO_PRICES = "dynamic_no_prices"  # dynamic, but the sensor is mute
POLICY_DEADBAND = "deadband"            # error too small to act on
POLICY_GRID_ZERO = "grid_zero"          # regulating normally, nothing limiting

POLICIES = [
    POLICY_GRID_ZERO,
    POLICY_DEADBAND,
    POLICY_MODE_CHARGE_ONLY,
    POLICY_MODE_DISCHARGE_ONLY,
    POLICY_MODE_PAUSE,
    POLICY_DYNAMIC_CHARGE,
    POLICY_EXTERNAL,
    POLICY_EXTERNAL_STALE,
    POLICY_DYNAMIC_NO_PRICES,
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
# --- Coordinator modes: one choice at a time ---------------------------------
# Grid-zero is not one mode among others, it is the floor every mode falls back
# on: charge on surplus, discharge on deficit. The other modes are that same
# regulation with a bound on the setpoint, so a pack never sits idle merely
# because it is outside a window. Fast charge is an override on top, not a mode,
# so preparing for a storm does not lose your strategy.
MODE_GRID_ZERO = "grid_zero"            # regulate the meter to ~0 (the default)
MODE_CHARGE_ONLY = "charge_only"        # never discharge; fill on surplus only
MODE_DISCHARGE_ONLY = "discharge_only"  # never charge; spend what is stored
MODE_PAUSE = "pause"                    # hold at 0, but stay in control
MODE_DYNAMIC = "dynamic"                # grid-zero, plus grid-charging when cheap
# Somebody else plans (EMHASS does model-predictive optimisation properly), we
# execute: the split, the SoC limits, never-opposite-directions and the safe
# revert all still apply underneath. `set_setpoint` is the seam.
MODE_EXTERNAL = "external"

MODES = [
    MODE_GRID_ZERO,
    MODE_CHARGE_ONLY,
    MODE_DISCHARGE_ONLY,
    MODE_PAUSE,
    MODE_EXTERNAL,
]

# How long an external setpoint stays valid. A plan that stops arriving must
# hand control back, not freeze the packs on its last instruction - the packs
# have no watchdog of their own (gotcha 1), so this is ours.
CONF_EXTERNAL_TIMEOUT = "external_timeout"   # minutes
DEFAULT_EXTERNAL_TIMEOUT = 15
DEFAULT_MODE = MODE_GRID_ZERO

# Defaults only: the wizard offers whatever the entity actually publishes, and
# these are pre-selected when present.
DEVICE_MODE_SELF = "self_consumption"
DEVICE_MODE_THIRD_PARTY = "third_party_control"
FLOW_CHARGE = "charge"
FLOW_DISCHARGE = "discharge"

# Fallback SoC limits when the limit entities are not provided
FALLBACK_CHARGE_LIMIT = 100.0
FALLBACK_DISCHARGE_LIMIT = 5.0

# Grace period (s) after enabling before the coordinator writes commands
STARTUP_GRACE = 5

# --- Dry run -----------------------------------------------------------------
# Compute everything, command nothing. Blocks every write to the packs: targets,
# grid flow, the operating-mode select AND the safe revert on unload. A half dry
# run that still set third_party_control would fight the site's existing
# automations, which is worse than not testing at all.
#
# Defaults to ON: this has never been field-tested, so a fresh install watches
# before it acts. Turning it off is a deliberate act.
DEFAULT_DRY_RUN = True

# How many control ticks to keep in memory for the diagnostics download. At the
# default 15 s that is a little over four hours - enough to answer "what was it
# thinking at 14:32" without holding a database in RAM. The month-long trend
# lives in Home Assistant's long-term statistics instead.
TICK_LOG_SIZE = 1000

# --- Shadow simulation -------------------------------------------------------
# In dry run something else is regulating the meter, so our integrator sees a
# near-zero error and parks at zero - it would look calm because someone else
# did the work. This closes the loop on reconstructed data instead:
#
#   net demand = grid + battery      (what the meter would read with no battery)
#   our meter  = net demand - our own setpoint
#
# PV cancels out of that, so no solar sensor is needed. What is needed is what
# the packs are doing *now*, and that can simply be read back from the very
# entities we would have written to - the other controller writes there.
CONF_SHADOW_SIMULATE = "shadow_simulate"
CONF_BATTERY_POWER_SENSOR = "battery_power_sensor"  # signed, + = discharging
DEFAULT_SHADOW_SIMULATE = True

# --- Persisted runtime state -------------------------------------------------
STORAGE_VERSION = 1
# How long a stored setpoint stays usable. Beyond this the house situation has
# moved on, so the integrator restarts from 0 rather than re-applying a stale
# command. The on/off state itself is restored regardless of age.
MAX_SETPOINT_AGE = 300  # seconds
# Debounce for writing runtime state to disk; the setpoint changes every tick.
SAVE_DELAY = 30  # seconds

UNAVAILABLE_STATES = ("unavailable", "unknown", "none", None, "")

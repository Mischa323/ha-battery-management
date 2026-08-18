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
# The pack's own power reading. Optional, and never used for control -
# gotcha 2 says it lags 10-30 s and arrives in bursts. It is here so the
# trace can show what we commanded next to what the pack actually did,
# which is the only way to see how far behind it runs.
CONF_UNIT_POWER_SENSOR = "power_sensor"        # sensor.* (W) - optional
# The pack's own *charging* power, which is a different question from the one
# above. `ac_output` is the better witness for "did this pack follow orders",
# but it is blind while charging, and charging is the only thing that can be
# split into sun and grid. Optional: no sensor, no energy figures, no guessing.
CONF_CHARGE_POWER_SENSOR = "charge_power_sensor"  # sensor.* (W) - optional
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
CONF_KP_RETURN = "kp_return"       # gain used when winding the command back down
CONF_INTERVAL = "interval"         # seconds between control ticks
# How old the meter reading may be before we stop regulating on it. The loop
# used to trust whatever the sensor held, however stale, and only noticed a
# meter that was outright unreadable. Those are not the same failure: an
# integration that hangs while keeping its last state looks perfectly healthy,
# and the integrator happily keeps adding kp*error to a number that stopped
# moving - a frozen +2000 W walks the setpoint to full discharge in about two
# minutes and holds it there until the packs are empty, with status "ok".
CONF_GRID_MAX_AGE = "grid_max_age"  # s, 0 = trust the reading whatever its age
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
# Where prices come from: a sensor somebody else's integration publishes, or
# straight from a supplier that makes them public. The sensor route stays the
# default and is what makes this supplier-agnostic; the direct route exists
# because "first install another custom integration" is a real obstacle at a
# site the maintainer does not live at. Either way the Dynamic mode is not
# offered until one of the two is configured.
CONF_PRICE_SOURCE = "price_source"
CONF_PRICE_SENSOR = "price_sensor"
# Prices are published once or twice a day, so this is about noticing that
# tomorrow has arrived, not about tracking anything.
# How to read the feed. The market settles in 15-minute blocks and suppliers
# are starting to publish that way; the ranking never cared, but 96 bars on a
# card is a lot of bars. Folding to hours is a readability choice that costs a
# little precision, so the default is to take the feed exactly as published.
CONF_PRICE_RESOLUTION = "price_resolution"
RESOLUTION_PUBLISHED = "published"
RESOLUTION_HOURLY = "hourly"
RESOLUTIONS = [RESOLUTION_PUBLISHED, RESOLUTION_HOURLY]
DEFAULT_PRICE_RESOLUTION = RESOLUTION_PUBLISHED

PRICE_REFRESH_MINUTES = 60
# Seconds. A supplier that is slow to answer must not hold up a control tick.
PRICE_TIMEOUT = 20
# A fetched forecast older than this is dropped rather than ranked on. In
# practice the slots expire by themselves - the ranking window starts at now -
# but a stale cache should not linger in the diagnostics looking authoritative.
MAX_PRICE_AGE = 36 * 3600
CONF_CHEAP_HOURS = "cheap_hours"                # hours per day to grid-charge on
CONF_CHARGE_BELOW_SOC = "charge_below_soc"      # only top up when emptier than this
# The reference a rank does not have. "The cheapest three hours of what is
# left" always finds three, however dear they are - at 22:00 with only today
# published it returned the most expensive hour of the day and the dashboard
# called it cheap. Buying early only pays if it beats the dear hours it saves
# for by enough to cover the round trip (~12 % on these packs) plus the wear,
# so that is the test. 0 restores the pure ranking.
CONF_PRICE_MARGIN = "price_margin"              # currency/kWh the buy must beat by
DEFAULT_PRICE_MARGIN = 0.05
# Several sensors, summed: Forecast.Solar publishes one per roof plane, and the
# primary site has three (west, south, north).
#
# Prefer the "remaining today" variants. A whole-day total is right at 02:00 and
# wrong at 17:00, when most of it has already been produced - and 17:00 is
# exactly when topping up for the evening matters.
CONF_SOLAR_FORECAST_SENSORS = "solar_forecast_sensors"
# Optional: subtract what is already in, turning a day total into a remainder.
CONF_SOLAR_PRODUCED_SENSOR = "solar_produced_sensor"
CONF_SOLAR_FORECAST_SENSOR = "solar_forecast_sensor"   # single, kept for old entries
CONF_SOLAR_FORECAST_MAX = "solar_forecast_max"  # fallback when capacity is unknown

# How many of the day's dearest hours to keep the packs' charge for. A pack
# smaller than a day's consumption is not short of cheap hours to fill on - the
# question is where the stored kWh are spent.
CONF_EXPENSIVE_HOURS = "expensive_hours"
DEFAULT_EXPENSIVE_HOURS = 4
# Above this state of charge, discharge anyway: refusing would leave no room for
# the sun that is still coming, and spilling free energy to save a few cents is
# a bad trade.
CONF_DISCHARGE_ANYWAY_SOC = "discharge_anyway_soc"
DEFAULT_DISCHARGE_ANYWAY_SOC = 90

# Bounds the user puts around the computed buy-up-to ceiling. The calculation is
# only as good as the solar forecast behind it - the primary site's under-reads
# by half - so both ends must be reachable without waiting for a code change.
# The defaults are wide open, so the computed value passes through untouched.
DEFAULT_BUY_CEILING_MIN = 0
DEFAULT_BUY_CEILING_MAX = 100

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

# --- Recovering after being emptied ------------------------------------------
# The floor is one threshold, so it is both where discharging stops and where it
# may start again. A pack sitting on 5 % that the sun lifts to 6 % is therefore
# discharged straight back to 5 %, and again, and again - micro-cycling at the
# deepest point of the pack, which is where cycling costs the most. Observed at
# the primary site.
#
# So once a pack has been emptied it has to climb this far above its floor
# before it may discharge again. A delta rather than an absolute percentage, so
# it composes with the SoC reserve and with each pack's own discharge limit. It
# never blocks charging - only the way back out.
#
# A latch, not a raised floor: a pack coming down from full still discharges all
# the way to its limit. Raising the floor to 10 % would simply cost you those
# points. 0 disables it.
CONF_DISCHARGE_RECOVERY = "discharge_recovery"
DEFAULT_DISCHARGE_RECOVERY = 5

# --- Per-phase fuse protection -----------------------------------------------
# The packs are single-phase and each sits on one leg of a three-phase supply.
# Nothing in the grid-zero loop knows that: it regulates the *total*, so a pack
# charging at 3500 W on a leg that is already pulling 20 A takes that leg to
# 35 A and drops the main fuse - while the other two legs sit half idle and the
# total looks perfectly reasonable.
#
# So the fuse is a bound per leg, applied per unit, and it needs to know which
# unit is on which leg. Entirely opt-in: without per-phase sensors none of this
# exists and the loop behaves exactly as before.
CONF_PHASE_SENSORS = "phase_power_sensors"   # per-phase power, in L1..Ln order
CONF_PHASE_LIMIT_AMPS = "phase_limit_amps"   # the main fuse per leg
CONF_PHASE_VOLTAGE = "phase_voltage"         # to turn amps into watts
CONF_PHASE_MARGIN = "phase_margin"           # % of the fuse left unused
CONF_PHASE_DETECT = "phase_detect"           # may we probe to find the wiring?
CONF_PHASE_REDETECT = "phase_redetect"       # probe again after a restart
CONF_PHASE_PROBE_SECONDS = "phase_probe_seconds"
CONF_UNIT_PHASE = "unit_phase"               # per unit: 0 = work it out yourself

DEFAULT_PHASE_LIMIT_AMPS = 25     # the common Dutch 3x25 A connection
DEFAULT_PHASE_VOLTAGE = 230
# A fuse is not a cliff - a B-curve holds ~1.13x for a long time - but running
# it to the last ampere leaves nothing for the kettle somebody just switched on
# while we were deciding. Ten per cent of 25 A is 2.5 A of room.
DEFAULT_PHASE_MARGIN = 10
DEFAULT_PHASE_DETECT = True
DEFAULT_PHASE_REDETECT = True
DEFAULT_PHASE_PROBE_SECONDS = 20

# Detection: command one pack, watch which leg moves. Deliberately crude,
# because the alternative is asking the user to read a meter cupboard.
# Everything at 0 before the baseline is taken - but *waiting* is not the same
# as being at rest, and a fixed count is a guess. At the primary site the
# baseline was taken while the pack was still drawing its previous 3500 W, so
# commanding 3500 W produced a delta of 144 W, the probe failed, and it tried
# again - which is what kept the pack from ever coming down. The SoC graph
# showed it: 5 % to 48 % in an hour and a half of nothing but probing.
#
# So the legs are watched until they stop moving. The minimum still applies
# (gotcha 2: nothing is trustworthy for the first 10-30 s), and the maximum
# stops a busy household from postponing the measurement for ever.
PHASE_SETTLE_SECONDS = 30       # never take a baseline sooner than this
PHASE_SETTLE_STEP = 10          # how often to look while waiting
PHASE_SETTLE_MAX = 120          # give up waiting for quiet and read anyway
PHASE_SETTLE_TOLERANCE = 200    # W per leg; below this the house counts as still
# How often the legs are read while the probe is running. The baseline
# waits for quiet; sampling the measurement only once at the end was the
# missing half of that, and it is how a kettle gets to cast a vote.
PHASE_PROBE_SAMPLE = 5          # seconds between readings during a probe
PHASE_PROBE_MIN_WATTS = 600     # below this the signal drowns in the household
PHASE_PROBE_MIN_FRACTION = 0.5  # the winning leg must show at least this much
PHASE_PROBE_MARGIN = 2.0        # ...and this many times the runner-up

# A probe that cannot answer must not simply be retried on the next tick. That
# is what happened at the primary site: 093 reported `too_small` every time -
# the pack was not responding to the command at all - and the packs spent every
# minute of the day being measured instead of regulating.
#
# So a failed unit waits, and after a few tries it stops asking until somebody
# presses the button, restarts, or types the leg in by hand. Giving up is a
# better outcome than a coordinator that never coordinates.
PHASE_RETRY_SECONDS = 1800      # half an hour between attempts
PHASE_MAX_ATTEMPTS = 3

PHASE_DETECT_UNKNOWN = "unknown"          # never looked
PHASE_DETECT_RUNNING = "running"          # probing right now
PHASE_DETECT_DONE = "done"                # every unit placed
PHASE_DETECT_PARTIAL = "partial"          # some placed, some not
PHASE_DETECT_INCONCLUSIVE = "inconclusive"  # looked, could not tell
PHASE_DETECT_MANUAL = "manual"            # the user typed it in
PHASE_DETECT_OFF = "off"                  # probing not allowed
PHASE_DETECT_BLOCKED = "blocked"          # dry run, or no room to probe in
PHASE_DETECT_GAVE_UP = "gave_up"          # asked enough times; type it in

PHASE_DETECT_STATES = [
    PHASE_DETECT_DONE,
    PHASE_DETECT_MANUAL,
    PHASE_DETECT_PARTIAL,
    PHASE_DETECT_RUNNING,
    PHASE_DETECT_INCONCLUSIVE,
    PHASE_DETECT_UNKNOWN,
    PHASE_DETECT_BLOCKED,
    PHASE_DETECT_GAVE_UP,
    PHASE_DETECT_OFF,
]

# --- Active policy: what decided this tick -----------------------------------
# Answers "why is the battery doing this?" without digging through logs, which
# matters most at the sites the owner does not live at.
POLICY_DISABLED = "disabled"            # kill-switch off
POLICY_NO_GRID_DATA = "no_grid_data"    # grid sensor unreadable
POLICY_GRID_STALE = "grid_stale"        # grid sensor readable but no longer reporting
POLICY_NO_UNITS = "no_units"            # nothing reachable to command at all
POLICY_FAST_CHARGE = "fast_charge"      # emergency override running
POLICY_FAST_CHARGE_HOLD = "fast_charge_hold"  # charged, now kept full on purpose
POLICY_SOC_RESERVE = "soc_reserve"      # would discharge, but the reserve says no
POLICY_PACKS_EMPTY = "packs_empty"      # would discharge, but nothing left
POLICY_RECOVERING = "recovering"        # emptied, not yet charged far enough back
POLICY_PACKS_FULL = "packs_full"        # would charge, but nowhere to put it
POLICY_MODE_CHARGE_ONLY = "mode_charge_only"        # mode forbids discharging
POLICY_MODE_DISCHARGE_ONLY = "mode_discharge_only"  # mode forbids charging
POLICY_MODE_PAUSE = "mode_pause"                    # mode holds everything at 0
POLICY_DYNAMIC_CHARGE = "dynamic_charge"      # buying now because it is cheap
POLICY_DYNAMIC_HOLD = "dynamic_hold"          # holding the charge for dearer hours
POLICY_SOLAR_HEADROOM = "solar_headroom"      # not buying, the sun still fits
POLICY_EXTERNAL = "external_plan"             # following someone else's plan
POLICY_EXTERNAL_STALE = "external_stale"      # plan went quiet, regulating ourselves
POLICY_DYNAMIC_NO_PRICES = "dynamic_no_prices"  # dynamic, but the sensor is mute
POLICY_PHASE_LIMIT = "phase_limit"      # the main fuse on one leg says no
POLICY_PHASE_DETECT = "phase_detect"    # working out which pack is on which leg
POLICY_DEADBAND = "deadband"            # error too small to act on
POLICY_MIN_OUTPUT = "min_output"        # setpoint too small for a pack to hold
POLICY_GRID_ZERO = "grid_zero"          # regulating normally, nothing limiting

POLICIES = [
    POLICY_GRID_ZERO,
    POLICY_DEADBAND,
    POLICY_MIN_OUTPUT,
    POLICY_PHASE_LIMIT,
    POLICY_PHASE_DETECT,
    POLICY_MODE_CHARGE_ONLY,
    POLICY_MODE_DISCHARGE_ONLY,
    POLICY_MODE_PAUSE,
    POLICY_DYNAMIC_CHARGE,
    POLICY_DYNAMIC_HOLD,
    POLICY_SOLAR_HEADROOM,
    POLICY_EXTERNAL,
    POLICY_EXTERNAL_STALE,
    POLICY_DYNAMIC_NO_PRICES,
    POLICY_SOC_RESERVE,
    POLICY_PACKS_EMPTY,
    POLICY_RECOVERING,
    POLICY_PACKS_FULL,
    POLICY_FAST_CHARGE,
    POLICY_FAST_CHARGE_HOLD,
    POLICY_NO_GRID_DATA,
    POLICY_GRID_STALE,
    POLICY_NO_UNITS,
    POLICY_DISABLED,
]

DEFAULT_BIAS = 30
DEFAULT_DEADBAND = 100
DEFAULT_KP = 0.25
# Going out and coming back are not symmetric problems. Building the command up
# too fast is what winds up and oscillates (gotcha 3); winding it back down
# cannot, because the far end of "back" is a pack sitting at 0 W. Export is
# caused by exactly one thing - the pack still discharging into a load that has
# already gone away - so coming back quickly is aimed straight at it.
# Measured on the primary site's own hour, one pack: 296 -> 227 Wh exported, at
# a cost of 3 % on the mean deviation.
#
# Left unset it is derived from Kp rather than being a number of its own, so
# raising Kp cannot silently flatten the asymmetry back out - which a fixed
# 0.5 would have done the moment anyone tuned Kp up to 0.5. An explicit value
# always wins; setting it equal to Kp restores the old symmetric loop.
KP_RETURN_FACTOR = 2.0
DEFAULT_INTERVAL = 15
# Four ticks. The primary site's P1 publishes every 5-6 s and the worst gap
# measured across 5917 ticks was 46 s, so this does not fire on ordinary
# jitter - it fires when the meter has genuinely stopped. Measured against
# `last_reported`, which Home Assistant bumps on every write even when the
# value is unchanged, so "the house is drawing a steady 100 W" cannot be
# mistaken for "the integration died holding 100 W".
DEFAULT_GRID_MAX_AGE = 60
# How long a command may go unacknowledged before we say so. The pack answers
# in 10-30 s (gotcha 2) and the readback normally matches within one tick, so
# three ticks is well clear of the lag: past this the device is not taking
# orders, and per gotcha 1 it is still executing whatever it took last.
UNACKED_TICKS = 3

# The floor releases lower than it engages, so the packs cannot chatter across
# it. Engaging at `min_output` and releasing at the same number would let a
# setpoint hovering there switch them on and off every tick - the very
# micro-cycling the floor exists to prevent. Only ever on the way *down*, so a
# command below `min_output` is transient rather than a resting state.
MIN_OUTPUT_RELEASE = 0.75
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

# --- Trace file --------------------------------------------------------------
# The in-memory log above holds about four hours and dies with the process,
# which is useless for "what happened on Saturday". The trace is a CSV in the
# config directory instead: one row per tick, every input and every bound, so a
# question about last week can be answered by arithmetic instead of memory.
CONF_TRACE = "trace"                 # write it at all
CONF_TRACE_DAYS = "trace_days"       # how many days of files to keep
DEFAULT_TRACE = True
DEFAULT_TRACE_DAYS = 14
TRACE_DIR = "battery_management_trace"

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

# The energy counters accumulate power x elapsed time, and the elapsed time is
# measured rather than assumed - a tick can be late. But a gap is not always a
# late tick: Home Assistant may have been down for hours, and multiplying the
# power read *now* by that gap would invent kilowatt-hours out of nothing. Past
# this many intervals we admit we do not know what happened and count nothing,
# which under-reports rather than inventing. The mirror image of Home
# Assistant's own `max_sub_interval`, and needed for the opposite reason.
MAX_ENERGY_GAP_INTERVALS = 4
# Debounce for writing runtime state to disk; the setpoint changes every tick.
SAVE_DELAY = 30  # seconds

UNAVAILABLE_STATES = ("unavailable", "unknown", "none", None, "")

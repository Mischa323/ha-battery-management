# Battery Management

A Home Assistant custom integration that turns **two or more Anker SOLIX Max AC**
batteries into **one coordinated system** — without them ever charging each other.

It is **solar-agnostic**: it steers on your household **grid-power sensor** (P1
meter), so it works with any solar setup (Enphase, string inverter, none at all).

## What it does

* **One brain.** A single anti-windup control loop drives the grid meter toward
  ~0 (with a small configurable import bias so you never accidentally export).
* **No cross-charging.** Both units are always commanded the *same* direction
  (both charge, or both discharge) — so one unit can never charge the other.
* **SoC-balanced split.** The fuller unit discharges more; the emptier charges
  more, so the packs even out over time.
* **No micro-cycling.** Tiny shares are consolidated onto one unit instead of
  making the other flip between idle and active.
* **Kill-switch.** A single switch to hand control back to the batteries'
  native Self-Consumption mode (safe fallback).
* **Fast charge (emergency).** One switch to charge both packs to full as fast
  as possible (from the grid if needed), then *hold* them there until you
  release it.
* **Fails safe.** If the integration is unloaded or errors out, the units are
  reverted to Self-Consumption.

## Requirements

* The batteries must be reachable through an integration that exposes, per unit:
  an **operating-mode select** (with `self_consumption` / `third_party_control`),
  a **grid-flow select** (`charge` / `discharge`), a **target-grid-power number**,
  and a **state-of-charge sensor**. The
  [Anker SOLIX Official (Modbus TCP)](https://github.com/anker) entities provide
  exactly these.
* A **grid-power sensor** in watts where **+ = import** and **- = export**
  (a P1 meter / HomeWizard / DSMR sensor).

> The batteries are driven in **Third-Party Control** mode while the coordinator
> is enabled. Turning the kill-switch off returns them to Self-Consumption.

## Install (via HACS)

1. HACS → three-dot menu → **Custom repositories**.
2. Add `https://github.com/Mischa323/ha-battery-management` as an **Integration**.
3. Install **Battery Management**, then restart Home Assistant.
4. Settings → Devices & Services → **Add Integration** → *Battery Management*.
5. Pick your grid-power sensor, the number of units, and per unit its four (or
   six) control entities. Done.

Manual install: copy `custom_components/battery_management` into your HA
`config/custom_components/` folder and restart.

## Tunables (Configure)

| Option | Default | Meaning |
| --- | --- | --- |
| Import bias | 30 W | aim for a tiny import so you never export |
| Deadband | 100 W | ignore errors smaller than this |
| Gain (Kp) | 0.25 | lower = gentler/slower, less overshoot |
| Interval | 15 s | control tick period |
| Min output | 150 W | below this a unit is idled (avoids micro-cycling) |
| Max per unit | 3500 W | hard ceiling per unit |

## Notes / status

The control loop is a port of a field-tested YAML setup, but this integration
itself has not yet run a full season on real hardware. **It ships with dry run
switched on** so a new site watches before it acts — see the reference below.

Every setting carries its own explanation in Home Assistant, under the field.
Every entity is explained in the reference section further down.

Not affiliated with Anker.

## Development and testing

### In the devcontainer (full Home Assistant)

Open the repo in VS Code and *Reopen in Container* (Dev Containers extension).
Setup installs Home Assistant plus the test tooling and symlinks the integration
into `config/custom_components/`, so an HA restart picks up your edits.

```bash
scripts/develop     # Home Assistant on http://localhost:8123
scripts/test        # the pytest suite
```

`config/` is a throwaway instance and ships a **simulator**
(`config/packages/simulator.yaml`): a fake P1 meter and two fake Max AC units as
template `select`/`number`/`sensor` entities. You can complete the config-flow
wizard against them and watch the loop regulate — no hardware needed.

It is a closed loop: `sensor.p1_meter_power` is the house load *minus* whatever
the packs are commanded to do, so the setpoint actually settles. Drive it with
`input_number.sim_house_load` (negative = PV export) and
`input_number.sim_0X_soc`.

| Wizard field | Simulator entity |
| --- | --- |
| Grid power sensor | `sensor.p1_meter_power` |
| Operating-mode select | `select.sim_0X_operating_mode` |
| Grid-flow select | `select.sim_0X_grid_flow` |
| Target grid power | `number.sim_0X_target_grid_power` |
| State of charge | `sensor.sim_0X_soc` |
| Charge / discharge limit | leave blank (falls back to 100 % / 5 %) |

### On the host (no Home Assistant needed)

Home Assistant only installs on Linux/macOS with a matching Python, so the test
suite deliberately does not require it: [conftest.py](conftest.py) stubs the
handful of HA symbols the control loop imports when the real package is absent.

```bash
python -m pip install -r requirements-test.txt
python -m pytest
```

CI runs the suite both ways — stubbed and against a real Home Assistant — plus
hassfest and HACS validation.

## Management card

The integration **ships and auto-registers** a Lovelace card, so after install
you can add it straight away (no manual resource step needed):

```yaml
type: custom:battery-management-card
title: Battery Management
enable: switch.battery_management_coordinator_enabled
fast_charge: switch.battery_management_fast_charge
status: sensor.battery_management_status
setpoint: sensor.battery_management_setpoint
grid_power: sensor.p1_meter_power
units:
  - name: Batterij 01
    soc: sensor.anker_solix_solarbank_max_ac_093_soc
    target: sensor.battery_management_batterij_01_target
    status: binary_sensor.battery_management_batterij_01_online
  - name: Batterij 02
    soc: sensor.tuin_batterij_02_soc
    target: sensor.battery_management_batterij_02_target
    status: binary_sensor.battery_management_batterij_02_online
# optional solar-forecast line:
forecast_today: sensor.zonverwachting_vandaag
forecast_tomorrow: sensor.zonverwachting_morgen
```

`target` points at the coordinator's own per-unit sensor rather than the Anker
number, so it shows the **commanded** value with a sign (+ discharge, − charge).
Comparing that against the unit's own power sensor is how you spot a pack that
is not following orders — those Modbus sensors lag 10–30 s.

## Modes and time windows

`select.battery_management_mode` picks one strategy at a time:

| Mode | What it does |
| --- | --- |
| Follow the meter | charge on surplus, discharge on deficit (the default) |
| Charge only | still fills from surplus, never discharges |
| Discharge only | still covers a deficit, never charges |
| Pause | holds at zero, but stays in control of the units |

Every mode is that same grid-zero regulation with a bound on it, so the packs
keep responding to the house and the sun inside whatever you pick — they never
sit idle merely because they are inside a window. Pause is **not** the
kill-switch: the units stay under third-party control holding at 0, whereas the
kill-switch hands them back to self-consumption entirely.

`number.battery_management_soc_reserve` applies in every mode: charge kept back
for the evening peak, so grid-zero cannot empty the packs by late afternoon.
`sensor.battery_management_active_policy` says which rule is currently limiting
things, so "why is it doing nothing?" has an answer without reading logs.

### Time windows come from Home Assistant, not from this integration

Two automation blueprints flip the mode on a schedule. Import them from
Settings → Automations & scenes → Blueprints → Import blueprint:

- [`mode_during_schedule.yaml`](blueprints/automation/battery_management/mode_during_schedule.yaml)
  — draw blocks in a Schedule helper, pick a mode for inside and outside them.
  Re-syncs after a restart, so a window that began while HA was down still applies.
- [`mode_between_times.yaml`](blueprints/automation/battery_management/mode_between_times.yaml)
  — one start and end time with optional weekdays; no helper to create.
- [`be_full_by_time.yaml`](blueprints/automation/battery_management/be_full_by_time.yaml)
  — start a fast charge late enough to be efficient and early enough to finish.
  Needs **Minutes from empty to full** filled in under Configure → Control
  parameters; measure it once. Until you do, `sensor.…_minutes_to_full`
  (**Fast charge duration**) stays
  unavailable and the automation does nothing, on purpose — arriving late on a
  guessed duration defeats the point.

Deliberately *not* a scheduler inside the integration: less to maintain, and
anything the blueprints did not anticipate you can still build with an ordinary
automation.

## Staying inside the main fuse

The control loop regulates the household **total**. The packs are
**single-phase**, and each one sits on one leg of the supply. Nothing in the
Modbus data connects those two facts, so on its own the loop will happily tell a
pack to charge at 3500 W on a leg that is already pulling 20 A — taking that leg
to 35 A while the other two sit half idle and the total looks perfectly
reasonable. That is how a main fuse drops.

This is **off unless you configure it**. Point *Configure → Fuse protection per
phase* at your meter's per-phase power sensors, in L1, L2, L3 order, and each
leg gets its own ceiling.

### How the limit works

The ceiling is a **bound on the setpoint**, applied per unit — the same
mechanism as the SoC reserve and the modes, at the same anti-windup clamp. So
grid-zero keeps regulating inside it and the integrator can never build pressure
against a fuse it may not cross.

For each leg it works out what the household is drawing *without us*:

```
without us = leg reading + our own command on that leg
room to charge    = fuse limit - without us
room to discharge = fuse limit + without us
```

Two things are worth knowing about that:

- **Both directions count.** A main fuse carries the net current through it, so
  a leg exporting 25 A is as far into the fuse as a leg importing 25 A.
  Discharging into a leg already full of sun is a real way to trip one.
- **It only bites when it matters.** A leg doing 500 W of a 5175 W budget has
  more room than a pack can use, so nothing is limited and you will never notice
  this is running. It appears exactly when it was going to matter.

The margin (10 % by default) is deliberate. A fuse is not a cliff — a B-curve
holds well above its rating for a long time — but running it to the last ampere
leaves nothing for the kettle somebody switches on while we are deciding, and
our picture of the leg is up to 30 s stale anyway (gotcha 2).

**Fast charge respects this too.** It is the one place that commands full rating
outright, so it is the single most likely thing to drop a leg.

### Which pack is on which phase

The limit is worthless without knowing where each pack is, and nothing reports
it. So it is **measured**: every other pack is held at 0, one pack is commanded,
and after 20 s the leg that moved with it is the answer. It takes about a minute
per pack, during which the packs do not regulate.

The probe is itself bounded by the fuse — the legs are unknown, so its power is
capped by whichever leg has least room. On a busy evening there may be no room
to push into, and then it simply declines and tries again later.

It **refuses to answer** rather than guess. The winning leg has to show at least
half of what was asked for, and stand clearly above the runner-up, or the oven
that switched on halfway through gets a vote. A pack placed on the wrong leg
would mean guarding the leg that was never in danger, so no answer is much better
than a wrong one. Whatever it saw is kept in the **Phase detection** sensor's
`probes` attribute, and in the diagnostics download.

It measures when:

- a placement is missing — including on a fresh install,
- a pack dropped offline and came back (it is no longer provably the same pack
  on the same wiring),
- Home Assistant restarted — an integration that has been down cannot vouch for
  what changed while it was. Switch *Measure again after a restart* off once the
  wiring is known and stable, otherwise every restart parks the packs for a
  minute,
- you press the **Detect phases** button.

It never measures during a **dry run**: a shadow run writes nothing, and this
has to write to see anything. So during a shadow month the detection sensor
reads *Not while in dry run* — either type the phases in by hand on each unit,
or accept that the first probe happens when you go live.

**Typing them in wins.** Each unit's page in the wizard has a *Phase* field;
anything other than 0 there overrides every measurement. If you have read the
meter cupboard, you know better than a probe does.

### When a pack has not been placed

A pack whose leg is unknown is treated as if it were on whichever leg has least
room — because it might be. That is deliberately the pessimistic reading, and it
is why it is worth letting the detection finish or typing the phases in. On a
quiet house it changes nothing at all; on a loaded one it will hold that pack
back.

If the phase sensors are configured but **cannot be read**, the packs are held
at 0. Falling back to "no limit" would disarm the guard at exactly the moment
the meter is misbehaving.

## The icon

Home Assistant does not read an integration icon from its own repository — it
fetches brand images from `brands.home-assistant.io`, which is fed by the
[home-assistant/brands](https://github.com/home-assistant/brands) repository.
Until an icon lands there, the integration page shows a grey placeholder.

The files are ready in [`brands/`](brands/), laid out exactly as that repository
expects. To publish them:

1. Fork `home-assistant/brands`
2. Copy `brands/custom_integrations/battery_management/` into the same path there
3. Open a pull request

`scripts/make_brand_icon.py` regenerates them (needs Pillow, which is
deliberately not a dependency of the integration itself, so the manifest can
keep `requirements: []`).

Once merged, drop `ignore: brands` from the HACS step in
`.github/workflows/ci.yml` — it is only there because the brand does not exist
yet.

## Reference: every control and what it means

Settings carry their own explanation in Home Assistant, under each field in the
setup wizard and under Configure. Entities have nowhere to put that, so they are
listed here.

### Switches

| Switch | What it does |
| --- | --- |
| **Coordinator enabled** | The kill switch. Off hands the packs back and stops all coordination. This is not a mode: it is "let go entirely", which is why it is a separate switch. |
| **Fast charge (emergency)** | Charges every pack at full power to its limit, from the grid if needed. Once full it *holds* them there until you switch it off, topping up if they drift down — you pressed it to be ready for something. Never resumed after a restart. |
| **Dry run** | Decide everything, command nothing. On by default. Blocks every write including the safe revert, so it cannot fight another controller. The suppressed-command counter on this switch is its proof of life: a shadow run that suppressed nothing is a broken one. |

### Mode

One strategy at a time. Every mode is grid-zero regulation with a bound on it,
so the packs keep responding to the house and the sun inside whatever you pick —
they never sit idle merely because they are inside a window.

| Mode | What it does |
| --- | --- |
| **Follow the meter** | Charge on surplus, discharge on deficit. The floor under every other mode. |
| **Charge only** | Still fills from surplus, never discharges. |
| **Discharge only** | Still covers a deficit, never charges. |
| **Pause** | Holds at zero. Not the kill switch: the packs stay under control. |
| **Dynamic tariff** | Grid-zero plus buying on the cheapest hours and saving the charge for the dearest. Only offered once a price sensor is configured. |
| **External plan** | Executes a plan from elsewhere (EMHASS) through the `set_setpoint` service. Hands control back if no plan arrives. |

### Numbers

| Number | What it does |
| --- | --- |
| **SoC reserve** | Charge held back, in every mode. Raises each pack's *own* discharge floor rather than clamping the pair, so the split tapers towards it and a fuller pack carries the load alone. 0 = off. |
| **Buy at least to** | Floor under the computed charge ceiling. Use it when the solar forecast is too gloomy to trust. |
| **Buy at most to** | Cap on the computed charge ceiling. Use it when the forecast under-reads, which would otherwise let it buy more than needed. |

The last two bound grid buying only. Charging from your own surplus is never
capped — that would be throwing sun away.

### Sensors

| Sensor | What it means | Unavailable when |
| --- | --- | --- |
| **Setpoint** | Total power the packs are told to deliver. Positive discharging, negative charging. This is the integrator's own state, not a reading of the packs. | never |
| **Status** | idle · charging · discharging · fast_charge · hold · off · degraded | never |
| **Active policy** | Which rule is limiting things right now — see the table below. | never |
| **‹unit› target** | What that pack was told to do, signed. Keeps its last value when the pack drops offline, because it is still executing that command. | never |
| **Grid power (as read)** | The meter reading as this integration read it. Should sit exactly on your own P1 sensor. | the meter cannot be read |
| **Grid power (regulated against)** | What it actually steered on. Equal to the above when live; the reconstruction during a shadow run. | the meter cannot be read |
| **Other controller** | What the site's own automations are commanding, signed. | not shadow running |
| **Fast charge duration** | How long a *fast charge* would take from now — at full power, which is what the be-full-by blueprint triggers. Slowest pack, since they charge in parallel. Its `at_current_rate_minutes` attribute answers the other question: how long at the rate being commanded right now, which on solar surplus alone is far longer. | the charge time has not been measured |
| **Solar remaining** | Sun still expected today. Its attributes break down every forecast sensor separately, so "0 kWh" can be told apart from a sensor that is not reading. | no forecast sensors configured |
| **Charge ceiling** | How full it is worth buying to: 100 % − remaining sun ÷ capacity, within your two bounds. | the charge time has not been measured |
| **Plan** | Today's cheap and dear hours with their prices, plus the numbers the ceiling was computed from, all in attributes. | never |
| **Fuse headroom** | Amps still available on the busiest leg of your supply. Per-leg detail — load, room, and which packs sit on it — is in the attributes. | no per-phase sensors configured |
| **Phase detection** | Whether it knows which pack is on which leg, and how it found out. The `probes` attribute holds the measurements behind each placement. | never |

### Binary sensors

| Sensor | What it means |
| --- | --- |
| **Healthy** | Off is good. On means the control loop is degraded — usually the grid sensor cannot be read. |
| **‹unit› online** | Whether that pack's state of charge could be read on the last tick. Off means it was skipped; it keeps running its last command regardless, so check its target sensor too. |

### Buttons

| Button | What it does |
| --- | --- |
| **Detect phases** | Measures again which pack sits on which leg of the supply. Takes about a minute per pack, during which they hold at 0. A phase you typed in yourself is left alone — set that field back to 0 first if you want it re-measured. |

### What the active policy is telling you

| Policy | Meaning |
| --- | --- |
| Following the meter | Regulating normally, nothing limiting it |
| Difference too small to act on | Inside the deadband |
| Holding the SoC reserve | Would discharge, but the reserve says no |
| Packs empty / Packs full | Nothing left to give, or nowhere to put it |
| Mode: … | Your chosen mode is the limit |
| Held back by the main fuse | One leg of your supply is close to its fuse; see the Fuse headroom sensor |
| Measuring which pack is on which phase | A detection run is in progress; the packs hold at 0 for about a minute |
| Buying now, prices are low | A cheap hour, packs low, little sun coming |
| Holding the charge for dearer hours | Refusing to discharge now so the kWh go to the peak |
| Not buying, the sun still fits | What is coming free would not fit if it bought now |
| Dynamic, but no prices available | The mode is on but the price sensor is mute |
| Following an external plan | EMHASS or similar is driving |
| External plan went quiet | It stopped arriving; back to following the meter |
| Fast charging / Charged, keeping full | The override is running |
| No grid reading | The meter cannot be read; nothing is commanded |
| Coordinator off | The kill switch is off |

## Checking the meter is read correctly

Three sensors, which together are how a shadow run is verified:

| Sensor | What it is |
| --- | --- |
| `…_grid_power_as_read` | the meter reading as this integration read it |
| `…_grid_power_regulated_against` | what it actually steered on |
| `…_other_controller` | what the site's own automations are commanding, signed |

The first should track your own P1 sensor exactly — same value, same sign
(+ import). If it does not, the entity or the sign convention is wrong and
nothing downstream can be trusted. It goes **unavailable** the moment the meter
cannot be read, which is the fastest way to notice a dropout.

During a shadow run the second is the *reconstruction*: the meter as it would
read if this coordinator were in charge instead of the site's automations. It
should differ from the first — that difference is roughly what the other
controller is doing. When running live the two are identical.

```yaml
type: history-graph
title: Is the meter read correctly?
hours_to_show: 6
entities:
  - entity: sensor.p1_meter_power
    name: Your own P1 sensor
  - entity: sensor.battery_management_grid_power_as_read
    name: What the integration read
  - entity: sensor.battery_management_grid_power_regulated_against
    name: What it steered on (reconstructed)
  - entity: sensor.battery_management_other_controller
    name: What your automations command
```

The first two lines should sit exactly on top of each other. If they ever
separate, that is a bug worth reporting.

## Seeing today's plan

`sensor.battery_management_plan` carries the day's intentions as attributes:
which hours it picked as cheap and dear, how much sun is still expected, the
usable capacity, and the resulting charge ceiling. A markdown card renders it:

```yaml
type: markdown
title: Plan for today
content: >-
  {% set p = state_attr('sensor.battery_management_plan','cheap_hours') %}
  {% set d = state_attr('sensor.battery_management_plan','dear_hours') %}
  {% set sun = state_attr('sensor.battery_management_plan','solar_remaining_kwh') %}
  {% set cap = state_attr('sensor.battery_management_plan','usable_capacity_kwh') %}
  {% set ceil = state_attr('sensor.battery_management_plan','charge_ceiling') %}

  **Sun still to come:** {{ sun if sun is not none else '?' }} kWh
  {%- if cap %} of {{ cap | round(1) }} kWh storage{% endif %}

  **Buy up to:** {{ ceil | round(0) if ceil is not none else 'not computed' }} %

  {% if p %}**Cheap hours** — buy here
  {% for h in p %}
  - {{ as_timestamp(h.start) | timestamp_custom('%H:%M') }} · {{ h.price }}
  {%- endfor %}
  {% endif %}

  {% if d %}**Dear hours** — the charge is saved for these
  {% for h in d %}
  - {{ as_timestamp(h.start) | timestamp_custom('%H:%M') }} · {{ h.price }}
  {%- endfor %}
  {% endif %}
```

Two sliders bound the computed ceiling, because it is only as good as the solar
forecast behind it: **Buy at least to** and **Buy at most to**. Leave them at
0 and 100 and the calculation passes through untouched. They only limit buying
from the grid — charging from your own surplus is never capped, since that would
be throwing sun away.

`sensor.battery_management_solar_remaining` and
`sensor.battery_management_charge_ceiling` carry the same two numbers on their
own, so they can be graphed.

The plan deliberately does **not** predict the setpoint. That depends on the
house minute by minute, and a graph claiming otherwise would look authoritative
and be wrong.

## External plan (EMHASS)

The **External plan** mode does not plan anything itself. It executes a plan
produced elsewhere — typically [EMHASS](https://github.com/davidusb-geek/emhass),
which optimises against price, solar and load forecasts.

**EMHASS is a separate project.** It is not installed with this integration and
is not a dependency (`requirements: []`). Install it yourself, then have it call
`battery_management.set_setpoint` — positive discharges, negative charges.

What this integration keeps doing underneath the plan: the SoC-weighted split
across packs, the per-unit and total capacity clamps, never commanding two packs
in opposite directions, the SoC reserve (which overrules the plan), and the safe
revert on unload.

If no plan arrives for `external_timeout` minutes (default 15) it hands control
back to normal grid-zero regulation rather than freezing the packs on the last
instruction — they have no watchdog of their own. Home Assistant raises a repair
issue explaining what is missing, and clears it once a plan arrives.

## Services

| Service | What it does |
| --- | --- |
| `battery_management.set_setpoint` | Force the integrator's setpoint (+ discharge, − charge). The next tick clamps it to what the packs can deliver and regulation carries on. |
| `battery_management.start_fast_charge` | Charge every unit at full power to its limit, then switch off by itself. |
| `battery_management.stop_fast_charge` | End fast charging. |

Each takes an optional `config_entry_id`; leave it out to act on every
coordinator.

The card shows status + setpoint, an enable toggle, a fast-charge button (with a
confirm dialog), and a per-unit SoC bar / target / status. If the card doesn't
appear after install, hard-refresh the browser; if it still doesn't, add
`/battery_management/battery-management-card.js` (JavaScript Module) under
Settings → Dashboards → Resources.

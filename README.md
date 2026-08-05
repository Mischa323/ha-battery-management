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
* **Fast charge (emergency).** One switch to stop discharging and charge both
  packs to full as fast as possible (from the grid if needed), then it hands
  back to normal automatically.
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

This is an early version (0.1.0). The control loop is a port of a field-tested
YAML setup. Test on your own system and open issues for anything that needs
tuning — especially the exact `min output` for your firmware and the mode/flow
option strings if a future firmware renames them (see `const.py`).

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
  parameters; measure it once. Until you do, `sensor.…_minutes_to_full` stays
  unavailable and the automation does nothing, on purpose — arriving late on a
  guessed duration defeats the point.

Deliberately *not* a scheduler inside the integration: less to maintain, and
anything the blueprints did not anticipate you can still build with an ordinary
automation.

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

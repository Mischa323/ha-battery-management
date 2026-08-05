# Solarbank Coordinator

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
2. Add `https://github.com/<you>/solarbank-coordinator` as an **Integration**.
3. Install **Solarbank Coordinator**, then restart Home Assistant.
4. Settings → Devices & Services → **Add Integration** → *Solarbank Coordinator*.
5. Pick your grid-power sensor, the number of units, and per unit its four (or
   six) control entities. Done.

Manual install: copy `custom_components/solarbank_coordinator` into your HA
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

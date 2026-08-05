# CLAUDE.md — Battery Management (repo: `ha-battery-management`)

Project context for Claude Code. Read this first.

## What this is

A **Home Assistant custom integration** that coordinates **2+ Anker SOLIX Max AC**
batteries as one system, driven off a **household grid-power sensor** (P1 meter).
It is **solar-agnostic** (steers on the grid meter, not the panels), so it works
at multiple sites with different PV. Goal: install via **HACS** across a few
family/friend sites and maintain centrally from this repo.

Integration domain: `battery_management`. This is **v0.1.0** — a working
scaffold, not yet field-tested inside HA.

## Origin — it's a port of a field-tested YAML setup

The logic was first built and proven as HA automations + helpers on a live
system, then rewritten into this Python integration. Behaviour to preserve:

- **One brain, anti-windup integral control.** Every ~15 s: read grid power,
  `error = grid - bias`, integrate a **stored setpoint** (`sp += kp*error`,
  clamped to deliverable range). The setpoint is the integrator state — it is
  **NOT** derived from the units' power sensors (those lag; see gotchas).
- **Never opposite directions.** Both units always get the same `grid_flow`
  (both `charge` or both `discharge`) → cross-charging is impossible.
- **SoC-weighted split.** Discharge weight `= soc - discharge_limit`; charge
  weight `= charge_limit - soc`. Fuller unit discharges more; emptier charges more.
- **Min-output flooring.** Shares below `min_output` are consolidated onto one
  unit (avoids the smaller-share unit flipping idle/active at low load).
- **Kill-switch** → hand back to native `self_consumption`. **Fast-charge** →
  both units charge at max to full, then auto-off. **Safe revert** to
  `self_consumption` on unload/error.

## What's built (files)

```
custom_components/battery_management/
  __init__.py          # setup/unload; reverts units to self_consumption on unload
  const.py             # domain, config keys, defaults, mode/flow option strings
  coordinator.py       # BatteryCoordinator: the control loop + _distribute()
  config_flow.py       # wizard: grid sensor + N units + tunables; options flow
  switch.py            # "Coordinator enabled" (kill-switch), "Fast charge"
  sensor.py            # "Setpoint" (W, +discharge/-charge), "Status"
  binary_sensor.py     # "Healthy" (problem device_class)
  strings.json + translations/{en,nl}.json
  manifest.json
hacs.json, README.md, LICENSE
```

`coordinator.py::_distribute()` is unit-tested by hand and behaves: normal load
splits proportionally; low load consolidates onto one unit; offline unit (weight
0) is skipped; per-unit clamp to `unit_max`.

## Real target environment (the primary site's entity IDs — for testing)

- Grid: `sensor.p1_meter_power` (**+ = import, - = export**), 3-phase; per-phase
  `sensor.p1_meter_power_phase_1/2/3` exist too.
- **Unit 093 ("Batterij 01")** entity prefix `anker_solix_solarbank_max_ac_093`:
  - mode: `select.anker_solix_solarbank_max_ac_093_operating_mode_device_runs_by_command_in_third_party_controlled`
  - flow: `select.anker_solix_solarbank_max_ac_093_grid_flow`
  - target: `number.anker_solix_solarbank_max_ac_093_target_grid_power`
  - soc: `sensor.anker_solix_solarbank_max_ac_093_soc`
  - limits: `number...._charging_limit`, `number...._discharge_limit`
- **Unit 052 ("Batterij 02")** entity prefix `tuin_batterij_02` (named after area
  + device, NOT the model): `select.tuin_batterij_02_operating_mode_device_runs_by_command_in_third_party_controlled`, `..._grid_flow`, `number.tuin_batterij_02_target_grid_power`, `sensor.tuin_batterij_02_soc`, `..._charging_limit`, `..._discharge_limit`.
- Entities come from the **Anker SOLIX Official (Modbus TCP)** integration.
- Option strings: mode = `self_consumption` / `third_party_control`; flow =
  `charge` / `discharge`.

## Gotchas learned the hard way (don't regress these)

1. **Third-Party Control has NO watchdog on the device** — a unit holds the last
   command **indefinitely**. Hence kill-switch + safe-revert-on-unload. If HA
   itself dies mid-command the unit keeps going until its SoC limit (charge 100%
   / discharge 5%) — those limits are the hardware backstop. Document this.
2. **Modbus sensors lag ~10-30 s and update in bursts.** e.g.
   `battery_discharging_power` can read 0 while `ac_output` shows the real output.
   Never drive control off the laggy per-unit power sensors — that's why the
   integrator tracks its own setpoint.
3. **A 10 s interval caused wind-up / oscillation** (both units discharged into
   an export). 15 s + the stored-setpoint anti-windup design is stable. Keep the
   interval configurable but default 15 s; keep Kp low (0.25).
4. **`target_grid_power` max attribute is unreliable per unit** — 052 reports
   3500, 093 reported a bogus 10000. Always cap at `min(reported_max, unit_max)`
   (unit_max default 3500 = the Max AC AC rating). Coordinator already does this.
5. **Solar is limited by a 16 A breaker (~3.68 kW)** at the primary site — not
   relevant to the coordinator (it's grid-driven) but relevant if you ever model
   PV.

## TODO

### A. Field work — nothing below is proven until this is done

1. **Test on real HA** next to the existing YAML setup; compare; tune. Tooling is
   ready: `.devcontainer/` + `scripts/develop`, and `config/packages/simulator.yaml`
   fakes the P1 meter and both units so the wizard can be run without hardware.
2. **Measure the real `min_output`** (the wattage below which a Max AC idles) —
   150 W is a guess. It decides when the pack consolidates onto one unit.
3. **Confirm mode/flow option strings** against the installed Anker integration
   (some firmwares/integrations may rename them) — centralised in `const.py`.
   Highest-risk of the three: if these are wrong the coordinator does nothing at
   all and only reports `degraded`.

### B. Charging options — design settled with the owner, read before touching

Rules that the whole roadmap below depends on:

- **Grid-zero is not a mode, it is the floor.** Charging from solar surplus and
  discharging on deficit is what the existing loop already does. Every mode is
  "grid-zero, plus these rules at these moments", so the pack never sits idle
  just because it is outside a window.
- **One mode at a time**, chosen from a `select`. No stack of policies fighting
  each other. If something must apply in *every* mode it becomes a separate
  always-on setting (like the SoC reserve), not a seventh combination in the list.
- **Nothing is mandatory.** Configure none of it and the integration behaves
  exactly as it does today. Dynamic mode is not even offered without a price
  sensor.
- **Constraints, not forced power, wherever possible.** "Do not discharge" is a
  bound on the setpoint; the integrator keeps regulating inside it. Only real
  grid charging (fast charge, cheap-hour charging) forces a value.
- **Fast charge stays an override, not a mode** — you prepare for a storm without
  losing your strategy, and it returns to the mode you were in.
- The kill-switch stays a separate switch: "stop coordinating, hand the packs
  back" is not a flavour of coordinating, and gotcha 1 makes one unambiguous
  emergency off worth having.

Build order, and why:

4. **Setpoint bounds + `sensor.…_active_policy` + SoC reserve.** *First*, because
   these are one mechanism, not three: a way to bound the setpoint per tick, a
   sensor saying which rule won, and the first user of it. Biggest saving per
   line of code — grid-zero happily empties the packs by 18:00 and then imports
   all evening at peak. It is also the first feature that makes a pack *refuse*
   to do something, so the "why" sensor earns its keep immediately instead of
   being a nice-to-have. Independent of everything else below.
5. **Hold after fast charge.** Small, and it closes a real gap in a feature that
   already exists: fast charge switches off at full, the mode resumes, and the
   packs discharge again before the storm arrives. Charge-then-hold is what the
   button is actually for.
6. **Mode select**: Grid-zero · Charge only · Discharge only · Pause · Dynamic.
   Nearly free once 4 exists — the middle three are setpoint bounds, no new
   control logic. Dynamic is only listed when a price sensor is configured.
7. **Schedule blueprint.** Time windows come from Home Assistant's own `schedule`
   helper plus an automation that flips the mode select, shipped as a blueprint
   so family sites only fill in times. Deliberately *not* a scheduler inside the
   integration: less to maintain, and anyone can build what we did not think of.
   Needs 6.
8. **"Full by time T"** for fast charge — work back from SoC and charge rate to
   decide when to start. Moderate: needs a rate estimate. Timing fast charge
   *crudely* already works today via the `start_fast_charge` service.
9. **Dynamic tariff.** *Last*, on purpose. Most moving parts (price source,
   forecast, decision rules), the only item with external dependencies, and no
   dynamic contract at the primary site yet.
   - **One entity picker for a price sensor**, not per-supplier support. Any
     integration that exposes prices works, and a site can switch supplier by
     swapping the sensor.
   - **Exchange (EPEX/day-ahead) prices are enough to decide with.** Energy tax,
     VAT and supplier markup are a near-flat per-kWh adder, so they shift every
     hour equally and leave the cheap-to-expensive *ranking* unchanged. Supplier
     prices only matter for showing euros. So this can be built before a dynamic
     contract exists. Note that a negative exchange price is usually still
     positive for the consumer after tax.
   - Charge on the cheapest hours **when SoC is low AND little sun is expected**
     (Forecast.Solar). NL is moving to **quarter-hourly** prices (Frank/Zonneplan
     give true 15-min; Tibber/Nord Pool hourly for NL).

### C. Loose ends

10. **`home-assistant/brands` icon** — the HACS action runs with `ignore: brands`
    until that PR lands. It is a PR to a Home Assistant repo, so the owner
    submits it.
11. **Card rendering unverified** — the card is served and registered, but nobody
    has looked at how it actually renders on a dashboard.

### D. Done

- **Per-unit observability.** `coordinator.unit_status` feeds a per-unit
  `binary_sensor` (connectivity) and a per-unit target `sensor` (signed,
  + discharge / − charge; `grid_flow` + `soc` as attributes). The target
  deliberately **keeps its last value when a unit goes offline**, because of
  gotcha 1 — the pack is still executing that command.
- **Persisted state.** A `Store` keyed on the entry id holds `enabled` +
  `setpoint` + `saved_at`; written through on a switch flip, debounced (30 s) on
  each tick. The coordinator **resumes on its own** after a restart or an options
  save. The setpoint is only restored if younger than `MAX_SETPOINT_AGE` (5 min).
  `fast_charge` is deliberately **never** restored.
- **Deterministic min-output consolidation.** `_distribute` drops the
  *lowest-weight* unit and re-splits, so units join in weight order. The old
  version handed the leftover to "the other unit" and the whole load ping-ponged
  between packs every tick.
- **Lovelace card.** Ships in `www/` and `__init__.py` auto-registers it
  (verified: served, HTTP 200). Its claim that low-tariff charging was active has
  been removed — B9 does not exist yet and a dashboard must not lie.
- **Config flow.** `validate.py` rejects duplicate unit names, an entity used
  twice on one unit, and a SoC-limit field pointing at a watt entity (that
  mistake made both packs ping-pong every tick — the limit fed the coordinator's
  own output back in). `discovery.py` resolves all six entities from one Anker
  **device**, shown for review rather than silently accepted, ties left blank on
  purpose. Options flow has a menu — **tuning** and **units** — so a mis-picked
  entity no longer means deleting and re-adding the entry.
- **N>2 units.** `tests/test_config_flow.py` drives the real wizard through 1 and
  3 units via Home Assistant itself; `_distribute` is unit-tested with 3.
- **services.yaml.** `set_setpoint`, `start_fast_charge`, `stop_fast_charge`,
  each with an optional `config_entry_id`.
- **HACS/CI.** `.github/workflows/ci.yml` runs hassfest, HACS, and pytest both
  stubbed and against a real Home Assistant.

## How to run

- Copy `custom_components/battery_management` into a HA `config/custom_components/`
  (or use the official HA devcontainer), restart, then Settings → Devices &
  Services → Add Integration → *Battery Management*.
- Watch the **Setpoint** and **Status** sensors and each unit's `target_grid_power`
  and `grid_flow`. Enable via the **Coordinator enabled** switch.

## Style / constraints

- Modern HA patterns (config entries, entity platforms, async). No blocking I/O
  in the event loop. Never let the control tick raise uncaught (it currently
  catches, logs, sets `status=degraded`). Keep it dependency-free
  (`requirements: []`). Not affiliated with Anker.

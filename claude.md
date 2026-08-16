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
  suppliers.py         # fetch prices ourselves (Frank Energie); pure parsing
  phases.py            # pure: per-leg fuse room, and attributing a probe to a leg
  config_flow.py       # wizard: grid sensor + N units + tunables; options flow
  switch.py            # "Coordinator enabled" (kill-switch), "Fast charge"
  button.py            # "Detect phases"
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
5. **The packs are single-phase; the loop regulates a three-phase total.**
   Nothing in the Modbus data joins those up, so without the fuse protection a
   pack will charge at 3500 W on a leg already pulling 20 A while the other two
   idle and the total looks fine. The per-leg placement is *measured*, because
   nothing reports it - see 18.
6. **The packs hold 7 kWh each, 14 kWh together** (confirmed by the owner,
   2026-08-16). That makes `full_charge_minutes = 120` exact rather than a
   guess - 3500 W x 2 h - so the solar-aware charge ceiling has real capacity
   to work from at the primary site. Do not try to derive this from a short
   trace: an afternoon of quiet regulation moves the state of charge by two or
   three points, and at 1 % resolution that gives answers between 4.8 and
   13.5 kWh. It needs a full charge, or asking.
7. **Solar is limited by a 16 A breaker (~3.68 kW)** at the primary site — not
   relevant to the coordinator (it's grid-driven) but relevant if you ever model
   PV.

## TODO

### A. Field work — nothing below is proven until this is done

1. **Test on real HA** next to the existing YAML setup; compare; tune.
   **The agreed route is a month of shadow running, so this now waits on C12/C13
   — the integration must not touch the packs until it has been watched.** The
   YAML setup stays in charge throughout; we only compare what this one *would*
   have done. Tooling for offline work is ready: `.devcontainer/` +
   `scripts/develop`, and `config/packages/simulator.yaml` fakes the P1 meter and
   both units so the wizard can be run without hardware.
2. **Measure the real `min_output`** (the wattage below which a Max AC idles) —
   150 W is a guess. It decides when the pack consolidates onto one unit.
3. ~~**Confirm mode/flow option strings**~~ — **answered, 2026-08-05, and it was
   not a naming problem.** The two packs do not offer the same modes:

   - 093: `self_consumption, tou_mode, third_party_control, custom_mode,
     smart_mode, dynamic_pricing`
   - 052: `third_party_control, custom_mode` only

   Both were in `third_party_control` at the time, so it is not a
   valid-transition thing — **052 has no P1 meter of its own, so its firmware
   hides self-consumption entirely.** Grid flow is `charge`/`discharge` on both.

   Consequence, and it is the important one: the old fixed revert to
   `self_consumption` **silently fails on 052**. The command is simply not
   accepted, and per gotcha 1 that pack then keeps its last instruction forever.
   **The site's existing YAML kill-switch almost certainly has this same hole** —
   worth verifying there.

   Fixed by making it per unit (`mode_control` / `mode_safe`, chosen in the
   wizard from the entity's own options). Empty `mode_safe` means "command 0 W
   and leave the mode alone", which is what a meterless pack needs: holding 0 W
   indefinitely is a safe resting state, unlike holding a non-zero one.

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

4. ~~**Setpoint bounds + `sensor.…_active_policy` + SoC reserve**~~ — **done.**
   One mechanism, not three. `number.…_soc_reserve` raises each unit's *own*
   discharge floor (`max(discharge_limit, reserve)`) rather than clamping the
   pack as a whole, so the SoC weighting tapers towards it and a pack above the
   reserve carries the load alone. Applies in every mode, defaults to 0 = off.
   `sensor.…_active_policy` answers "which rule is limiting me": it distinguishes
   `soc_reserve` from `packs_empty` by checking whether the packs would have had
   anything to give *without* the reserve — "I am holding some back" is a
   different answer than "I am empty". The reserve is stored as a setting, so it
   survives a restart even when the coordinator was switched off.
5. ~~**Hold after fast charge**~~ — **done.** Once every pack is full the switch
   stays *on* and holds them there (target 0, no discharge) until the user
   releases it; it tops up again if a pack drifts down. Switching off at full
   handed control straight back to the mode, which discharged the packs again -
   defeating the reason you pressed it before a storm. `fast_charge_hold` in the
   tuning options restores the old auto-release. Not resumed after a restart,
   same reasoning as fast charge itself.
6. ~~**Mode select**~~ — **done.** `select.…_mode`: Follow the meter · Charge
   only · Discharge only · Pause. Each is a bound on the setpoint applied at the
   existing anti-windup clamp, so grid-zero keeps regulating inside it and the
   integrator cannot build pressure against a bound it may not cross. Pause is
   not the kill-switch: the units stay in third-party control holding at 0. The
   mode persists across restarts and an unknown stored mode falls back to
   grid-zero. Dynamic arrives with 9. The Anker device's own option strings are
   now `DEVICE_MODE_*` so `MODE_*` means the coordinator's own modes.
7. ~~**Schedule blueprint**~~ — **done.** Two blueprints under
   `blueprints/automation/battery_management/`: one driven by a Schedule helper
   (re-syncs on HA start, so a window that began during downtime still applies)
   and one plain start/end time with weekdays. `tests/test_blueprints.py`
   validates them with Home Assistant's own blueprint schema, substitutes real
   inputs, runs the result through the automation validator, and asserts the
   mode options they offer still match `MODES` — a label pointing at a removed
   mode would otherwise be a silent no-op. The dev config gained `automation:`
   and `schedule:` so they can be tried there.
8. ~~**"Full by time T"**~~ — **done.** `sensor.…_minutes_to_full` does the
   arithmetic (slowest pack, not the sum — they charge in parallel) and the
   `be_full_by_time` blueprint decides when to press the button: same split as 7,
   calculation inside, scheduling in Home Assistant.
   State of charge is a percentage and the packs never report their capacity, so
   empty-to-full time cannot be derived; it is a measured option
   (`full_charge_minutes`, default 0). At 0 the sensor is **unavailable** rather
   than guessing — a "be full by 18:00" built on a guess is worse than none.
   Still open: learning that duration from observed charges instead of typing it.
9. ~~**Dynamic tariff**~~ — **done.** `prices.py` recognises *shapes*, not
   integrations: lists of dicts (`raw_today`, `prices`, `prices_today`, …) and
   bare number lists of 24 or 96 (hourly / quarter-hourly), inferring missing end
   times from the next slot. Unrecognised means an **empty** forecast, which
   disables cheap-hour charging — never a guessed price. One entity picker, so a
   site changes supplier by pointing elsewhere.
   Cheap slots are ranked over a **rolling 24 h window**, not over everything
   published: with tomorrow already known, a 48 h ranking can decide nothing
   today is worth charging on and leave the packs flat through this evening.
   Three conditions must all hold before a cent is spent: cheap now, a pack below
   `charge_below_soc`, and not more sun expected than `solar_forecast_max` (0 =
   ignore; a *missing* forecast is not treated as "lots of sun"). Buying is the
   one thing expressed as a forced value rather than a bound — there is no
   surplus to regulate against.
   `MODE_DYNAMIC` is only in the select's options when a price sensor is set, so
   the select builds its options per entry rather than per class.
   Still open: showing what it actually cost, which needs supplier prices rather
   than exchange prices — see 10, and do not invent the number.

10. **Hard surplus clamp — make "solar only" a guarantee, not a tendency.**
    Charging follows the meter, so the energy comes from the surplus by
    construction — but the integrator lags. Measured: when a cloud kills a
    2000 W surplus, the packs keep charging from the grid for about seven ticks,
    tapering 1668 → 0 W, roughly **20 Wh per event** at Kp 0.25 / 15 s. Fine
    once, mildly annoying on a broken-cloud day.
    The fix is a bound, so it fits the existing clamp: the surplus *is*
    derivable even while charging, because we know what we commanded —
    `surplus = -(grid + setpoint)` — so `lower = -min(maxchg, surplus)`.
    The catch is gotcha 2: the meter reflects the packs' response 10-30 s late,
    so that estimate is noisy and clamping too eagerly would stop legitimate
    charging. Wants a deadband of its own, and measuring on real hardware first.
    Should be **optional** (default off), since paying 20 Wh for a loop that
    never oscillates is a fair trade for most sites.

11. **Optional PV production sensor.** The owner has Enphase (15 micro-inverters
    via Envoy, live production + per-panel), so a real sensor is available.
    **It adds nothing to the surplus calculation** and that is worth writing
    down, because it looks like it should. From the energy balance
    `house = pv + grid + battery`, the surplus is `pv - house = -(grid + battery)`
    — PV cancels out. The meter plus our own command already determine it exactly.
    Where it genuinely helps:
    - *An independent ceiling.* The weak link in that formula is that `battery`
      is what we **commanded**, not what the packs did, and per gotcha 2 those
      diverge for 10-30 s. Charging can never legitimately exceed live
      production, so `charge <= min(derived surplus, pv_now)` catches a pack
      that ignored an order. Pairs with 10.
    - *An explicit "solar only" mode* — charge while the panels produce, nothing
      else. Explainable to family without mentioning integrators.
    - *Calibrating the forecast veto.* Forecast.Solar under-predicted by 50 % on
      2026-08-05 at the primary site (14.9 kWh predicted, 22.46 actual; five of
      the fifteen panels face north). Setting `solar_forecast_max` against raw
      forecast values would mean buying power that arrives free. With live
      production the threshold can be set against the site's own measured bias.
    Optional like everything else: no sensor, no change.

15. ~~**Price-aware discharge, and solar headroom**~~ — **done.** Two halves of
    one idea, both from the owner.
    *Discharge:* the packs hold less than a day's consumption, so the question is
    where the stored kWh are spent. In dynamic mode the setpoint's upper bound
    goes to 0 outside the dearest `expensive_hours`, so a cheap midday hour is
    bought from the grid and the charge is kept for the peak. Two escapes, both
    about not wasting free energy: a pack above `discharge_anyway_soc` (90 %)
    discharges regardless, and without prices there is nothing to be clever with.
    *Charging:* a whole-day solar forecast is right at 02:00 and wrong at 17:00,
    when most of it is already produced — the old veto read that as "lots of sun
    coming, do not buy" exactly when topping up for the evening was right. So it
    is now a ceiling instead: buy up to `100 % - remaining sun / capacity`. At
    02:00 with more sun coming than the packs hold that is zero; at 17:00 with an
    hour left it is ~93 %. Capacity is not configured — it falls out of the
    empty-to-full time that has to be measured anyway.
    Several forecast sensors can be picked and are summed (Forecast.Solar
    publishes one per roof plane; the primary site has three). Prefer the
    "remaining today" ones, or add a "produced so far" sensor to subtract.
    Falls back to the old threshold while the capacity is unmeasured.

### C. Proving it on real hardware before it touches anything

The owner runs a working YAML setup on live batteries. The plan is a month of
shadow running next to it, then switch over. These two items are what make that
possible, and they gate section A.

12. ~~**Dry run**~~ — **done.** `switch.…_dry_run`, **on by default** so a fresh
    install watches before it acts. Every write goes through `_svc_select` /
    `_svc_number`, so one guard there covers targets, grid flow, the
    operating-mode select *and* the safe revert — tests assert each of those
    separately, plus that turning the coordinator on claims nothing and that
    dynamic mode buys nothing. Everything else keeps running: the setpoint and
    the per-unit targets are identical to a live run, so the comparison data
    accumulates in long-term statistics on its own. A suppressed-command counter
    (on the switch and on the Status sensor) is the proof of life — a shadow run
    that suppressed nothing is a broken one. Going live logs at warning level.
    Survives restarts, so a month-old shadow install cannot come back live.
    **Still true, and the reason 13 exists:** in shadow the site's own
    automations regulate the meter, so our integrator sees a near-zero error and
    parks at zero. Direction, split, reserve and price decisions compare
    honestly; setpoint *trajectories* do not, until 13 closes the loop.
13. ~~**Simulated plant**~~ — **done.** In dry run the loop now closes on
    reconstructed data: `net demand = grid + battery`, `our meter = net demand -
    our own setpoint`. A test pins the failure it prevents — with simulation off,
    a shadow run sees a near-zero error and parks, because the other controller
    already did the work.
    **Correction to what this item used to say: it does not need the PV sensor.**
    PV cancels out of that algebra exactly as it does out of the surplus
    calculation — the same trap this file warns about under 11, walked into while
    writing the list. What it needs is the other controller's *current* power,
    and that can simply be read back from the target/flow entities we would have
    written to, since whoever is in charge writes there. A measured
    `battery_power_sensor` overrides the readback where one exists, because a
    command is only as accurate as the packs are obedient.
    Falls back to the real meter when neither is observable: an honest open loop
    beats a wrong reconstruction. Both the real meter reading and the other
    controller's power are recorded per tick so the reconstruction can be checked
    afterwards.

14. ~~**Execute an external plan (EMHASS)**~~ — **done.** `MODE_EXTERNAL`; the
    plan arrives through the existing `set_setpoint` service, which now also
    records *when* it arrived. Everything underneath still applies and is tested:
    the SoC-weighted split, the capacity clamp, never-opposite-directions, the
    SoC reserve overruling the plan, and dry run suppressing it entirely.
    **Staleness is the point.** A plan that stops arriving hands control back
    after `external_timeout` (default 15 min) instead of freezing the packs on
    its last instruction — gotcha 1 in a new coat, since the packs have no
    watchdog of their own. The handover is deliberately *smooth*: the setpoint is
    the integrator state, so grid-zero resumes from where the plan left off
    rather than snapping to zero and slamming the packs shut.
    `POLICY_EXTERNAL` / `POLICY_EXTERNAL_STALE` say which of the two is happening,
    and the diagnostics carry the last plan and its age.
    EMHASS itself is **not** a dependency and is not installed with this — the
    manifest still has `requirements: []`. Do not add it during the shadow month:
    three controllers at once and nothing can be attributed to anything.

### D. Statistics and dashboard

15. **Statistics cards + a shipped dashboard.**
    - *Already works:* Setpoint and the per-unit targets carry
      `device_class: power` + `state_class: measurement`, so Home Assistant
      records long-term statistics for them and a `statistics-graph` card needs
      no code at all. `number.…_soc_reserve` has no state class — adding
      `measurement` would let you see when it was changed against the effect.
    - *Build:* an example dashboard in the README — statistics-graph for setpoint
      and per-unit targets, SoC alongside, and the **active-policy sensor
      overlaid**. That last one is what makes the graphs readable: a flat line
      stops being a mystery when you can see it says "holding the SoC reserve".
    - **Do not derive energy (kWh) from the coordinator's own commands.** The
      target sensors are what we *told* the packs to do. Per gotcha 2 the Modbus
      sensors lag 10-30 s and update in bursts, so integrating our own commands
      would produce an authoritative-looking number that is the plan, not the
      outcome — the same trap as the card claiming low-tariff charging. Use the
      units' own energy sensors, or a Riemann-sum (`integration`) helper on their
      real power sensors, and document wiring that into HA's Energy dashboard
      under "Home battery storage".
    - *Genuinely new figure:* **time per mode and per policy** — "the packs spent
      4 h holding the SoC reserve last week" is what tells you the reserve is set
      wrong. Enum sensors get no long-term statistics, so this needs
      `history_stats` sensors; ship them as an optional package or blueprint
      rather than hard-coding entities nobody asked for.
    - Depends on nothing, but only worth doing after A: graphs of a system that
      has not run against real hardware are graphs of a simulator.

### E. Loose ends

16. **`home-assistant/brands` icon** — *files ready, PR outstanding.* A battery
    with two cells, drawn by `scripts/make_brand_icon.py`, sits in `brands/`
    laid out as that repository expects; it stays legible down to 32 px. The
    pull request is a public contribution under the owner's name, so the owner
    submits it. The HACS step keeps `ignore: brands` until it lands.
17. ~~**Card rendering unverified**~~ - **seen at last, 2026-08-16.** It lays
    out fine: labels do not collide at dashboard width, green and red are
    distinguishable, the summary line reads well. Two things the render caught
    that no test had:
    - **The chart spoke UTC.** `hhmm` sliced the ISO string instead of parsing
      it, so midnight was labelled 22:00 and the evening peak looked like an
      afternoon one. Two hours wrong for half of Europe, and invisible in the
      arithmetic tests because they never asked what a slot was *called*.
    - **Moments were compared as text.** Fine while everything carries `Z`, as
      Frank does, but a sensor publishing `+02:00` would have sorted wrong and
      lost the "now" marker entirely - a latent break in the third-party route.
    `tests/card/clock.mjs` pins both against a real payload under a real zone.

17b. **Old note:** card rendering unverified - and the price chart added in 0.6.1 raises
    the stakes: its geometry is checked (negative prices hang below the zero
    line rather than being clipped) but nobody has looked at how it lays out.
    The colours were validated rather than eyeballed - green/red is the worst
    possible pair for deuteranopia, so the steps were chosen to clear CVD
    dE 9.7 on both a light and a dark card, and the role is also in the tooltip
    and the legend so hue never carries it alone.
17b. **Old text:** card rendering unverified — the card is served and registered, but nobody
    has looked at how it actually renders on a dashboard.

### F. Not tripping the main fuse

18. ~~**Per-phase fuse protection**~~ - **done, and it is the first thing here
    that moves the packs on its own initiative.** Asked for by the owner: the
    packs could draw their full rating onto a leg that was already near 25 A.

    *The limit* is a bound per leg applied per unit, at the existing anti-windup
    clamp, so it composes with the modes and the SoC reserve and the integrator
    cannot wind up against it. `without us = leg reading + our own command`,
    then `room = fuse limit -/+ that`. **Both directions count** - a fuse
    carries net current, so a leg full of sun is as close to it as a loaded one.
    Fast charge is capped too; it is the one place that commands full rating
    outright. Configured but unreadable sensors **hold the packs at 0** rather
    than falling back to "no limit". Entirely opt-in: no per-phase sensors, no
    behaviour change at all.

    *The placement* is measured, per the owner's proposal: hold everything at 0,
    command one pack, see which leg moves. Refinements on top of that: the probe
    is capped by whichever leg has least room (the leg is unknown, so worst
    case), it declines below 600 W of room rather than measuring noise, and it
    **refuses to answer** unless the winner shows half the probe power *and*
    twice the runner-up. A pack on the wrong leg guards the leg that was never
    in danger, so no answer beats a wrong one. Re-measures when a pack returns
    from offline and after a restart, both as asked, both switchable; a
    hand-typed phase always wins.

    Two things fell out of building it:
    - **`_distribute` now redistributes what a ceiling refused.** It used to
      drop it, on the reasoning that this prevented overshoot - it cannot
      overshoot, only what is left of the request is handed on, and the request
      is already clamped to the sum of the ceilings. What the old behaviour did
      produce was a steady-state shortfall the integrator could not correct.
      Invisible at 3500 W ceilings, glaring at the few-hundred-watt ones the
      fuse produces.
    - **The detection flag has to be set before the task is created**, not
      inside it. In between, the same tick regulated the packs and the next tick
      launched a second probe on top of the first.

    ~~Still open: the probe has never run against real packs.~~ **It has, on
    2026-08-09, and it worked first time.** 093 → L1 (delta 3569 W against a
    runner-up of 2 W), 052 → L3 (3547 W against 18 W). 20 s of probe was
    plenty; the attribution margins were not close.

    What the same download *did* show is that **the settle was too short**:
    052's probe recorded −3557 W on L1, which was 093 still winding down ten
    seconds after being told to stop. Gotcha 2 in the act. Harmless there,
    because the packs were on different legs and the attribution looks for the
    largest *rise* - but two packs on one leg would have cancelled out
    (−3557 + 3547 ≈ −10) and the probe would have refused forever without ever
    saying why. Settle raised 10 → 30 s.

    **The probe storm, 2026-08-16.** Detection ran continuously for an hour and
    a half. Unit 093 answered `too_small` every time - 3500 W commanded, 144 W
    seen - and nothing remembered the failure, so the next tick started again.
    The owner's SoC graph is what explained it: 5 % to 48 % over exactly that
    period. The probing *was* charging the pack, and the fixed 30 s settle was
    not enough for it to come down, so every baseline already contained its own
    3500 W and every delta was ~0. A loop that fed itself.

    Two fixes, and both were needed. A failed probe now waits half an hour and
    gives up after three tries - an unplaced pack is held to the tightest leg,
    which is safe, whereas a coordinator that never coordinates is not. And the
    settle is no longer a count: the legs are watched until two readings agree,
    with a floor of 30 s (gotcha 2) and a cap of 120 s so a busy house cannot
    postpone it for ever.

    Still open, and it needs hardware:
    - **`phase_voltage` is nominal.** The meter usually publishes real per-phase
      voltages; using them would make the amps honest. Not worth it until the
      rest is proven.
    - **Two packs on one leg has never been observed**, and it is the case the
      settle change was made for. Unverifiable at the primary site, where they
      are on L1 and L3.
    - **Cannot run during a shadow run** (it writes, dry run does not), so the
      phases have to be typed in by hand until go-live, or the first probe
      happens the moment it goes live - which is what happened here.

20. ~~**Not bouncing off the bottom of the pack**~~ - **done.** Reported from
    the primary site: the sun lifts a pack from 5 % to 6 % and it is discharged
    straight back to 5 %, over and over. The floor was one threshold, so it was
    both where discharging stops and where it starts again.

    A **latch**, not a raised floor - a pack coming down from full still empties
    all the way to its own limit, whereas raising the floor to 10 % would simply
    cost those points. Counted as a delta above the floor, so it composes with
    the SoC reserve and each pack's own limit. Charging is never blocked. It
    persists, because coming back at 6 % after a restart and resuming the dump
    is the whole failure. `POLICY_RECOVERING` keeps it apart from "empty" and
    from "holding the reserve" - three different silences that want three
    different answers. Default 5 points; 0 switches it off.

### G. Prices without a second integration

19. ~~**Fetch prices without a second integration**~~ - **done.** Asked for by
    the owner when the site moved to Frank Energie. The sensor route stays the
    default and stays supplier-agnostic; this is the other half, for a house
    where installing another custom integration is the obstacle.

    Structured as the owner proposed: **first which kind of source, then which
    supplier or which sensor**. `CONF_PRICE_SOURCE` is `none` / a supplier key /
    `entity`, and only the entity route gets a second screen - asking "which
    sensor" after "fetch it yourself" would be a form with one dead field on it.
    An entry made before the choice existed has a sensor and no source, which is
    exactly what `entity` means, so nothing needs migrating.

    `suppliers.py` keeps the HTTP call apart from the arithmetic: `frank_request`
    builds the GraphQL body, `parse_frank` reads the answer, and both are pure
    and tested without a network. The result is handed back as
    `{"prices": [...]}` - a shape `parse_forecast` already understood - so
    nothing downstream knows where it came from. That seam is what keeps this
    from becoming per-supplier support creeping through the whole integration.

    Uses the **all-in** price, not the exchange price. Ranking is unaffected
    (tax and markup are a fixed adder, VAT a fixed multiplier - a monotonic
    transform cannot reorder anything), but the number shown is then what is
    actually paid. That closes the open question under 9.

    Still `requirements: []` - the HTTP goes through Home Assistant's own
    `async_get_clientsession`.

    Open: the endpoint has only ever been called from tests. The first real
    fetch is the one to watch, and `prices_error` in the diagnostics is where it
    will say so.

### H. Done

- **Asymmetric gain, and the ceiling on tuning.** Asked for by the owner: one
  pack has to work on its own. Measuring that first established what is even
  available, on the site's own hour with a single pack — and it is not much.
  A loop that could see the next tick exports **nothing**; one that can only
  react exports **210 Wh** however it is tuned, because the packs answer
  10-30 s later. We were at 266. **Do not go looking for the other 210 Wh — it
  is arithmetic, not tuning.** A lag-compensating loop (subtract the
  in-flight correction, then raise Kp) was tried and came out level or worse;
  the dominant error is disturbance, not sluggishness.
  What did work: the two directions are not the same risk. Going out bets on
  packs that have not answered yet, which is what oscillates (gotcha 3);
  coming back cannot run away, since the far end of "less" is a pack at 0 W —
  and it is where export comes from, a pack still discharging into a load that
  has already gone. `kp_return` defaults to **2 × Kp**, a factor rather than a
  number so that raising Kp cannot silently flatten the asymmetry out again.
  Through the real coordinator: one pack 266 → 211 Wh, two packs 323 → 244 Wh,
  fewer heavy export ticks and a lower worst case, for 2-3 % on the mean.
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

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
| Return gain | 2 × Kp | how fast the command is wound back *down* (see below) |
| Interval | 15 s | control tick period |
| Min output | 150 W | below this a unit is idled (avoids micro-cycling) |
| Max per unit | 3500 W | hard ceiling per unit |

### Why the two gains differ

Going out on a command and coming back from one are not the same risk. Every
step *up* is a bet on packs that answer 10–30 s later, which is what makes a
high gain oscillate. Coming back down cannot run away — the far end of "less"
is a pack sitting at 0 W.

It is also where the exports come from: a pack still discharging into a load
that has already gone away. Measured by replaying one site's own hour through
the real loop:

| | exported | ticks below −300 W | mean deviation |
| --- | --- | --- | --- |
| symmetric (both 0.25) | 323 Wh | 58 | 833 W |
| return gain 0.5 | **244 Wh** | **44** | 861 W |
| best possible without seeing the future | 210 Wh | 51 | 718 W |

A quarter less export for 3 % on the mean, and within reach of the arithmetic
limit for any loop that only reacts. It is a factor rather than a fixed number
so that raising Kp cannot silently flatten the asymmetry back out; set it equal
to Kp for the old symmetric behaviour.

## The trace file

Every tick is written to `config/battery_management_trace/YYYY-MM-DD.csv`, one
row per tick, kept for 14 days. The diagnostics download only holds the last
four hours and dies with the process — which is no use at all when the question
is "why did the packs do that on Saturday".

Get at it with the **File editor**, **Samba share** or **Terminal** add-on, or
`ha` over SSH. Every column is flat, so `pandas.read_csv` or a spreadsheet can
plot any of it without unpacking anything.

What each row carries:

| column | meaning |
| --- | --- |
| `grid_w` | the meter, as used (reconstructed in a shadow run) |
| `observed_grid_w` | what the meter actually said |
| `error_w`, `sp_before_w`, `sp_wanted_w`, `setpoint_w` | the whole integrator step |
| `sp_reason` | `integrate` · `deadband` · `clamped_upper` · `clamped_lower` · `dynamic_buy` · `external_plan` |
| `gain` | which of the two gains was applied |
| `upper_w`, `lower_w` | the bounds in force |
| `free_discharge_w`, `fuse_discharge_w` | what the packs could give, and what the fuse left of it |
| `phase1_w`…`phase3_w` | each leg |
| `<pack>_target_w` | what we told that pack — **signed: + discharging, − charging** |
| `schema` | the row format; `2` signed the column above, `1` did not |
| `<pack>_actual_w` | what the pack says it is doing — **the lag, measured** |
| `grid_age_s` | how old the meter reading was when we regulated on it |
| `<pack>_readback_w` | what the device holds as its target |
| `<pack>_ack_s` | seconds between commanding it and the device showing it |
| `<pack>_actual_age_s`, `<pack>_soc_age_s` | how stale that pack's readings were |
| `<pack>_cap_w`, `<pack>_soc`, `<pack>_phase`, `<pack>_recovering` | its ceiling and state |
| `mode`, `policy`, `status`, `dry_run`, `offline` | context |

Rows with `event=phase_probe` record a phase measurement: the deltas per leg,
the winning leg, the runner-up and the verdict.

`<pack>_actual_w` needs the optional **Power sensor** on each unit (the wizard
finds `..._ac_output` by itself). Without it the column is empty and the trace
cannot show whether a pack followed its orders — which is usually the question.

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

## The two cards, and where to find them

The integration ships two Lovelace cards and registers them itself, so there is
no manual resource step:

| Card | What it is for |
| --- | --- |
| **Battery Management Card** | The control panel: on/off, fast charge, state of charge per pack, and the price chart underneath. |
| **Battery Management Prices** | Only the prices — current price large, today's bars, and the cheapest and dearest hour with their times. |

### Adding one

Edit the dashboard → **+ Add card** → the **By card** tab → type `battery`.

That tab is the one that matters, and it is worth saying why: **By entity**
builds a standard card around an entity you pick, and a custom card is not an
entity — it can never appear there, however hard you look. The **By card** tab
may also open on *Suggestions* for whatever was selected; either search in it,
or use the **"Can't find the card you want? → Browse all cards"** link at the
bottom, where custom cards are grouped at the end.

Pick either card and press **Save**. They fill themselves in: the switches, the
packs, the meter and the price chart are all found from the entities that exist,
so there is no YAML to write. Both can sit on the same dashboard.

### If the card is not in that list

Almost always a stale script. The browser caches the card file hard, so an
update can appear to do nothing — the old script keeps running, and a card
added in a later release is simply not in it.

1. **Check the version** in HACS. Cards appeared in `0.8.0`; the cache fix that
   makes updates take effect at all landed in `0.8.3`. Below that, one manual
   hard refresh (Ctrl+Shift+R) is needed after each update.
2. **Check the file is served.** Open
   `http://<your-ha>:8123/battery_management/battery-management-card.js`
   directly. You should get JavaScript; search it for `prices-card` to confirm
   it is the current one. A 404 means the static path was not registered — the
   log will say so, and the fallback is to add that URL by hand under
   *Settings → Dashboards → Resources*.
3. **Restart Home Assistant**, not just reload the integration. The card is
   registered during setup.
4. **Check for a hand-added copy** under *Settings → Dashboards → Resources*.
   The integration registers the script itself, so a manual entry pointing at
   the same file loads it a second time. That used to be fatal - the second
   copy died on a duplicate element definition before it reached the second
   card, so the card list showed a stale entry and the prices card was simply
   missing. Harmless from `0.9.1`, but the manual entry is still redundant.

**From `0.11.2` the server answers this itself.** Download the diagnostics and
read the `card` block: it says where the file was looked for, how many bytes
were served, which URL was offered to the frontend, and what failed if
anything did. `"error": "file_missing"` means HACS did not copy the script;
`"error": "static_path: …"` means it is not being served; a complete block with
no error means the file is fine and the browser is holding an old copy, so
hard-refresh. The same appears in the log at startup.

Before `0.11.4` the card was registered during setup, which on a cold boot can
run *before* the frontend exists — and a card offered to a frontend that has
not initialised is simply lost. If cards worked after reloading the integration
but not after a reboot, that was why. It now waits for the frontend instead.

Deliberately **not** by making the frontend a hard dependency: that was tried
in `0.11.2` and is the worse trade, because the batteries then stop being
coordinated at all if the frontend fails to start. A battery controller has no
business depending on a web interface.

From `0.8.3` the script URL carries the version, so an update busts the cache on
its own and none of this should be needed again.

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

## Where the prices come from

Dynamic mode needs to know what electricity costs. There are two ways to tell
it, chosen under *Configure → Dynamic tariff*.

**A sensor from another integration** is the original route and still the
default. Nord Pool, ENTSO-e, EnergyZero, Tibber, Zonneplan — anything that
publishes a list of upcoming prices in its attributes works, because
`prices.py` recognises the *shapes* rather than the integrations. Changing
supplier means pointing this at a different entity. A shape it does not
recognise gives an **empty** forecast, which disables buying rather than
guessing at a price.

**Fetched directly** is for suppliers who publish their prices openly. Today
that is **Frank Energie**, whose market-price API needs no account. It exists
because "first install another custom integration" is a real obstacle at a
house you do not live in, and this is meant to be maintained centrally across
several of them.

The direct route uses the **all-in** price — market price plus VAT, sourcing
markup and energy tax — not the bare exchange price. That matters less than it
sounds for *deciding*: tax and markup are a fixed adder and VAT a fixed
multiplier, so the transform is monotonic and the cheap-to-expensive ranking is
identical either way. It matters for *reading*: the number on the Plan sensor is
then what you actually pay.

Prices are re-fetched hourly, which is about noticing that tomorrow has been
published rather than tracking anything. A supplier that cannot be reached is
not an error state — no forecast disables cheap-hour charging and leaves
grid-zero regulating exactly as it does without a dynamic contract. The previous
answer is kept, because prices do not change retroactively and old slots fall
out of the ranking window by themselves. `prices_error` and `prices_fetched_at`
in the diagnostics say which of those is happening.

### The price chart

The chart lives on the card that ships with the integration, not on the device
page — a device page lists entities, and a chart is not one.

Added the same way as the other card — see **The two cards, and where to find
them** above.

It draws **the whole day** — one bar per hour, from midnight, coloured by the
decision that hour belongs to. Hours that have already gone are drawn faint and
carry no colour of their own: the ranking looks forward, so calling a past hour
"cheap" would be inventing a decision that was never made. Green
is **not** "a low number" — it is the hours the coordinator will actually buy
on, and red the hours it is keeping the charge for. Those are computed by the
integration, so the chart cannot draw a different plan than the one being
executed; a card picking its own threshold could. Everything else stays grey:
following the meter, no price decision involved.

The two hues clear a colourblind-separation check on both a light and a dark
card (CVD ΔE 9.7, contrast ≥ 3:1 on both), but colour never carries the meaning
on its own — every bar names its role in its tooltip and the legend spells all
three out. The current hour is marked by an outline rather than another colour, and
**tapping any bar** reads out that hour instead — price, time span and what the
coordinator intends there. Tap it again to hand the readout back to the clock.
A tapped bar gets a dashed outline so it is never mistaken for the current one.
The hover tooltip is still there for a mouse, but a tap is what works on a
phone, which is where a price chart actually gets read.
Negative prices hang below the zero line instead of being clipped, because on a
dynamic tariff they are real.

To place it by hand instead, the only line the chart needs is `prices:`, and it
points at the **Plan** sensor rather than a price sensor — that is where the
whole series lives, each hour carrying its role:

```yaml
type: custom:battery-management-card
prices: sensor.battery_management_plan
```

### Quarter-hourly prices

The market settles in 15-minute blocks and suppliers are starting to publish
that way. Nothing in the ranking ever assumed hours — `cheap_hours` is a
duration, converted into however many slots the feed happens to use — so a
quarter-hourly feed works with no change: ask for 3 cheap hours and it picks
the 12 cheapest quarters.

*How to read the prices* under **Dynamic tariff** chooses between the two:

- **As the supplier publishes them** (the default) is the more precise: the
  coordinator will buy on a single cheap quarter.
- **By the hour** folds them, weighted by duration. 24 bars on a chart instead
  of 96, at the cost of averaging away the peaks inside each hour.

Whichever you pick applies to the decisions *and* the chart, because they must
never be able to disagree about what "cheap" meant.

### A prices-only card

For a dashboard that just wants to know whether to run the dishwasher, there is
a second card: **Battery Management Prices**.
Current price large at the top, the same chart underneath, and the cheapest and
dearest hour of the day with the times they fall.

### Wiring it into the Energy dashboard

*Settings → Dashboards → Energy → Grid consumption → **Use an entity with
current price*** and pick **Current price**. That is the all-in price, which is
what import is billed at.

**Export is a different number.** Energy tax and VAT are not paid back to you,
so pointing export compensation at the same entity overstates what you earn —
and a wrong figure on an energy dashboard looks exactly like a right one. Use
**Market price** instead, which is the exchange component on its own.

What your supplier actually pays back is calculated *from* that, with their own
fee or deduction on top. This integration does not know your contract and does
not guess at it: if Frank deducts a fixed amount per kWh, a static rate or a
template sensor built on Market price will be closer than either raw number.

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
| **Dry run** | Decide everything, command nothing. On by default. Blocks every write including the safe revert, so it cannot fight another controller. The suppressed-command counter on this switch is its proof of life: a shadow run that suppressed nothing is a broken one. Switching it back **on** hands the packs back first — otherwise they would hold the last live command indefinitely. |

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
| **Market price** | The exchange component of this hour, without tax or markup. For the Energy dashboard's *export compensation* — see below. | not on the direct route |
| **Current price** | What this hour costs, in EUR/kWh. Its attributes say which decision the hour belongs to, when it changes, and what the next one is. | no prices available |
| **Plan** | Today's cheap and dear hours with their prices, plus the numbers the ceiling was computed from, all in attributes. Its `hours` attribute is the whole series, each slot carrying the `role` it belongs to — `cheap`, `dear` or `normal` — which is what the card's chart is drawn from. | never |
| **Fuse headroom** | Amps still available on **the busiest single leg** — not a total, and not per leg. It is the one that would trip first; `tightest_phase` in the attributes says which. Measured against the usable limit (the fuse less your margin), so the margin is still there underneath. Per-leg detail — `amps` through the fuse, `amps_without_us`, headroom, and which packs sit on it — is in the attributes. | no per-phase sensors configured |
| **Phase detection** | Whether it knows which pack is on which leg, and how it found out. The `probes` attribute holds the measurements behind each placement. | never |
| **‹unit› phase** | Which leg that pack is on — `L1`, `L2` or `L3`. Its `source` attribute says whether that was measured or typed in. | no per-phase sensors configured |

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

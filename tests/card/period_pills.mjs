// Choosing which length of the charge split to look at.
//
// Asked for by the owner on 2026-08-20: day, week or month, and the month by
// default. Three pairs of sensors, one accumulation behind them - so what this
// file checks is that the card asks the right pair, and that the default is
// the month rather than whichever key happens to sort first.
import { readFileSync } from "node:fs";
process.env.TZ = "Europe/Amsterdam";
const src = readFileSync(
  "custom_components/battery_management/www/battery-management-card.js",
  "utf8"
);
globalThis.window = globalThis;
const _defined = new Map();
globalThis.customElements = {
  define: (tag, cls) => _defined.set(tag, cls),
  get: (tag) => _defined.get(tag),
};
globalThis.HTMLElement = class {};
globalThis.console.info = () => {};

let fails = 0;
const check = (name, cond, got) => {
  if (!cond) { console.log("FAIL", name, JSON.stringify(got)); fails++; }
  else console.log("ok  ", name);
};

class FakeEl {
  constructor(period) {
    this.listeners = {};
    this.style = {};
    this.innerHTML = "";
    this.textContent = "";
    this.dataset = period ? { period } : {};
    this.on = false;
    this.classList = {
      toggle: (name, value) => { if (name === "on") this.on = value; },
    };
  }
  addEventListener(type, fn) {
    (this.listeners[type] = this.listeners[type] || []).push(fn);
  }
  closest() { return this; }
}
globalThis.document = { createElement: () => new FakeEl() };

const { CHARGE_SUFFIX, DEFAULT_PERIOD } = new Function(
  src + ";return {CHARGE_SUFFIX, DEFAULT_PERIOD};"
)();
const Manage = _defined.get("battery-management-card");

const SP = "sensor.bm_setpoint";
const PILLS = ["day", "week", "month"].map((p) => new FakeEl(p));

// one distinct pair of readings per period, so "which pair did it read" is
// answerable from the rendered numbers alone
const STATES = {
  [SP]: { state: "0", attributes: {} },
  "sensor.bm_charged_today": { state: "1", attributes: {} },
  "sensor.bm_charged_from_grid_today": { state: "0.25", attributes: {} },
  "sensor.bm_charged_this_week": { state: "10", attributes: {} },
  "sensor.bm_charged_from_grid_this_week": { state: "2.5", attributes: {} },
  "sensor.bm_charged_this_month": { state: "100", attributes: {} },
  "sensor.bm_charged_from_grid_this_month": { state: "25", attributes: {} },
};

function build(config) {
  const els = new Map();
  const card = new Manage();
  card.querySelector = (sel) => {
    const key = String(sel).replace(/^#/, "");
    if (!els.has(key)) els.set(key, new FakeEl());
    return els.get(key);
  };
  card.querySelectorAll = () => PILLS;
  card.setConfig({ setpoint: SP, ...config });
  card._els = els;
  card.hass = { states: STATES, entities: {}, callService: () => {} };
  return card;
}

const read = (card) => card._els.get("ctotal").textContent;

// --- the default ----------------------------------------------------------
check("the default period is the month", DEFAULT_PERIOD === "month", DEFAULT_PERIOD);
check("all three periods have a suffix pair",
  Object.keys(CHARGE_SUFFIX).sort().join() === "day,month,week" &&
    Object.values(CHARGE_SUFFIX).every((pair) => pair.length === 2),
  CHARGE_SUFFIX);
check("no suffix is reused between periods",
  new Set(Object.values(CHARGE_SUFFIX).flat()).size === 6,
  Object.values(CHARGE_SUFFIX).flat());

const fresh = build({});
check("a card with no setting opens on the month", read(fresh) === "100.0 kWh",
  read(fresh));
check("and the month pill is the lit one",
  PILLS.find((p) => p.on).dataset.period === "month",
  PILLS.map((p) => [p.dataset.period, p.on]));

// --- switching ------------------------------------------------------------
const card = build({});
const tap = (period) => {
  const group = card._els.get("cper");
  const pill = PILLS.find((p) => p.dataset.period === period);
  group.listeners.click[0]({ target: pill });
};

tap("day");
check("tapping Dag reads the day pair", read(card) === "1.0 kWh", read(card));
check("and lights the day pill",
  PILLS.find((p) => p.on).dataset.period === "day",
  PILLS.map((p) => [p.dataset.period, p.on]));

tap("week");
check("tapping Week reads the week pair", read(card) === "10.0 kWh", read(card));

tap("month");
check("and back to Maand reads the month pair", read(card) === "100.0 kWh",
  read(card));

// --- the opening period is configurable -----------------------------------
check("charge_period opens on that one",
  read(build({ charge_period: "week" })) === "10.0 kWh",
  read(build({ charge_period: "week" })));
check("a nonsense charge_period falls back to the month rather than breaking",
  read(build({ charge_period: "fortnight" })) === "100.0 kWh",
  read(build({ charge_period: "fortnight" })));

// --- the sun share follows the period -------------------------------------
const sunny = build({ charge_period: "day" });
check("the sun half is the remainder of the period on show",
  sunny._els.get("csunt").textContent.includes("0.75"),
  sunny._els.get("csunt").textContent);

// --- the explicit config keys pin the month, not every period -------------
//
// `charged_total` / `charged_grid` are what getStubConfig writes and what
// configs made before this carried. Honouring them for every period would
// quietly show the month under all three labels - which reads as the switch
// being broken rather than as the config winning.
const pinned = build({
  charged_total: "sensor.bm_charged_this_month",
  charged_grid: "sensor.bm_charged_from_grid_this_month",
});
check("an explicit month config still shows the month", read(pinned) === "100.0 kWh",
  read(pinned));
const pinnedThenDay = build({
  charged_total: "sensor.bm_charged_this_month",
  charged_grid: "sensor.bm_charged_from_grid_this_month",
});
{
  const group = pinnedThenDay._els.get("cper");
  group.listeners.click[0]({ target: PILLS.find((p) => p.dataset.period === "day") });
}
check("but switching to Dag still shows the day",
  read(pinnedThenDay) === "1.0 kWh", read(pinnedThenDay));

process.exit(fails ? 1 : 0);

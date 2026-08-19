// The mode as a control, the policy as an explanation, and the charge split.
//
// Three things the card gained together, all of which fail quietly rather than
// loudly: a dropdown that never fires the service still looks like a dropdown,
// a policy line reading a raw key still reads as text, and a split bar with the
// wrong arithmetic still draws a bar. So each is asserted on what it *does*.
import { readFileSync } from "node:fs";
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
  constructor() {
    this.listeners = {};
    this.style = {};
    this.innerHTML = "";
    this.textContent = "";
    this.value = "";
    this.dataset = {};
    this.classList = { toggle: () => {} };
  }
  addEventListener(type, fn) {
    (this.listeners[type] = this.listeners[type] || []).push(fn);
  }
  appendChild() {}
}
globalThis.document = { createElement: () => new FakeEl() };

const [Manage] = new Function(
  src + ";return [BatteryManagementCard, BatteryManagementPricesCard];"
)();

const MODE = "select.bm_mode";
const POLICY = "sensor.bm_active_policy";
const TOTAL = "sensor.accu_geladen_totaal";
const GRID = "sensor.accu_geladen_uit_net";
const CONFIG = { mode: MODE, policy: POLICY, charged_total: TOTAL, charged_grid: GRID };

// what Home Assistant's own resolver returns, which is where the translations
// actually live - the card must not carry a second copy of them
const DUTCH = {
  grid_zero: "Volg de meter",
  pause: "Pauze",
  dynamic: "Dynamisch tarief",
  packs_empty: "Accu's leeg",
};

/** A card whose querySelector hands back a stub per selector. */
function build(config, states) {
  const els = new Map();
  const card = new Manage();
  card.querySelector = (sel) => {
    const key = String(sel).replace(/^#/, "");
    if (!els.has(key)) els.set(key, new FakeEl());
    return els.get(key);
  };
  card.setConfig(config);
  card._els = els;
  card.calls = [];
  card.hass = {
    states,
    callService: (domain, service, data) => card.calls.push([domain, service, data]),
    formatEntityState: (st, value) => DUTCH[value === undefined ? st.state : value],
  };
  return card;
}

const el = (card, id) => card._els.get(id);

const statesWith = (mode, options, policy, total, grid) => ({
  [MODE]: { state: mode, attributes: { options } },
  [POLICY]: { state: policy, attributes: {} },
  [TOTAL]: { state: String(total), attributes: {} },
  [GRID]: { state: String(grid), attributes: {} },
});

const options = ["grid_zero", "pause", "dynamic"];
const bare = build({}, {});

// ---------------------------------------------------------------- the mode
let card = build(CONFIG, statesWith("pause", options, "packs_empty", 12.5, 2.5));

check("the mode row is shown", el(card, "moderow").style.display === "flex",
  el(card, "moderow").style);
check("every option the entity offers is drawn",
  options.every((o) => el(card, "mode").innerHTML.includes('value="' + o + '"')),
  el(card, "mode").innerHTML);
check("options carry Home Assistant's own labels",
  el(card, "mode").innerHTML.includes("Volg de meter") &&
  el(card, "mode").innerHTML.includes("Dynamisch tarief"),
  el(card, "mode").innerHTML);
check("the dropdown shows the mode it is in", el(card, "mode").value === "pause",
  el(card, "mode").value);

// changing it has to reach the coordinator, not merely move the widget
const sel = el(card, "mode");
sel.value = "dynamic";
sel.listeners.change[0]({ target: sel });
check("changing it calls select_option",
  card.calls.length === 1 &&
  card.calls[0][0] === "select" && card.calls[0][1] === "select_option" &&
  card.calls[0][2].entity_id === MODE && card.calls[0][2].option === "dynamic",
  card.calls);

// The bug this guards: `_update` runs on every state change in the house. If it
// rebuilds the <select> each time, an open dropdown closes under the reader's
// finger - readable, but not changeable, exactly on the phone where it is used.
sel.innerHTML = "REBUILT";
card.hass = card._hass;
check("an ordinary update does not rebuild the options",
  sel.innerHTML === "REBUILT", sel.innerHTML);

// ... but a genuinely changed list must land. Dynamic only exists once a price
// source is configured, so the list is not a constant.
card._hass.states[MODE] = {
  state: "pause",
  attributes: { options: ["grid_zero", "pause"] },
};
card.hass = card._hass;
check("a changed option list is rebuilt",
  sel.innerHTML !== "REBUILT" && !sel.innerHTML.includes("dynamic"), sel.innerHTML);

check("no mode entity, no mode row", el(bare, "moderow").style.display === "none",
  el(bare, "moderow").style);

// -------------------------------------------------------------- the policy
card = build(CONFIG, statesWith("grid_zero", options, "packs_empty", 12.5, 2.5));
check("the policy is spelled out, not keyed",
  el(card, "policy").textContent === "Accu's leeg", el(card, "policy").textContent);
check("no policy entity, no policy row",
  el(bare, "policyrow").style.display === "none", el(bare, "policyrow").style);

// a policy added to the integration but not yet to the card must still read
const odd = build(CONFIG, statesWith("grid_zero", options, "brand_new_policy", 1, 0));
check("an untranslated policy falls back to the raw key, tidied",
  el(odd, "policy").textContent === "Brand new policy", el(odd, "policy").textContent);

// --------------------------------------------------------------- the split
check("the total is what went in", el(card, "ctotal").textContent === "12.5 kWh",
  el(card, "ctotal").textContent);
check("the sun is the remainder", el(card, "csunt").textContent === "zon 10.0 kWh",
  el(card, "csunt").textContent);
check("the meter half is its own", el(card, "cnett").textContent === "net 2.5 kWh",
  el(card, "cnett").textContent);
check("the bar is that same proportion",
  el(card, "csun").style.width === "80%" && el(card, "cnet").style.width === "20%",
  [el(card, "csun").style.width, el(card, "cnet").style.width]);

// Two meters that never quite agree can put the grid figure fractionally over
// the total. The split has to stay a split - a negative sun is not a number.
const drift = build(CONFIG, statesWith("grid_zero", options, "grid_zero", 4.0, 4.2));
check("drift cannot produce a negative sun",
  el(drift, "csunt").textContent === "zon 0.00 kWh" &&
  el(drift, "cnett").textContent === "net 4.0 kWh",
  [el(drift, "csunt").textContent, el(drift, "cnett").textContent]);

// One half alone says nothing about the share, and "0 % from the sun" is a far
// worse answer than no answer at all.
const half = build({ charged_grid: GRID }, { [GRID]: { state: "2.5", attributes: {} } });
check("one figure alone gets no split",
  el(half, "csunt").textContent === "" && el(half, "cnett").textContent === "net 2.5 kWh",
  [el(half, "csunt").textContent, el(half, "cnett").textContent]);
check("and draws no proportion either",
  el(half, "csun").style.width === "0%" && el(half, "cnet").style.width === "0%",
  [el(half, "csun").style.width, el(half, "cnet").style.width]);
check("neither sensor, no charge block",
  el(bare, "charge").style.display === "none", el(bare, "charge").style);

// A Riemann sum over a power sensor that dipped below zero can start out
// negative. The cap above only bounds the meter half, so this is the case that
// reaches the floor under the sun - without it, a negative total renders a
// negative sun, which is not a quantity of anything.
const backwards = build(CONFIG, statesWith("grid_zero", options, "grid_zero", -1.0, 0));
check("a negative total cannot render a negative sun",
  el(backwards, "csunt").textContent === "zon 0.00 kWh",
  el(backwards, "csunt").textContent);

// A counter switched on an hour ago reads 0.01 kWh. At one decimal that is
// "0.00" three times over, which reads as broken rather than as new - and new
// is what it is for everyone the first time they enable it.
const tiny = build(CONFIG, statesWith("grid_zero", options, "grid_zero", 0.01, 0.01));
check("a freshly started counter is not rounded away",
  el(tiny, "ctotal").textContent === "0.01 kWh", el(tiny, "ctotal").textContent);

// a fresh install has charged nothing yet; 0 out of 0 is not 100 % sun
const fresh = build(CONFIG, statesWith("grid_zero", options, "grid_zero", 0, 0));
check("nothing charged yet claims no share",
  el(fresh, "csun").style.width === "0%", el(fresh, "csun").style.width);

// ---------------------------------------------- found without being told
// A card already on a dashboard keeps the config it was created with, so
// entities added in a later release would never appear on it - getStubConfig
// only runs when a card is *added*. Hand-editing the YAML of every dashboard
// on every site is exactly what this integration exists to avoid, so the card
// resolves its own entities off the setpoint sensor it was given.
const SP = "sensor.battery_management_setpoint";
const derived = build(
  { setpoint: SP },
  {
    [SP]: { state: "51", attributes: {} },
    "select.battery_management_mode": {
      state: "grid_zero",
      attributes: { options },
    },
    "sensor.battery_management_active_policy": {
      state: "packs_empty",
      attributes: {},
    },
    "sensor.battery_management_charged": { state: "12.5", attributes: {} },
    "sensor.battery_management_charged_from_grid": { state: "2.5", attributes: {} },
  }
);

check("the mode is found from the setpoint's own prefix",
  el(derived, "moderow").style.display === "flex" &&
  el(derived, "mode").value === "grid_zero", el(derived, "mode").value);
check("so is the policy",
  el(derived, "policy").textContent === "Accu's leeg", el(derived, "policy").textContent);
check("and so is the charge split",
  el(derived, "ctotal").textContent === "12.5 kWh" &&
  el(derived, "csunt").textContent === "zon 10.0 kWh",
  [el(derived, "ctotal").textContent, el(derived, "csunt").textContent]);

// changing the mode must reach the derived entity, not nothing at all
const dsel = el(derived, "mode");
dsel.value = "pause";
dsel.listeners.change[0]({ target: dsel });
check("changing a derived mode still calls the service",
  derived.calls.length === 1 &&
  derived.calls[0][2].entity_id === "select.battery_management_mode",
  derived.calls);

// an entity that does not exist must not be named - a site without the charge
// sensors gets a card without the split, not a split of nothing
const sparse = build({ setpoint: SP }, { [SP]: { state: "51", attributes: {} } });
check("nothing is invented for a site that has none",
  el(sparse, "csunt").textContent === "" && el(sparse, "cnett").textContent === "" &&
  el(sparse, "moderow").style.display === "none",
  [el(sparse, "csunt").textContent, el(sparse, "moderow").style.display]);

// Silently hiding is what cost three rounds of "I still do not see it": a
// block that vanishes when it cannot find its entities looks exactly like a
// broken card, and naming the id it looked for turns the next question into
// a one-line answer.
check("it says what it looked for instead of vanishing",
  el(sparse, "charge").style.display === "block" &&
  el(sparse, "cnote").textContent.includes("sensor.battery_management_charged"),
  el(sparse, "cnote").textContent);
check("and the bar is not drawn on nothing",
  el(sparse, "cbar").style.display === "none", el(sparse, "cbar").style.display);

// an explicit setting still wins over the derived one
const pinned = build(
  { setpoint: SP, charged_total: "sensor.elders", charged_grid: GRID },
  {
    [SP]: { state: "51", attributes: {} },
    "sensor.elders": { state: "8", attributes: {} },
    [GRID]: { state: "2", attributes: {} },
    "sensor.battery_management_charged": { state: "999", attributes: {} },
  }
);
check("an explicit entity is not overridden",
  el(pinned, "ctotal").textContent === "8.0 kWh", el(pinned, "ctotal").textContent);

// Home Assistant appends _2 when the id it wants is taken, and an entity that
// predates a device rename keeps its old slug. Guessing the exact slug is what
// put "not found" on a dashboard whose counters were working perfectly.
const suffixed = build(
  { setpoint: SP },
  {
    [SP]: { state: "51", attributes: {} },
    "sensor.battery_management_charged_2": { state: "12.5", attributes: {} },
    "sensor.battery_management_charged_from_grid_2": { state: "2.5", attributes: {} },
  }
);
check("a _2 suffix is still recognised as ours",
  el(suffixed, "ctotal").textContent === "12.5 kWh" &&
  el(suffixed, "csunt").textContent === "zon 10.0 kWh",
  [el(suffixed, "ctotal").textContent, el(suffixed, "csunt").textContent]);

// "..._charged" must not swallow "..._charged_from_grid", or the total and the
// grid half become the same number and the sun silently reads zero
check("the two counters are not confused for each other",
  el(suffixed, "cnett").textContent === "net 2.5 kWh", el(suffixed, "cnett").textContent);

// found but with nothing in it is a different fault from not found at all, and
// they need different answers - one is a setting, the other is a bug here
const empty = build(
  { setpoint: SP },
  {
    [SP]: { state: "51", attributes: {} },
    "sensor.battery_management_charged": { state: "unknown", attributes: {} },
  }
);
check("an unreadable counter is named, not called missing",
  el(empty, "cnote").textContent.includes("heeft nog geen waarde"),
  el(empty, "cnote").textContent);

console.log(fails ? "\n" + fails + " FAILED" : "\nmode / policy / charge checks pass");
process.exit(fails ? 1 : 0);

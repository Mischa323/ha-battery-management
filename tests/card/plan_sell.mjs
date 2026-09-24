// The plan card's "wanneer verkopen": when slim handelen will sell today.
//
// Asked for by the owner: "bij plan van vandaag erbij zetten wanneer hij gaat
// ontladen voor slim handelen als je dat hebt aanstaan". So it appears only
// with trading on or in shadow, it says "zou" in shadow (nothing is sold), a
// quarter-hourly peak reads as one row rather than eight, and an empty list
// says why - including the one thing no price list knows: the packs have to be
// above the sell floor when the hour comes.
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

const { sellRowSays, sellRuns } = new Function(src + ";return {sellRowSays, sellRuns};")();
const Plan = _defined.get("battery-management-plan-card");

const nodes = new Map();
function render(planAttrs) {
  nodes.clear();
  const card = new Plan();
  Object.defineProperty(card, "innerHTML", {
    set(html) { this._html = html; }, get() { return this._html || ""; }, configurable: true,
  });
  card.querySelector = (sel) => {
    const id = sel.replace("#", "");
    if (!nodes.has(id)) nodes.set(id, { textContent: "", innerHTML: "" });
    return nodes.get(id);
  };
  card.setConfig({ plan: "sensor.bm_plan" });
  card.hass = { states: { "sensor.bm_plan": { state: "ok", attributes: planAttrs } } };
  return (nodes.get("plsell") || {}).innerHTML || "";
}

// today at a fixed local evening, so the slots are always "today"
const base = new Date();
base.setHours(18, 0, 0, 0);
const q = 15 * 60000;
const now = Date.now();
const slot = (i, extra = {}) => {
  const start = new Date(base.getTime() + i * q);
  const end = new Date(start.getTime() + q);
  return {
    start: start.toISOString(), end: end.toISOString(), price: 0.4,
    past: end.getTime() <= now, role: "dear", buy: false, bought: false,
    sell: false, sold: false, sell_margin: null, ...extra,
  };
};
const future = (i, extra) => ({ ...slot(i, extra), past: false });

const TRADE = { mode: "on", sell_floor: 70, min_margin_eur_kwh: 0.2, above_floor_kwh: 8.4, sell_hours: [] };
const plan = (hours, trade = TRADE, mode = "dynamic") => ({
  mode, has_prices: true, cheap_hours: [], expected: { known: false }, hours, trade,
});

// --- shown only when trading is on ---------------------------------------
check("nothing at all with trading off",
  render(plan([future(0, { sell: true, sell_margin: 0.3 })], { ...TRADE, mode: "off" })) === "",
  "off");
check("nothing from an older integration without the field",
  render(plan([future(0)], null)) === "", "no trade");

// --- the rows -------------------------------------------------------------
{
  const html = render(plan([
    future(0, { sell: true, sell_margin: 0.21 }),
    future(1, { sell: true, sell_margin: 0.32 }),
    future(2, { sell: true, sell_margin: 0.25 }),
    future(3, { sell: true, sell_margin: 0.22 }),
    future(4),
    future(6, { sell: true, sell_margin: 0.2 }),
  ]));
  check("a run of quarters reads as one row, and a gap starts another",
    (html.match(/class="hr"/g) || []).length === 2, html);
  check("the run shows its best margin", html.includes("+€0,320/kWh"), html);
  check("says it will sell", html.includes("gaat verkopen") && html.includes("Wanneer verkopen<"), html);
  check("and on what condition, with how much there is",
    html.includes("boven 70 % zitten") && html.includes("8.4 kWh daarboven"), html);
}

// --- shadow says "zou" ------------------------------------------------------
{
  const html = render(plan([future(0, { sell: true, sell_margin: 0.3 })], { ...TRADE, mode: "shadow" }));
  check("shadow says it would, in the title and the row",
    html.includes("Wanneer verkopen (schaduw)") && html.includes("zou verkopen") &&
    !html.includes("gaat verkopen"), html);
  check("and that it commands nothing", html.includes("stuurt niets aan"), html);
}

// --- what happened ----------------------------------------------------------
check("a past sale is a fact",
  sellRowSays({ past: true, sold: true }, "on").text === "verkocht" &&
  sellRowSays({ past: true, sold: true }, "on").tone === "done",
  sellRowSays({ past: true, sold: true }, "on"));
check("in shadow, a would-have",
  sellRowSays({ past: true, sold: true }, "shadow").text === "zou verkocht hebben",
  sellRowSays({ past: true, sold: true }, "shadow"));
check("the current slot, selling",
  sellRowSays({ past: false, sold: true, sell: true }, "on").text === "verkoopt nu",
  sellRowSays({ past: false, sold: true }, "on"));
{
  const html = render(plan([
    { ...slot(0, { sold: true }), past: true },
    { ...slot(1, { sell: true }), past: true },   // earmarked but gone unsold: not listed
    future(3, { sell: true, sell_margin: 0.3 }),  // a gap, so nothing merges it away
  ]));
  check("a past slot is listed only if it sold",
    (html.match(/class="hr"/g) || []).length === 2 && html.includes("verkocht"), html);
}
check("runs merge only on the same words",
  sellRuns([{ ...future(0), sold: true }, future(1, { sell: true })], "on").length === 2,
  "mixed");

// --- empty ----------------------------------------------------------------
check("an empty day names the threshold",
  render(plan([future(0)])).includes("minstens €0,20 per kWh"), render(plan([future(0)])));
check("outside Dynamic it says it only sells there",
  render(plan([future(0)], TRADE, "grid_zero")).includes("alleen in de modus Dynamisch"),
  render(plan([future(0)], TRADE, "grid_zero")));
check("without a capacity it still names the floor",
  render(plan([future(0, { sell: true })], { ...TRADE, above_floor_kwh: null }))
    .includes("boven 70 % zitten."),
  "no capacity");

console.log(fails ? `\n${fails} FAILED` : "\nplan sell checks pass");
process.exit(fails ? 1 : 0);

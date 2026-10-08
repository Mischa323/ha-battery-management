// The plan card says why it fills past what the house needs.
//
// Asked for on 8 October: the packs stopped at 70 %, the sell line, and the
// evening's sale had nothing to sell. With trading on it now fills for the
// sale when that pays, and the card has to say so - "laadt vol" with no reason
// reads like a ceiling gone wrong.
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

const { fillSays } = new Function(src + ";return {fillSays};")();
const Plan = _defined.get("battery-management-plan-card");

const nodes = new Map();
function why(planAttrs) {
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
  return (nodes.get("plwhy") || {}).textContent || "";
}

const EXPECTED = {
  known: true, grid_kwh: 8.4, solar_kwh: 0, room_for_solar_kwh: 0,
  solar_remaining_kwh: 0, solar_expected_kwh: 0, ceiling: 100,
};
const TRADE = { mode: "on", sell_floor: 70, min_margin_eur_kwh: 0.05, above_floor_kwh: 0, sell_hours: [] };
const plan = (trade) => ({
  mode: "dynamic", has_prices: true, cheap_hours: [], hours: [], expected: EXPECTED, trade,
});

check("filling for the sale says so, and how full",
  why(plan({ ...TRADE, fill_to: 100 })).includes("Laadt bij tot 100 % om vanavond te verkopen"),
  why(plan({ ...TRADE, fill_to: 100 })));
check("nothing extra when it is not filling for a sale",
  !why(plan({ ...TRADE, fill_to: null })).includes("verkopen"),
  why(plan({ ...TRADE, fill_to: null })));
check("nor from an older integration without the field",
  fillSays(TRADE) === "" && fillSays(null) === "", [fillSays(TRADE), fillSays(null)]);
check("rounds the percentage", fillSays({ fill_to: 94.6 }).includes("tot 95 %"), fillSays({ fill_to: 94.6 }));

if (fails) { console.log(fails + " failed"); process.exit(1); }

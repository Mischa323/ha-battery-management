// The plan card says why it buys less than the ceiling has room for.
//
// Reported on 9 October: "Laadt tot 89 %" and 19.7 kWh from the grid, with the
// packs at 18 % and one quarter planned - a flat day, where only the quarters
// that beat the dear hours by the margin are worth buying on. The tile now
// shows what the plan will buy, and the sentence says why not more, and which
// setting the margin is.
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

new Function(src)();
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
  const text = (id) => (nodes.get(id) || {}).textContent || "";
  return { net: text("plnet"), why: text("plwhy") };
}

const EXPECTED = {
  known: true, grid_kwh: 19.7, solar_kwh: 3.0, room_for_solar_kwh: 3.0,
  solar_remaining_kwh: 6.0, solar_expected_kwh: 3.0, ceiling: 89,
};
const plan = (expected, margin = 0.05) => ({
  mode: "dynamic", has_prices: true, cheap_hours: [], hours: [], expected, price_margin: margin,
});

{
  const out = render(plan({ ...EXPECTED, grid_planned_kwh: 1.75 }));
  check("the tile shows what the plan will buy, not the room", out.net === "1.8 kWh", out.net);
  check("the sentence says only that much pays, and why",
    out.why.startsWith("Mag laden tot 89 %, maar vandaag loont maar 1.8 kWh van het net") &&
    out.why.includes("minstens €0,05 per kWh goedkoper") &&
    out.why.includes("12 % verlies plus slijtage"), out.why);
  check("and where the margin is set",
    out.why.includes("Minimale besparing, in te stellen onder Configureren → Dynamisch tarief"), out.why);
}
{
  const out = render(plan({ ...EXPECTED, grid_planned_kwh: 0 }, 0.08));
  check("nothing that pays says so", out.net === "0.00 kWh" &&
    out.why.includes("vandaag loont laden van het net niet: in geen enkel kwartier") &&
    out.why.includes("€0,08"), out);
}
{
  const out = render(plan({ ...EXPECTED, grid_planned_kwh: 19.7 }));
  check("when it all pays, the ceiling sentence as before",
    out.net === "19.7 kWh" && out.why.startsWith("Laadt tot 89 %"), out);
}
{
  const out = render(plan({ ...EXPECTED }));
  check("an older integration without the field shows the room, as before",
    out.net === "19.7 kWh" && out.why.startsWith("Laadt tot 89 %"), out);
}

if (fails) { console.log(fails + " failed"); process.exit(1); }

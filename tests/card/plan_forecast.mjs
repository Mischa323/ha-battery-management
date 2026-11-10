// The plan card says what is kept for the peak; the chart is not on it.
//
// Asked for on 1 November, after the packs met the EUR 0.49 peak empty with
// nothing on the card to say they would: the sentence says which hours are
// kept for it. The line itself moved to its own card on 10 November - "deze
// had gescheiden moeten worden in twee losse kaarten" - see forecast_card.mjs.
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

// "now" is where the example data starts, whatever day the tests run on
Date.now = () => Date.parse("2026-11-01T00:05:00+01:00");
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
  return { html: card._html, soc: (nodes.get("plsoc") || {}).innerHTML || "", hold: nodes.get("plhold").textContent };
}

const at = (h) => `2026-11-01T${String(h).padStart(2, "0")}:00:00+01:00`;
const line = Array.from({ length: 24 }, (_, i) => ({ start: at(i % 24), soc: 60 - i * 2 }));
const base = { mode: "dynamic", has_prices: true, hours: [], expected: {} };

{
  const out = render({ ...base, forecast: {
    soc: line, empty_at: null, short_kwh: 0, lowest: { start: at(23), soc: 14 },
    peak: { start: at(17), end: at(21), price: 0.4912 },
    hold_hours: [
      { start: at(13), end: at(14), price: 0.40 },
      { start: at(14), end: at(15), price: 0.40 },
      { start: at(16), end: at(17), price: 0.41 },
    ],
  } });
  check("no chart on the plan card: it has its own", out.soc === "" &&
    !out.html.includes("Verwacht accuniveau") && !out.html.includes('id="plsoc"'), out.soc);
  check("the sentence names the peak, its price and the kept hours, merged",
    out.hold.includes("Bewaart lading voor de piek van 17:00 (gem. €0,49)") &&
    out.hold.includes("ontlaadt niet 13:00–15:00, 16:00–17:00"), out.hold);
}

{
  const out = render({ ...base, forecast: {
    soc: line, empty_at: at(19), short_kwh: 6.4, lowest: { start: at(19), soc: 5 },
    peak: { start: at(17), end: at(21), price: 0.49 }, hold_hours: [],
  } });
  check("nothing kept, but empty: says when, and what the grid gives after",
    out.hold.includes("Verwacht leeg om 19:00") && out.hold.includes("6.4 kWh"), out.hold);
}

{
  const out = render({ ...base, forecast: {
    soc: line, empty_at: null, short_kwh: 0, lowest: { start: at(6), soc: 22 },
    peak: null, hold_hours: [],
  } });
  check("all well: the low point", out.hold.includes("laagste stand 22 % om 06:00"), out.hold);
}

{
  const out = render({ ...base });
  check("an older integration: nothing said", out.hold === "", out);
}

console.log(fails ? `\n${fails} FAILED` : "\nforecast checks pass");
process.exit(fails ? 1 : 0);

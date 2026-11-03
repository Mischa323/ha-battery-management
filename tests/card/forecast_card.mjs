// The expected charge as a card of its own.
//
// Asked for on 3 November: "je hebt het verwachte percentage in het plan
// gemaakt, kan je daarvan een losse kaart maken". The plan sensor's forecast,
// larger than the plan card's line: hours along the bottom, the peak, the
// hours kept for it, the minimum, and where the packs run dry.
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
const realNow = Date.now;
Date.now = () => Date.parse("2026-11-03T14:05:00+01:00");
new Function(src)();
const Card = _defined.get("battery-management-forecast-card");
check("the card is defined", !!Card, [..._defined.keys()]);

const nodes = new Map();
function render(attrs, config = { plan: "sensor.bm_plan" }) {
  nodes.clear();
  const card = new Card();
  Object.defineProperty(card, "innerHTML", {
    set(html) { this._html = html; }, get() { return this._html || ""; }, configurable: true,
  });
  card.querySelector = (sel) => {
    const id = sel.replace("#", "");
    if (!nodes.has(id)) nodes.set(id, { textContent: "", innerHTML: "" });
    return nodes.get(id);
  };
  card.setConfig(config);
  card.hass = { states: attrs === undefined ? {} : { "sensor.bm_plan": { state: "ok", attributes: attrs } } };
  const text = (id) => (nodes.get(id) || {}).textContent || "";
  return {
    html: card._html, now: text("fcnow"), low: text("fclow"),
    chart: (nodes.get("fcchart") || {}).innerHTML || "", legend: text("fclegend"), says: text("fcsays"),
  };
}

const at = (h) => `2026-11-03T${String(h % 24).padStart(2, "0")}:00:00+01:00`;
const soc = Array.from({ length: 24 }, (_, i) => ({ start: at(14 + i).replace("11-03T0", "11-04T0"), soc: 70 - i * 2.5 }))
  .map((r, i) => ({ ...r, start: i + 14 < 24 ? at(14 + i) : `2026-11-04T${String(i - 10).padStart(2, "0")}:00:00+01:00` }));

{
  const out = render({
    buy_ceiling_min: 25,
    forecast: {
      soc, empty_at: null, short_kwh: 0, lowest: { start: soc[23].start, soc: 12.5 },
      peak: { start: at(17), end: at(21), price: 0.48 },
      hold_hours: [{ start: at(15), end: at(16), price: 0.40 }],
    },
  });
  check("the charge now, large", out.now === "70 %", out.now);
  check("the low point beside it", out.low.includes("laagste 13 %"), out.low);
  check("a dashed line for what is expected", out.chart.includes('<polyline class="line"'), out.chart);
  check("zoom buttons and a way back to now", out.chart.includes('data-hours="6"') &&
    out.chart.includes('data-hours="48"') && out.chart.includes('data-now="1"'), out.chart);
  check("grid at 25, 50 and 75 %", (out.chart.match(/class="grid"/g) || []).length === 3, out.chart);
  check("the peak and the kept hour shaded", out.chart.includes('class="peak"') &&
    out.chart.includes('class="hold"'), out.chart);
  check("the minimum dashed", out.chart.includes('class="minimum"'), out.chart);
  check("hours along the bottom", out.chart.includes(">15:00<") && out.chart.includes(">18:00<"), out.chart);
  check("the legend names what is shown",
    out.legend.includes("rood: piek") && out.legend.includes("oranje") &&
    out.legend.includes("minimum 25 %"), out.legend);
  check("and the sentence says what is kept",
    out.says.includes("Bewaart lading voor de piek van 17:00"), out.says);
  check("its own title", out.html.includes('header="Verwacht accuniveau"'), out.html.slice(0, 80));
}

{
  const out = render({
    forecast: {
      soc, empty_at: at(19), short_kwh: 4.2, lowest: { start: at(19), soc: 5 },
      peak: { start: at(17), end: at(21), price: 0.48 }, hold_hours: [],
    },
  });
  check("dry: a red dot, and said", out.chart.includes('class="empty"') &&
    out.legend.includes("rode stip") && out.says.includes("Verwacht leeg om 19:00"), out);
  check("no minimum, no dashed line", !out.chart.includes('class="minimum"'), out.chart);
}

{
  const out = render({ mode: "dynamic" });
  check("no forecast yet: says why, draws nothing",
    out.chart === "" && out.says.includes("Nog geen verwachting"), out);
}

{
  const out = render(undefined);
  check("no plan sensor: says so", out.says.includes("Plan-sensor niet ingesteld"), out.says);
}

{
  const stub = Card.getStubConfig({ states: { "sensor.battery_management_plan": {} } }, ["sensor.battery_management_plan"]);
  check("it finds the plan sensor itself",
    stub.type === "custom:battery-management-forecast-card" && stub.plan === "sensor.battery_management_plan", stub);
}

// -- the parts the pointer drives -------------------------------------------
const helpers = new Function(src + ";return {socHistory, socZoom, socPan, socSpan, socAt, socBounds, socTicks};")();
const H = 3600e3;
const t0 = Date.parse("2026-11-03T00:00:00+01:00");
{
  // two packs, 14 and 7 kWh, the first at 60 then 40, the second at 30 throughout
  const answer = {
    "sensor.a": [{ s: "60", lu: t0 / 1000 }, { s: "40", lu: (t0 + 2 * H) / 1000 }],
    "sensor.b": [{ state: "30", last_updated: new Date(t0).toISOString() }],
  };
  const past = helpers.socHistory(answer, [{ entity: "sensor.a", kwh: 14 }, { entity: "sensor.b", kwh: 7 }], t0, t0 + 3 * H);
  check("history is the packs together, weighted by size",
    Math.abs(past[0].soc - 50) < 0.01 && Math.abs(past[past.length - 1].soc - (40 * 14 + 30 * 7) / 21) < 0.01, past);
  check("a quarter at a time", past.length === 13, past.length);
  check("a pack without history leaves the line out",
    helpers.socHistory({ "sensor.a": answer["sensor.a"] }, [{ entity: "sensor.a", kwh: 14 }, { entity: "sensor.b", kwh: 7 }], t0, t0 + H).length === 0, "");
}
{
  const bounds = { lo: t0 - 48 * H, hi: t0 + 24 * H };
  const view = helpers.socSpan(24, t0, bounds);
  check("24 hours: six behind, eighteen ahead", view.from === t0 - 6 * H && view.to === t0 + 18 * H, view);
  const zin = helpers.socZoom(view, 0.5, 0.25, bounds);
  check("zooming in about now keeps now where it was",
    Math.abs((t0 - zin.from) / (zin.to - zin.from) - 0.25) < 1e-9 && zin.to - zin.from === 12 * H, zin);
  check("never closer than an hour", helpers.socZoom(view, 0.001, 0.5, bounds).to -
    helpers.socZoom(view, 0.001, 0.5, bounds).from === H, "");
  const back = helpers.socPan(view, -10, bounds);
  check("dragging back stops at the start of the history", back.from === bounds.lo, back);
  const ahead = helpers.socPan(view, 10, bounds);
  check("and forward at the end of the forecast", ahead.to === bounds.hi, ahead);
  check("zoomed all the way out it is the whole", (() => {
    const all = helpers.socZoom(view, 100, 0.5, bounds);
    return all.from === bounds.lo && all.to === bounds.hi;
  })(), "");
  check("the hours along the bottom thin out as it zooms out",
    (helpers.socTicks({ from: t0, to: t0 + 6 * H }).match(/<span/g) || []).length === 5 &&
    (helpers.socTicks({ from: t0, to: t0 + 48 * H }).match(/<span/g) || []).length === 7, "");
}
{
  const past = [{ t: t0, soc: 50 }, { t: t0 + H, soc: 45 }];
  const ahead = [{ t: t0 + H, soc: 45 }, { t: t0 + 2 * H, soc: 30 }];
  const before = helpers.socAt(past, ahead, t0 + 10 * 60e3, t0 + H);
  const after = helpers.socAt(past, ahead, t0 + 2 * H, t0 + H);
  check("the read-out: measured behind now", before.soc === 50 && !before.expected, before);
  check("and expected ahead of it", after.soc === 30 && after.expected, after);
}

console.log(fails ? `\n${fails} FAILED` : "\nforecast card checks pass");
process.exit(fails ? 1 : 0);

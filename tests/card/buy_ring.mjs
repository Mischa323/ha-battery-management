// The cheap *band* and the hours the charging is actually aimed at are two
// different facts, and the chart has to show both.
//
// Reported from the primary site on 2026-08-20: one green bar where four were
// expected. The ranking was right - the packs only had room for one more hour
// - but the chart had a single channel to say it in, so the cheap stretch that
// hour was picked out of had disappeared. Green is the band again; the plan is
// a blue ring drawn on top of it.
//
// The ring is a `box-shadow` and not an `outline` on purpose, and that is what
// most of this file is about: "now" and "picked" have both already claimed the
// outline, and a planned hour can be either of those at the same moment. If it
// ever goes back to being an outline the ring silently replaces the current-
// hour marker, which is a worse chart than the one this fixed.
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

const { priceBars, drawPrices, planSays, PRICE_CSS, PRICE_LEGEND, PRICE_BUY_RING } =
  new Function(
    src +
      ";return {priceBars, drawPrices, planSays, PRICE_CSS, PRICE_LEGEND, PRICE_BUY_RING};"
  )();

const hour = 3600000;
const now = Date.now();
const slot = (offset, price, role, extra = {}) => ({
  start: new Date(now + offset * hour).toISOString(),
  end: new Date(now + (offset + 1) * hour).toISOString(),
  price,
  role,
  past: offset < -1,
  ...extra,
});

// four cheap hours, and the charging aimed at exactly one of them
const hours = [
  slot(-3, 0.05, "cheap", { buy: true, bought: true }), // planned, and paid
  slot(-2, 0.06, "cheap", { buy: true }),               // planned, never spent
  slot(0, 0.07, "cheap"),                               // in the band, not planned
  slot(1, 0.08, "cheap", { buy: true }),                // planned, still ahead
  slot(2, 0.90, "dear"),
];
const bars = priceBars(hours).bars;

check("all four cheap hours keep their green",
  bars.filter((b) => b.role === "cheap").length === 4,
  bars.map((b) => b.role));
check("only the planned hours carry the flag",
  bars.map((b) => b.buy).join() === "true,true,false,true,false",
  bars.map((b) => b.buy));
check("a bar in the band but not planned is still green",
  bars[2].role === "cheap" && bars[2].buy === false,
  [bars[2].role, bars[2].buy]);

// --- the markup ----------------------------------------------------------
const made = [];
const el = () => {
  const node = {
    style: {},
    innerHTML: "",
    addEventListener() {},
    getBoundingClientRect: () => ({ left: 0, width: 100 }),
  };
  made.push(node);
  return node;
};
const plot = el();
drawPrices(plot, el(), hours, null);

const ringed = (plot.innerHTML.match(/class="pbar[^"]*\bbuy\b/g) || []).length;
check("three bars get the ring class", ringed === 3, ringed);
check("the ring class never lands on an unplanned bar",
  !/class="pbar[^"]*\bbuy\b[^>]*>/.test(
    plot.innerHTML.split("</div></div>")[2] || ""
  ),
  plot.innerHTML.split("</div></div>")[2]);

// --- the CSS channel -----------------------------------------------------
const ringRule = PRICE_CSS.match(/\.pbar\.buy\s*\{[^}]*\}/);
check("the ring rule exists", !!ringRule, PRICE_CSS.slice(0, 40));
check("the ring is a box-shadow, so it composes with .now and .picked",
  ringRule && /box-shadow/.test(ringRule[0]) && !/outline/.test(ringRule[0]),
  ringRule && ringRule[0]);
check("the current-hour outline is still its own channel",
  /\.slot\.now \.pbar \{[^}]*outline/.test(PRICE_CSS),
  "now rule");
check("the ring is blue, not a third green or red",
  /^#[0-9a-f]{6}$/i.test(PRICE_BUY_RING) &&
    parseInt(PRICE_BUY_RING.slice(5, 7), 16) >
      Math.max(
        parseInt(PRICE_BUY_RING.slice(1, 3), 16),
        parseInt(PRICE_BUY_RING.slice(3, 5), 16)
      ),
  PRICE_BUY_RING);
check("the legend explains it", PRICE_LEGEND.includes(PRICE_BUY_RING), PRICE_LEGEND);

// --- the wording ---------------------------------------------------------
check("a planned hour ahead is worded as an intention",
  planSays({ buy: true }, false) === " — hier gaat hij laden",
  planSays({ buy: true }, false));
check("a planned hour that has gone drops the future tense",
  planSays({ buy: true, past: true }, true) === " — hier zou hij laden",
  planSays({ buy: true, past: true }, true));
check("actually paid outranks merely planned",
  planSays({ buy: true, bought: true }, true) === " — toen geladen",
  planSays({ buy: true, bought: true }, true));
check("an ordinary hour says nothing extra",
  planSays({}, false) === "", planSays({}, false));

// a bar's tooltip must carry both the band and the plan
check("the tooltip names the band and the plan together",
  bars[3].label.includes("goedkoop genoeg om te kopen") &&
    bars[3].label.includes("hier gaat hij laden"),
  bars[3].label);

process.exit(fails ? 1 : 0);

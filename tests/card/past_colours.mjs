// An hour that has gone keeps its colour, faded - it does not turn grey.
//
// Asked for by the owner: by teatime the whole left half of the chart was one
// flat grey, so the picture could not answer "which hours did the battery
// charge on". It knows: the integration writes each hour's verdict down while
// that hour is the current one, and hands it back on the plan.
//
// The distinction the card has to get right is between two kinds of past. An
// hour from *today's plan* carries a recorded verdict and keeps it. An hour
// from the **recorder** (yesterday and back) carries prices and nothing else,
// because which hours were bought on then was never written down - so those
// stay grey, and re-colouring them against today's ranking would draw
// decisions that were never taken. Both are asserted here.
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

const { priceBars, historySlots, PRICE_COLOUR } = new Function(
  src + ";return {priceBars, historySlots, PRICE_COLOUR};"
)();

// Two hours behind us and two ahead of a "now" the clock actually agrees with,
// because `priceBars` reads the wall clock for the fading and the "now" marker.
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

const hours = [
  slot(-3, 0.10, "cheap", { bought: true }),   // bought this morning
  slot(-2, 0.90, "dear"),                      // saved for, and it has gone
  slot(0, 0.20, "cheap"),                      // the hour we are in
  slot(1, 0.80, "dear"),                       // still ahead
];
const bars = priceBars(hours).bars;

check("a spent cheap hour keeps its green",
  bars[0].role === "cheap" && PRICE_COLOUR[bars[0].role] === PRICE_COLOUR.cheap,
  bars[0].role);
check("a spent dear hour keeps its red",
  bars[1].role === "dear", bars[1].role);
check("both are marked as gone, which is what fades them",
  bars[0].past === true && bars[1].past === true,
  [bars[0].past, bars[1].past]);
check("the hour we are in is not faded",
  bars[2].past === false && bars[2].current === true,
  [bars[2].past, bars[2].current]);
check("nor is one still to come",
  bars[3].past === false, bars[3].past);

// The wording has to move with the tense. Naming an hour this morning among
// "de goedkoopste uren van vandaag" reads as something still on offer.
//
// Matched on the tense marker rather than on the phrase, so rewording the band
// does not fail this for the wrong reason - what is being checked is that the
// two tenses differ, not what either one says.
check("a past hour is described in the past tense",
  /^was /.test(bars[0].label.split("—")[2].trim()), bars[0].label);
check("and the current one is not",
  !/^was /.test(bars[2].label.split("—")[2].trim()), bars[2].label);
check("the two tenses really are different wording",
  bars[0].label.split("—")[2].trim() !== bars[2].label.split("—")[2].trim(),
  [bars[0].label, bars[2].label]);
check("an hour the grid was actually paid for says so",
  /toen geladen/.test(bars[0].label), bars[0].label);
check("and one that merely looked cheap does not",
  !/toen geladen/.test(bars[1].label), bars[1].label);

// ------------------------------------------------------------ the recorder
// Days before today come back from long-term statistics: prices, no verdicts.
const yesterday = historySlots([
  { start: Date.UTC(2026, 7, 18, 6), end: Date.UTC(2026, 7, 18, 7), mean: 0.25 },
]);
const old = priceBars(yesterday).bars;
check("a recorded day has no verdict to keep, so it stays grey",
  old[0].role === "past", old[0].role);
check("and is faded like everything else that has gone",
  old[0].past === true, old[0].past);

// An older integration publishes hours without the `past` flag at all. The
// chart still has to fade them, or a whole morning renders as if it were live.
const noFlag = [{ start: hours[0].start, end: hours[0].end, price: 0.1, role: "cheap" }];
check("an hour whose end has gone is faded even without the flag",
  priceBars(noFlag).bars[0].past === true, priceBars(noFlag).bars[0]);

process.exit(fails ? 1 : 0);

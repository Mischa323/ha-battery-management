// The zoom buttons, wired to a whole card rather than to a function.
//
// price_zoom.mjs checks that the fold is honest. This checks that pressing the
// button actually folds anything - which is a different failure, and the one
// this repo has already shipped once: the day picker had the markup, the CSS,
// the cursor and a printed instruction to the reader, and no listener. It was
// decorative for a fortnight.
//
// So this drives the real card: build it, feed it a quarter-hourly day, and
// press the buttons the way a thumb would.
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
globalThis.console.info = () => {};

let fails = 0;
const check = (name, cond, got) => {
  if (!cond) { console.log("FAIL", name, JSON.stringify(got)); fails++; }
  else console.log("ok  ", name);
};

/**
 * A DOM stand-in that answers only for ids the markup really contains.
 *
 * That restriction is the point: a stub that returns an element for every
 * selector would let a typo'd id pass, which is exactly the kind of mistake
 * a card of this size makes and no unit test catches.
 */
class El {
  constructor(id) {
    this.id = id;
    this.style = {};
    this.innerHTML = "";
    this.textContent = "";
    this.clientWidth = 320;
    this.scrollWidth = 320;
    this.scrollLeft = 0;
    this.offsetLeft = 0;
    this.offsetWidth = 11;
    this.listeners = {};
    this.style.setProperty = (k, v) => (this.style[k] = v);
    this.getBoundingClientRect = () => ({ left: 0, width: this.clientWidth });
    const classes = new Set();
    this.classes = classes;
    this.classList = {
      toggle: (n, on) => (on ? classes.add(n) : classes.delete(n)),
      contains: (n) => classes.has(n),
      add: (n) => classes.add(n),
      remove: (n) => classes.delete(n),
    };
  }
  addEventListener(type, fn) {
    (this.listeners[type] = this.listeners[type] || []).push(fn);
  }
  fire(type, event) {
    for (const fn of this.listeners[type] || []) fn(event);
  }
  querySelector(sel) {
    if (!this.innerHTML.includes(`id="${sel.slice(1)}"`)) return null;
    this._kids = this._kids || {};
    return (this._kids[sel] = this._kids[sel] || new El(sel));
  }
}
globalThis.HTMLElement = El;

const [, PricesCard] = new Function(
  src + ";return [BatteryManagementCard, BatteryManagementPricesCard];"
)();

/** Local midnight today.
 *
 * Today, not a fixed date: the card draws whichever day `chartSlots` is asked
 * for, and that defaults to today. A day baked into the test is a day the card
 * stops being able to find - this file was written on 11 September and every
 * assertion in it went hollow on the 12th, drawing nought bars and reporting
 * it as a wiring failure.
 */
function midnight() {
  const start = new Date();
  start.setHours(0, 0, 0, 0);
  return start.getTime();
}

/** A full quarter-hourly day, as Frank publishes it: 96 slots from midnight. */
function quarterDay() {
  const hours = [];
  const base = midnight();
  for (let i = 0; i < 96; i++) {
    const from = base + i * 900000;
    hours.push({
      start: new Date(from).toISOString(),
      end: new Date(from + 900000).toISOString(),
      price: 0.2 + 0.001 * i,
      role: i % 8 < 2 ? "cheap" : i > 70 ? "dear" : "normal",
      buy: i === 12,
    });
  }
  return hours;
}

const card = new PricesCard();
card.setConfig({ type: "custom:battery-management-prices-card", prices: "sensor.plan" });
card.hass = { states: { "sensor.plan": { state: "0.3", attributes: { hours: quarterDay() } } } };

const bars = () =>
  (card.querySelector("#plot").innerHTML.match(/class="slot/g) || []).length;
const level = () => card.querySelector("#plevel").textContent;
const press = (id) => {
  const button = card.querySelector(id);
  check(`${id} exists and is listening`, !!(button && button.listeners.click), id);
  button.listeners.click[0]();
};

check("it opens folded to hours", bars() === 24, bars());
check("and the strip does not scroll until it needs to",
  !card.querySelector("#plot").classes.has("wide"),
  [...card.querySelector("#plot").classes]);
check("the zoom row is offered, because there are quarters to see",
  card.querySelector("#pzoom").style.display === "",
  card.querySelector("#pzoom").style.display);
check("and − is disabled, because there is nowhere further out to go",
  card.querySelector("#pout").classes.has("off"),
  [...card.querySelector("#pout").classes]);

// One press is not enough on a 320 px strip: 96 quarters need about 2.6x
// before each is 6 px wide. That is the arithmetic doing its job, not a bug -
// so press until the quarters arrive rather than asserting a magic count.
press("#pin");
check("one press widens the strip but keeps the hours",
  card.querySelector("#plot").classes.has("wide") && bars() === 24,
  [bars(), [...card.querySelector("#plot").classes]]);

press("#pin");
check("a second press reaches the quarters", bars() === 96, bars());
check("and the level says which you are looking at",
  level() === "per kwartier", level());

press("#pout");
press("#pout");
check("pressing − folds it back", bars() === 24, bars());
check("and the level says so", level() === "per uur", level());

// ------------------------------------------------------------- the pinch
// The gesture the owner actually asked for. Two fingers spreading apart must
// do what two presses did, without the page zooming instead of the chart.
const strip = card.querySelector("#pscroll");
const touch = (x) => ({ clientX: x, clientY: 0 });
let prevented = 0;
const pinch = (a, b) => ({
  touches: [touch(a), touch(b)],
  preventDefault: () => prevented++,
});

check("the strip listens for a pinch",
  !!(strip.listeners.touchstart && strip.listeners.touchmove), Object.keys(strip.listeners));

strip.fire("touchstart", pinch(140, 180));
strip.fire("touchmove", pinch(60, 260));
check("spreading two fingers reaches the quarters", bars() === 96, bars());
check("and the pinch is taken off the page, so the dashboard does not zoom",
  prevented >= 2, prevented);

strip.fire("touchmove", pinch(150, 170));
check("pinching back in folds it to hours again", bars() === 24, bars());
strip.fire("touchend", {});

// A one-finger drag is the scroller's, and must never be swallowed: that is
// what "scrolling through the hours" rests on.
prevented = 0;
strip.fire("touchstart", { touches: [touch(100)], preventDefault: () => prevented++ });
strip.fire("touchmove", { touches: [touch(40)], preventDefault: () => prevented++ });
check("a one-finger drag is left to the browser to scroll",
  prevented === 0, prevented);

// ------------------------------------------------------- keeping your place
card._scale = 1;
card._update();
press("#pin");
press("#pin");
check("zooming lets go of the tapped bar rather than mistranslating it",
  card._picked === null, card._picked);

// --------------------------------------------------------------- the edges
for (let i = 0; i < 12; i++) press("#pin");
check("+ stops at the far end rather than zooming forever",
  card.querySelector("#pin").classes.has("off"),
  [...card.querySelector("#pin").classes]);
for (let i = 0; i < 12; i++) press("#pout");
check("and − stops at the fitted day", bars() === 24, bars());

// An hourly feed has nothing finer to show, so the control must not be offered.
const hourly = new PricesCard();
hourly.setConfig({ type: "x", prices: "sensor.plan" });
const flat = [];
for (let i = 0; i < 24; i++) {
  const from = midnight() + i * 3600000;
  flat.push({
    start: new Date(from).toISOString(),
    end: new Date(from + 3600000).toISOString(),
    price: 0.2,
    role: "normal",
  });
}
hourly.hass = { states: { "sensor.plan": { state: "0.2", attributes: { hours: flat } } } };
check("an hourly feed is not offered a zoom it cannot honour",
  hourly.querySelector("#pzoom").style.display === "none",
  hourly.querySelector("#pzoom").style.display);

// Opening straight on the quarters, for a screen where they do fit.
const zoomed = new PricesCard();
zoomed.setConfig({ type: "x", prices: "sensor.plan", price_zoom: "quarter" });
zoomed.hass = { states: { "sensor.plan": { state: "0.3", attributes: { hours: quarterDay() } } } };
check("price_zoom: quarter opens on the quarters",
  (zoomed.querySelector("#plot").innerHTML.match(/class="slot/g) || []).length === 96,
  (zoomed.querySelector("#plot").innerHTML.match(/class="slot/g) || []).length);

console.log(fails ? `\n${fails} FAILED` : "\nall zoom wiring checks pass");
process.exit(fails ? 1 : 0);

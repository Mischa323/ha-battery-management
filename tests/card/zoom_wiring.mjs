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
    this.offsetLeft = 0;
    this.offsetWidth = 11;
    this.listeners = {};
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

/** A full quarter-hourly day, as Frank publishes it: 96 slots from 22:00 UTC. */
function quarterDay() {
  const hours = [];
  const base = Date.UTC(2026, 8, 10, 22, 0);
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
  card.querySelector("#pzoom").style.display === "", card.querySelector("#pzoom").style.display);

press("#pin");
check("pressing + shows all 96 quarters", bars() === 96, bars());
check("and widens the bars so they can be told apart",
  card.querySelector("#plot").classes.has("wide"), [...card.querySelector("#plot").classes]);
check("the level says which you are looking at",
  card.querySelector("#plevel").textContent === "per kwartier",
  card.querySelector("#plevel").textContent);

press("#pout");
check("pressing − folds it back", bars() === 24, bars());
check("and the level says so",
  card.querySelector("#plevel").textContent === "per uur",
  card.querySelector("#plevel").textContent);

// A tapped bar is an index, and the indices mean different things at the two
// levels: bar 40 is 10:00 at quarters and does not exist at hours. Carrying
// the selection across would move the readout to an hour nobody tapped.
press("#pin");
card._picked = 40;
press("#pout");
check("zooming lets go of the tapped bar rather than mistranslating it",
  card._picked === null, card._picked);

// An hourly feed has nothing finer to show, so the control must not be offered.
const hourly = new PricesCard();
hourly.setConfig({ type: "x", prices: "sensor.plan" });
const flat = [];
for (let i = 0; i < 24; i++) {
  const from = Date.UTC(2026, 8, 10, 22) + i * 3600000;
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

// Opening straight on the quarters, for a big screen where they do fit.
const zoomed = new PricesCard();
zoomed.setConfig({ type: "x", prices: "sensor.plan", price_zoom: "quarter" });
zoomed.hass = { states: { "sensor.plan": { state: "0.3", attributes: { hours: quarterDay() } } } };
check("price_zoom: quarter opens on the quarters",
  (zoomed.querySelector("#plot").innerHTML.match(/class="slot/g) || []).length === 96,
  (zoomed.querySelector("#plot").innerHTML.match(/class="slot/g) || []).length);

console.log(fails ? `\n${fails} FAILED` : "\nall zoom wiring checks pass");
process.exit(fails ? 1 : 0);

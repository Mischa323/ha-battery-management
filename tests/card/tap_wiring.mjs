// The bars have to be *wired*, not merely tappable in theory.
//
// tap.mjs pins `pickedSlot`, which was correct all along. The bug was one level
// out: the prices card had the picked-bar CSS, the `cursor: pointer`, and even
// printed "tik nogmaals voor nu" to the reader - while nothing was listening
// for the tap. On a desktop the `title` tooltip hid it; on a phone, which has
// no hover at all, the chart simply could not be read.
//
// So this asserts the wiring, per card, which is the thing that was missing.
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
const [ManagementCard, PricesCard] = new Function(
  src + ";return [BatteryManagementCard, BatteryManagementPricesCard];"
)();

let fails = 0;
const check = (name, cond, got) => {
  if (!cond) { console.log("FAIL", name, JSON.stringify(got)); fails++; }
  else console.log("ok  ", name);
};

/** Just enough element for a card to hang a listener on. */
class FakeEl {
  constructor() {
    this.listeners = {};
    this.style = {};
    this.innerHTML = "";
    this.dataset = {};
  }
  addEventListener(type, fn) {
    (this.listeners[type] = this.listeners[type] || []).push(fn);
  }
}

/** A card whose querySelector hands back a stub for whatever is asked for. */
function build(Cls, config) {
  const els = new Map();
  const card = new Cls();
  card.querySelector = (sel) => {
    const key = String(sel).replace(/^#/, "");
    if (!els.has(key)) els.set(key, new FakeEl());
    return els.get(key);
  };
  card.setConfig(config);
  card._build();
  // A card that never wired the plot never asked for it either, so the stub
  // would not exist and this would crash instead of reporting. Ask once, so a
  // missing listener reads as a failed check rather than a stack trace.
  card.querySelector("#plot");
  card._els = els;
  // the readout is redrawn on every pick; count it instead of rendering it
  card._redraws = 0;
  card._update = () => { card._redraws += 1; };
  return card;
}

const tapOn = (card, index) => {
  const plot = card._els.get("plot");
  const handlers = (plot.listeners.click || []);
  if (!handlers.length) return false;
  handlers[0]({ target: { closest: () => ({ dataset: { i: String(index) } }) } });
  return true;
};

for (const [name, Cls, config] of [
  ["management card", ManagementCard, { prices: "sensor.x_plan" }],
  ["prices card", PricesCard, { prices: "sensor.x_plan" }],
]) {
  const card = build(Cls, config);
  const plot = card._els.get("plot");

  check(`${name}: the chart listens for a tap`,
    (plot.listeners.click || []).length === 1,
    Object.keys(plot.listeners));

  check(`${name}: tapping a bar selects it`,
    tapOn(card, 5) && card._picked === 5, card._picked);

  check(`${name}: tapping the same bar returns to now`,
    tapOn(card, 5) && card._picked === null, card._picked);

  check(`${name}: tapping another bar moves the readout`,
    tapOn(card, 5) && tapOn(card, 9) && card._picked === 9, card._picked);

  check(`${name}: every tap redraws the readout`, card._redraws === 4,
    card._redraws);

  // a tap on the padding between bars must not clear the selection
  const before = card._picked;
  (card._els.get("plot").listeners.click[0])({ target: { closest: () => null } });
  check(`${name}: a miss changes nothing`, card._picked === before, card._picked);
}

console.log(fails ? `\n${fails} FAILED` : "\ntap wiring checks pass");
process.exit(fails ? 1 : 0);

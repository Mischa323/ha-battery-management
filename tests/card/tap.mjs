// Tapping a bar: which slot the readout is about.
import { readFileSync } from "node:fs";
process.env.TZ = "Europe/Amsterdam";
const src = readFileSync(
  "custom_components/battery_management/www/battery-management-card.js",
  "utf8"
);
globalThis.window = globalThis;
globalThis.customElements = { define() {} };
globalThis.HTMLElement = class {};
globalThis.console.info = () => {};
const [pickedSlot, priceBars] = new Function(
  src + ";return [pickedSlot, priceBars];"
)();

let fails = 0;
const check = (name, cond, got) => {
  if (!cond) { console.log("FAIL", name, JSON.stringify(got)); fails++; }
  else console.log("ok  ", name);
};

// a day of hourly slots around the current hour
const top = new Date(); top.setMinutes(0, 0, 0);
const hours = Array.from({ length: 24 }, (_, i) => {
  const start = new Date(top.getTime() + (i - 6) * 3600e3);
  return {
    start: start.toISOString(),
    end: new Date(start.getTime() + 3600e3).toISOString(),
    price: 0.1 + i / 100,
    role: i < 6 ? "past" : "normal",
  };
});

const live = pickedSlot(hours, null);
check("nothing picked -> the current hour", live.live === true, live);
check("and it is the right one", live.slot.start === hours[6].start, live.slot);

const chosen = pickedSlot(hours, 20);
check("a pick wins over the clock", chosen.live === false, chosen);
check("and it is the bar tapped", chosen.slot.start === hours[20].start, chosen.slot);
check("its price comes through", chosen.slot.value === hours[20].price, chosen.slot);

check("a past bar can be read too", pickedSlot(hours, 0).slot.start === hours[0].start,
  pickedSlot(hours, 0));
check("out of range falls back to now", pickedSlot(hours, 99).live === true,
  pickedSlot(hours, 99));

// the marked bar, and the current one, must stay distinguishable
const bars = priceBars(hours);
check("exactly one bar is 'now'", bars.bars.filter((b) => b.current).length === 1,
  bars.bars.filter((b) => b.current).length);

console.log(fails ? `\n${fails} FAILED` : "\ntap checks pass");
process.exit(fails ? 1 : 0);

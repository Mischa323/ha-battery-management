// The card's clock, against a real Frank Energie payload.
import { readFileSync } from "node:fs";
process.env.TZ = "Europe/Amsterdam";
const src = readFileSync(
  "custom_components/battery_management/www/battery-management-card.js",
  "utf8"
);
globalThis.window = globalThis;
// a registry with get(), because the card checks before defining
const _defined = new Map();
globalThis.customElements = {
  define: (tag, cls) => _defined.set(tag, cls),
  get: (tag) => _defined.get(tag),
};
globalThis.HTMLElement = class {};
globalThis.console.info = () => {};
const [hhmm, priceBars, priceSummary] = new Function(
  src + ";return [hhmm, priceBars, priceSummary];"
)();

let fails = 0;
const check = (name, cond, got) => {
  if (!cond) { console.log("FAIL", name, JSON.stringify(got)); fails++; }
  else console.log("ok  ", name);
};

// Frank publishes UTC; the primary site reads CEST
check("UTC midnight reads as midnight", hhmm("2026-08-15T22:00:00.000Z") === "00:00",
  hhmm("2026-08-15T22:00:00.000Z"));
check("the evening peak is the evening", hhmm("2026-08-15T20:00:00.000Z") === "22:00",
  hhmm("2026-08-15T20:00:00.000Z"));
// a sensor publishing a local offset must land on the same wall clock
check("an offset timestamp agrees", hhmm("2026-08-16T00:00:00+02:00") === "00:00",
  hhmm("2026-08-16T00:00:00+02:00"));
check("nonsense degrades quietly", typeof hhmm("not a date") === "string", hhmm("not a date"));

// "now" must be found whatever offset the feed uses
const now = new Date();
const hourStart = new Date(now); hourStart.setMinutes(0, 0, 0);
const hourEnd = new Date(hourStart.getTime() + 3600e3);
const utc = [{ start: hourStart.toISOString(), end: hourEnd.toISOString(), price: 0.2, role: "normal" }];
check("current slot found (Z)", priceBars(utc).bars[0].current === true, priceBars(utc).bars[0]);

const offsetIso = (d) => {
  const pad = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T` +
    `${pad(d.getHours())}:${pad(d.getMinutes())}:00+02:00`;
};
const local = [{ start: offsetIso(hourStart), end: offsetIso(hourEnd), price: 0.2, role: "normal" }];
check("current slot found (+02:00)", priceBars(local).bars[0].current === true, priceBars(local).bars[0]);
check("summary finds it too", priceSummary(local).current !== undefined, priceSummary(local));

console.log(fails ? `\n${fails} FAILED` : "\nclock checks pass");
process.exit(fails ? 1 : 0);

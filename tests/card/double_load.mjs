// The script loaded twice must still leave both cards registered.
import { readFileSync } from "node:fs";
const src = readFileSync(
  "custom_components/battery_management/www/battery-management-card.js",
  "utf8"
);
globalThis.window = globalThis;
globalThis.HTMLElement = class {};
globalThis.console.info = () => {};

const defined = new Map();
globalThis.customElements = {
  define(tag, cls) {
    if (defined.has(tag)) throw new Error(`${tag} already defined`);
    defined.set(tag, cls);
  },
  get: (tag) => defined.get(tag),
};

let fails = 0;
const check = (name, cond, got) => {
  if (!cond) { console.log("FAIL", name, JSON.stringify(got)); fails++; }
  else console.log("ok  ", name);
};

// this is the real situation: the integration registers it, and a hand-added
// Lovelace resource loads the very same file a second time
new Function(src)();
new Function(src)();

check("both elements defined", defined.size === 2, [...defined.keys()]);
const types = window.customCards.map((c) => c.type);
check("both cards listed", types.includes("battery-management-card") &&
  types.includes("battery-management-prices-card"), types);
check("no duplicate entries", new Set(types).size === types.length, types);
check("the description is the current one",
  window.customCards.find((c) => c.type === "battery-management-card")
    .description.includes("price chart"),
  window.customCards);

console.log(fails ? `\n${fails} FAILED` : "\ndouble-load checks pass");
process.exit(fails ? 1 : 0);

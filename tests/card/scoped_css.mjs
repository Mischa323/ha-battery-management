// Every card styles only itself.
//
// The cards are drawn in the page's light DOM, so a rule written by one
// reaches every other card on the dashboard. On 10 November the main card's
// ".bar" cut the forecast card's buttons in half; on 13 November a look at
// all six together showed the plan card's ".split" moving the main card's
// charge bar. Each card's rules now sit under its own root class.
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

const { scopeCss } = new Function(src + ";return {scopeCss};")();

// --- the helper ---------------------------------------------------------------
{
  const out = scopeCss(".a { x:1; } .b, .c .d { y:2; } /* note { } */ .root { z:3; } .root .e { w:4; } .root.f { v:5; }", ".root");
  const selectors = [...out.matchAll(/([^{}]+)\{/g)].map((m) => m[1].trim());
  check("a bare rule goes under the scope", selectors.includes(".root .a"), selectors);
  check("each selector of a list", selectors.includes(".root .b, .root .c .d"), selectors);
  check("the root itself, and what is under it already, are left alone",
    selectors.includes(".root") && selectors.includes(".root .e") && selectors.includes(".root.f"), selectors);
  check("comments go, their braces with them", !out.includes("note"), out);
  check("declarations are untouched", out.includes("x:1;") && out.includes("w:4;"), out);
}

// --- every card ---------------------------------------------------------------
const ROOTS = {
  "battery-management-card": ".sbc",
  "battery-management-prices-card": ".pc",
  "battery-management-plan-card": ".plc",
  "battery-management-forecast-card": ".fcc",
  "battery-management-trade-card": ".trc",
  "battery-management-savings-card": ".svc",
};
const node = () => new Proxy(function () {}, {
  get: (t, k) => (k === Symbol.toPrimitive ? () => "" : k === "length" ? 0 : k === Symbol.iterator ? [][Symbol.iterator].bind([]) : node()),
  set: () => true,
  apply: () => node(),
});
for (const [tag, root] of Object.entries(ROOTS)) {
  const Card = _defined.get(tag);
  const card = new Card();
  let html = "";
  Object.defineProperty(card, "innerHTML", { set(v) { html = v; }, get() { return html; }, configurable: true });
  card.querySelector = () => node();
  card.querySelectorAll = () => [];
  card.addEventListener = () => {};
  card.setConfig({});
  try { card.hass = { states: {}, entities: {}, callService() {} }; } catch (e) { /* only the build matters here */ }
  const css = [...html.matchAll(/<style>([\s\S]*?)<\/style>/g)].map((m) => m[1]).join("\n");
  const stray = [...css.replace(/\/\*[\s\S]*?\*\//g, "").matchAll(/([^{}]+)\{/g)]
    .flatMap((m) => m[1].split(","))
    .map((s) => s.trim())
    .filter((s) => s && !(s === root || s.startsWith(root + " ") || s.startsWith(root + ".")));
  check(tag + ": has rules, all under " + root, css.length > 100 && stray.length === 0, stray.slice(0, 8));
}

console.log(fails ? `\n${fails} FAILED` : "\nscoped css checks pass");
process.exit(fails ? 1 : 0);

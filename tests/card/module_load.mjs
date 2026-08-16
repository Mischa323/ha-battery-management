// Load the card the way the browser actually does: as an ES module.
//
// Every other check here evaluates the source with `new Function(...)`, which
// is a sloppy-mode function body - not module scope, not strict mode. A file
// can pass all of those and still fail to load in Home Assistant, and then the
// only symptom is "Custom element doesn't exist", which is also the symptom of
// half a dozen unrelated causes. So load it properly at least once.
//
// The check that matters is the pairing: anything advertised in
// `window.customCards` must have a matching custom element. Advertised but
// undefined is exactly what a card picker full of spinning placeholders looks
// like.
import { mkdtempSync, copyFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { pathToFileURL } from "node:url";

const defined = new Map();
globalThis.window = globalThis;
globalThis.DOMException = globalThis.DOMException || class extends Error {};
globalThis.customElements = {
  define: (tag, cls) => {
    if (defined.has(tag)) {
      throw new DOMException(`'${tag}' has already been defined`);
    }
    defined.set(tag, cls);
  },
  get: (tag) => defined.get(tag),
};
globalThis.HTMLElement = class {};
globalThis.console.info = () => {};

// .js is treated as CommonJS by Node unless the package says otherwise, so
// take a copy with the extension that forces module semantics
const dir = mkdtempSync(join(tmpdir(), "bm-card-"));
const copy = join(dir, "card.mjs");
copyFileSync(
  "custom_components/battery_management/www/battery-management-card.js",
  copy
);

let fails = 0;
const check = (name, cond, got) => {
  if (!cond) {
    console.log("FAIL", name, JSON.stringify(got));
    fails++;
  } else console.log("ok  ", name);
};

try {
  await import(pathToFileURL(copy).href);
} catch (err) {
  console.log("FAIL the module did not even load:", err.message);
  process.exit(1);
}

const advertised = (globalThis.customCards || []).map((c) => c.type);
check("both cards defined", defined.size === 2, [...defined.keys()]);
check(
  "the management card is defined",
  defined.has("battery-management-card"),
  [...defined.keys()]
);
check(
  "the prices card is defined",
  defined.has("battery-management-prices-card"),
  [...defined.keys()]
);
check("both cards advertised", advertised.length === 2, advertised);
check(
  "nothing advertised that does not exist",
  advertised.every((t) => defined.has(t)),
  advertised.filter((t) => !defined.has(t))
);
check(
  "nothing defined that is not offered",
  [...defined.keys()].every((t) => advertised.includes(t)),
  [...defined.keys()].filter((t) => !advertised.includes(t))
);

console.log(fails ? fails + " FAILED" : "module load checks pass");
process.exit(fails ? 1 : 0);

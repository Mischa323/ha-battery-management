// The plan card: how much from the sun, how much from the grid, and when.
//
// Asked for by the owner on 2026-08-20 - "zodat ik kan controleren wat hij
// doet". Checking is the operative word, so the thing most worth pinning is
// not the happy path but the four quite different reasons the hour list can be
// empty. "Niets gepland" must never be able to stand in for "no prices",
// "wrong mode" or "already full enough" - those want three different actions
// from the reader, and a card that blurs them is worse than no card.
//
// The other half is what this card refuses to do: there is no predicted
// setpoint anywhere in it, because the setpoint depends on the house minute by
// minute. A test asserts that stays true.
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

const { buyRowSays, NO_SPLIT, waitingSays } = new Function(
  src + ";return {buyRowSays, NO_SPLIT, waitingSays};"
)();
const Plan = _defined.get("battery-management-plan-card");
check("the card is registered", !!Plan, [..._defined.keys()]);

// --- a DOM just big enough for the card to write into ---------------------
const nodes = new Map();
function fakeCard() {
  const card = new Plan();
  card.innerHTML = "";
  Object.defineProperty(card, "innerHTML", {
    set(html) { this._html = html; },
    get() { return this._html || ""; },
    configurable: true,
  });
  card.querySelector = (sel) => {
    const id = sel.replace("#", "");
    if (!nodes.has(id)) nodes.set(id, { textContent: "", innerHTML: "" });
    return nodes.get(id);
  };
  return card;
}

const hour = 3600000;
const hhmmOf = (iso) => {
  const at = new Date(iso);
  return `${String(at.getHours()).padStart(2, "0")}:${String(at.getMinutes()).padStart(2, "0")}`;
};
const now = Date.now();
const slot = (offset, price, extra = {}) => {
  const start = new Date(now + offset * hour);
  return {
    start: start.toISOString(),
    end: new Date(start.getTime() + hour).toISOString(),
    price,
    past: offset < 0,
    role: "cheap",
    ...extra,
  };
};

function render(planAttrs, config = {}) {
  nodes.clear();
  const card = fakeCard();
  card.setConfig({ plan: "sensor.bm_plan", ...config });
  card.hass = {
    states: {
      "sensor.bm_plan": { state: "ok", attributes: planAttrs },
      ...(config._states || {}),
    },
  };
  const out = {};
  for (const [id, node] of nodes) out[id] = node;
  return out;
}

const FULL = {
  mode: "dynamic",
  has_prices: true,
  cheap_hours: [{}, {}, {}, {}],
  expected: {
    known: true,
    solar_kwh: 6.4,
    grid_kwh: 3.2,
    room_for_solar_kwh: 6.4,
    solar_remaining_kwh: 6.4,
    ceiling: 54,
  },
  hours: [
    slot(-3, 0.05, { buy: true, bought: true }),
    slot(-2, 0.06, { buy: true, bought: false }),
    slot(1, 0.07, { buy: true }),
    slot(2, 0.40, { role: "normal" }),
  ],
};

// --- the split ------------------------------------------------------------
let out = render(FULL);
check("the sun half is shown in kWh", out.plsun.textContent === "6.4 kWh",
  out.plsun.textContent);
check("the grid half is shown in kWh", out.plnet.textContent === "3.2 kWh",
  out.plnet.textContent);
check("the ceiling is explained, not just printed",
  out.plwhy.textContent.includes("54 %") &&
    out.plwhy.textContent.includes("zon"),
  out.plwhy.textContent);
check("no shortfall clause when the sun fills the room it was given",
  !out.plwhy.textContent.includes("ruimte vrij"), out.plwhy.textContent);

out = render({
  ...FULL,
  expected: { ...FULL.expected, room_for_solar_kwh: 5.6, solar_kwh: 2.0,
              solar_remaining_kwh: 2.0, ceiling: 60 },
});
check("a bounded ceiling says the space will not be filled",
  out.plwhy.textContent.includes("ruimte vrij") &&
    out.plwhy.textContent.includes("2.0 kWh"),
  out.plwhy.textContent);

// --- how much of the sun is expected to arrive ----------------------------
//
// The ceiling is the one number on this card that decides whether the packs
// get filled, and it is set by a forecast of sun that mostly never reaches
// them. Unsaid, "koopt bij tot 45 %" reads as a setting rather than a bet.
// The owner who lost a night to exactly that had no way to see the bet.

const MEASURED = {
  ...FULL,
  expected: {
    ...FULL.expected,
    solar_remaining_kwh: 7.4,
    solar_expected_kwh: 1.7,
    solar_capture_share: 0.23,
    solar_capture_days: 14,
  },
};

out = render(MEASURED);
check("it says how much sun is coming and how much of it lands",
  out.plsunshare.textContent.includes("7.4 kWh") &&
    out.plsunshare.textContent.includes("1.7 kWh"),
  out.plsunshare.textContent);
check("and that the rest goes to the house, which is the part nobody guesses",
  out.plsunshare.textContent.includes("huis"), out.plsunshare.textContent);
check("with the share and how much measurement is behind it",
  out.plsunshare.textContent.includes("23 %") &&
    out.plsunshare.textContent.includes("14 dagen"),
  out.plsunshare.textContent);

// Before there is history the ceiling reserves room for the whole forecast -
// which is the assumption that emptied the packs. It must be visible, not
// silently absent.
out = render({
  ...FULL,
  expected: {
    ...FULL.expected,
    solar_remaining_kwh: 7.4,
    solar_expected_kwh: 7.4,
    solar_capture_share: null,
    solar_capture_days: 0,
  },
});
check("unmeasured, it warns that room is being held for all of it",
  out.plsunshare.textContent.includes("alles") &&
    out.plsunshare.textContent.includes("nog niet gemeten"),
  out.plsunshare.textContent);

out = render({
  ...FULL,
  expected: {
    ...FULL.expected,
    solar_remaining_kwh: 0,
    solar_expected_kwh: 0,
    solar_capture_share: 0.23,
    solar_capture_days: 14,
  },
});
check("after sunset there is nothing to explain and it says nothing",
  out.plsunshare.textContent === "", out.plsunshare.textContent);

// An integration too old to publish any of this, or a card cached from before
// it existed. Neither may leave a dash in the middle of a sentence.
out = render(FULL);
check("an older integration is not warned about a measurement it cannot make",
  out.plsunshare.textContent === "", out.plsunshare.textContent);
out = render({
  ...FULL,
  expected: { ...FULL.expected, room_for_solar_kwh: 5.6, solar_kwh: 2.0,
              solar_remaining_kwh: 2.0, ceiling: 60 },
});
check("and the shortfall clause falls back to the forecast rather than a dash",
  !out.plwhy.textContent.includes("—"), out.plwhy.textContent);

// The split is unknown: no stale explanation may survive underneath it.
out = render({ ...FULL, expected: { known: false, reason: "no_forecast" } });
check("an unknown split clears the sun line with it",
  out.plsunshare.textContent === "", out.plsunshare.textContent);

// --- the hours ------------------------------------------------------------
out = render(FULL);
check("only buying hours are listed",
  (out.plhours.innerHTML.match(/class="hr"/g) || []).length === 3,
  out.plhours.innerHTML);
check("a bought hour reads as done",
  out.plhours.innerHTML.includes(">geladen<"), out.plhours.innerHTML);
check("a planned hour still ahead reads as intent",
  out.plhours.innerHTML.includes(">gaat laden<"), out.plhours.innerHTML);
check("a planned hour that came to nothing says so",
  out.plhours.innerHTML.includes(">niet geladen<"), out.plhours.innerHTML);
check("the morning is still listed at teatime",
  out.plhours.innerHTML.split('class="hr"').length - 1 === 3 &&
    out.plnone.textContent === "",
  out.plnone.textContent);

check("bought outranks merely planned",
  buyRowSays({ bought: true, buy: true, past: true }).text === "geladen",
  buyRowSays({ bought: true, buy: true, past: true }));
check("a future hour is never called a miss",
  buyRowSays({ buy: true, past: false }).text === "gaat laden",
  buyRowSays({ buy: true, past: false }));

// --- the four empty states, which is what the card is for -----------------
const empty = { ...FULL, hours: [slot(2, 0.40, { role: "normal" })] };

out = render({ ...empty, mode: "grid_zero" });
check("wrong mode says so", out.plnone.textContent.includes("deze modus"),
  out.plnone.textContent);

out = render({ ...empty, has_prices: false });
check("no prices says so", out.plnone.textContent.includes("Geen prijzen"),
  out.plnone.textContent);

out = render({ ...empty, cheap_hours: [{}, {}] });
check("cheap hours but nothing needed says the packs are full enough",
  out.plnone.textContent.includes("plafond"), out.plnone.textContent);

out = render({ ...empty, cheap_hours: [] });
check("nothing cheap enough says that instead",
  out.plnone.textContent.includes("goedkoop genoeg"), out.plnone.textContent);

check("the four messages are all different",
  new Set([
    render({ ...empty, mode: "grid_zero" }).plnone.textContent,
    render({ ...empty, has_prices: false }).plnone.textContent,
    render({ ...empty, cheap_hours: [{}, {}] }).plnone.textContent,
    render({ ...empty, cheap_hours: [] }).plnone.textContent,
  ]).size === 4,
  "distinct");

// --- a hold on the purchase ----------------------------------------------
//
// Reported on the morning of 23 September: "hij gaf vanochtend aan dat hij
// niet ging laden vandaag maar nu zegt hij van wel". Before the peak a cheaper
// window after it was holding the purchase back; the card showed the floor the
// hold stops at as the ceiling, and an empty list with "de accu's halen het
// plafond al zonder het net". After the peak: the real ceiling, and noon.

const peakAt = new Date(now + 2 * hour).toISOString();
const WAITING = {
  ...empty,
  expected: { ...FULL.expected, ceiling: 91 },
  waiting: {
    held_to: 30,
    until: peakAt,
    then_to: 91,
    hours: [slot(6, 0.18)],
  },
  hours: [
    slot(2, 0.45, { role: "dear" }),
    slot(6, 0.18, { expected: true }),
  ],
};

out = render(WAITING);
check("a hold states today's ceiling, not the floor it is holding at",
  out.plwhy.textContent.includes("tot 91 %"), out.plwhy.textContent);
check("and says how full until when, and why",
  out.plwhy.textContent.includes("hooguit tot 30 %") &&
    out.plwhy.textContent.includes(hhmmOf(peakAt)) &&
    out.plwhy.textContent.includes("goedkoper"),
  out.plwhy.textContent);
check("the hour it is waiting for is listed",
  (out.plhours.innerHTML.match(/class="hr"/g) || []).length === 1,
  out.plhours.innerHTML);
check("as expected, not as a promise",
  out.plhours.innerHTML.includes(">verwacht<") &&
    !out.plhours.innerHTML.includes(">gaat laden<"),
  out.plhours.innerHTML);
check("and never with the message that it has nothing to buy",
  !out.plnone.textContent.includes("plafond"), out.plnone.textContent);

// The expected hour can be tomorrow's, and then today's list is empty - the
// exact shape of the report. It must still say what it is waiting for.
out = render({
  ...WAITING,
  waiting: { ...WAITING.waiting, hours: [slot(30, 0.18)] },
  hours: [slot(2, 0.45, { role: "dear" })],
});
check("with nothing in today's list, it says it is waiting and for when",
  out.plnone.textContent.includes("wacht tot na de piek") &&
    out.plnone.textContent.includes("morgen"),
  out.plnone.textContent);

out = render({
  ...WAITING,
  waiting: { ...WAITING.waiting, hours: [] },
  hours: [slot(2, 0.45, { role: "dear" })],
});
check("with no hour to name yet, it names the level instead",
  out.plnone.textContent.includes("wacht tot na de piek") &&
    out.plnone.textContent.includes("91 %"),
  out.plnone.textContent);

// This morning's history must not hide the wait: bought hours are behind us,
// the one we are waiting for is tomorrow.
out = render({
  ...WAITING,
  waiting: { ...WAITING.waiting, hours: [slot(30, 0.18)] },
  hours: [slot(-3, 0.30, { buy: true, bought: true })],
});
check("past hours in the list do not swallow the wait",
  out.plnone.textContent.includes("wacht tot na de piek"),
  out.plnone.textContent);

check("an expected hour outranks nothing that has happened",
  buyRowSays({ expected: true, bought: true }).text === "geladen" &&
    buyRowSays({ expected: true, buy: true }).text === "gaat laden",
  "precedence");

check("five empty states, all different",
  new Set([
    render({ ...empty, mode: "grid_zero" }).plnone.textContent,
    render({ ...empty, has_prices: false }).plnone.textContent,
    render({ ...empty, cheap_hours: [{}, {}] }).plnone.textContent,
    render({ ...empty, cheap_hours: [] }).plnone.textContent,
    render({ ...WAITING, hours: [] }).plnone.textContent,
  ]).size === 5,
  "distinct");

// No hold, no trace of one.
out = render(FULL);
check("without a hold the ceiling sentence is unchanged",
  !out.plwhy.textContent.includes("piek"), out.plwhy.textContent);

// --- refusing to guess ----------------------------------------------------
for (const reason of ["no_capacity", "no_forecast", "no_units"]) {
  out = render({ ...FULL, expected: { known: false, reason } });
  check(`${reason} is explained, not shown as a number`,
    out.plsun.textContent === "—" && out.plnet.textContent === "—" &&
      out.plwhy.textContent === NO_SPLIT[reason],
    [out.plsun.textContent, out.plwhy.textContent]);
}
check("an unknown reason still says something",
  render({ ...FULL, expected: { known: false, reason: "wat?" } })
    .plwhy.textContent.length > 0,
  "fallback");

// --- what it must never grow ---------------------------------------------
//
// Asked of the *code*, not of the prose: a comment may well mention the
// setpoint - one does, explaining why the policy line is there - and matching
// the bare word would make this test fail for the wrong reason and then get
// deleted. What must stay true is that the card never reads a setpoint entity,
// because the moment it has one somebody will draw it, and today's setpoint
// depends on the house minute by minute.
const body = src.slice(
  src.indexOf("class BatteryManagementPlanCard"),
  src.indexOf('defineCard("battery-management-plan-card"')
);
const code = body.replace(/\/\*[\s\S]*?\*\//g, "").replace(/\/\/.*$/gm, "");
check("the card never reads a setpoint entity",
  !/_state\(\s*["']setpoint/.test(code) && !/config\.setpoint/.test(code),
  code.match(/.*setpoint.*/i));
check("and does not wire one into a fresh config",
  !/setpoint/i.test(
    body.slice(body.indexOf("getStubConfig"), body.indexOf("getCardSize"))
  ),
  "getStubConfig");

process.exit(fails ? 1 : 0);

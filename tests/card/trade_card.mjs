// The trading card: would it sell now, what for, what have the packs saved,
// and when have they paid for themselves.
//
// Built for the first evenings in shadow, so what is pinned is mostly the
// words: the sum is written out rather than only its answer, and every reason
// it does not sell says whether that is normal or something to fix. "Too
// little margin" is the ordinary state of most hours and must never read as a
// fault; "no purchase price" must always say where to fill it in.
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

const { euro, tradeSum, paybackSays, findTradeEntities, TRADE_WHY } = new Function(
  src + ";return {euro, tradeSum, paybackSays, findTradeEntities, TRADE_WHY};"
)();
const Trade = _defined.get("battery-management-trade-card");
check("the card is registered", !!Trade, [..._defined.keys()]);
check("and advertised in the picker",
  (globalThis.customCards || []).some((c) => c.type === "battery-management-trade-card"),
  globalThis.customCards);

// --- a DOM just big enough for the card to write into ---------------------
const nodes = new Map();
function fakeCard() {
  const card = new Trade();
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
const text = (id) => (nodes.get(id) || {}).textContent || "";
const html = (id) => (nodes.get(id) || {}).innerHTML || "";

const TRADE = {
  trade_mode: "shadow",
  export_value_eur_kwh: 0.437,
  refill_eur_kwh: 0.19,
  wear_eur_kwh: 0.0298,
  margin_eur_kwh: 0.1913,
  min_margin_eur_kwh: 0.05,
  why: null,
  saldering: true,
  saldering_until: "2027-01-01",
  sell_floor: 30,
};

function states(overrides = {}) {
  const base = {
    "select.bm_slim_handelen": {
      state: "shadow",
      attributes: { options: ["off", "shadow", "on"] },
    },
    "sensor.bm_slim_handelen_status": { state: "would_sell", attributes: { ...TRADE } },
    "sensor.bm_besparing_vandaag": {
      state: "0.42",
      attributes: {
        period: "day", saved_eur: 0.42, saved_actual_eur: 0.42,
        traded_kwh: 0, traded_eur: 0, shadow_kwh: 6.7, shadow_eur: 1.28,
      },
    },
    "sensor.bm_besparing_deze_maand": {
      state: "12.5",
      attributes: { period: "month", saved_actual_eur: 12.5 },
    },
    "sensor.bm_besparing_sinds_start": {
      state: "-3.2",
      attributes: { saved_actual_eur: -3.2, saldering_until: "2027-01-01" },
    },
    "sensor.bm_terugverdientijd": {
      state: "10.0",
      attributes: {
        known: true, years_without_saldering: 10.0, years_with_saldering: 20.5,
        years_to_go: 9.6, counted_days: 14, reliable: false,
      },
    },
    // unrelated selects, which must not be mistaken for the trade mode - one
    // with three options too, listed after it so it would win if it could
    "select.bm_mode": { state: "dynamic", attributes: { options: ["grid_zero", "dynamic"] } },
    "select.bm_zoom": { state: "hour", attributes: { options: ["hour", "quarter", "day"] } },
  };
  return { ...base, ...overrides };
}

function render(overrides, config = {}) {
  nodes.clear();
  const calls = [];
  const card = fakeCard();
  card.setConfig({ ...config });
  card.hass = {
    states: states(overrides),
    callService: (...args) => calls.push(args),
  };
  return { card, calls };
}

// --- finding the entities -------------------------------------------------
{
  const found = findTradeEntities({ states: states() });
  check("finds every entity by what it carries, whatever the language", 
    found.trade === "sensor.bm_slim_handelen_status" &&
    found.trade_mode === "select.bm_slim_handelen" &&
    found.savings_today === "sensor.bm_besparing_vandaag" &&
    found.savings_month === "sensor.bm_besparing_deze_maand" &&
    found.savings_total === "sensor.bm_besparing_sinds_start" &&
    found.payback === "sensor.bm_terugverdientijd",
    found);
  const unavailable = findTradeEntities({
    states: { "sensor.bm_payback": { state: "unavailable", attributes: {} } },
  });
  check("an unavailable payback sensor is still found by its id",
    unavailable.payback === "sensor.bm_payback", unavailable);
  check("the stub config carries them",
    Trade.getStubConfig({ states: states() }).trade === "sensor.bm_slim_handelen_status",
    Trade.getStubConfig({ states: states() }));
}

// --- the sum -------------------------------------------------------------
{
  render();
  check("says what it would do", text("trhead") === "Zou nu verkopen (schaduw)", text("trhead"));
  check("writes the sum out, not only its answer",
    text("trsum") ===
      "Opbrengst €0,437 − terugkopen €0,190 ÷ 0,88 − slijtage €0,030 = €0,191 per kWh (drempel €0,050)",
    text("trsum"));
  check("names saldering and its end", /Saldering tot 1 januari 2027/.test(text("trsal")), text("trsal"));

  const sum = tradeSum({ ...TRADE, export_value_eur_kwh: null });
  check("no sum when an input is missing", sum === null, sum);
}

// --- why not -------------------------------------------------------------
{
  render({
    "sensor.bm_slim_handelen_status": {
      state: "waiting",
      attributes: { ...TRADE, export_value_eur_kwh: null, margin_eur_kwh: null, why: "no_battery_price" },
    },
  });
  check("no purchase price says where to fill it in",
    /Instellen → Slim handelen/.test(text("trwhy")), text("trwhy"));
  check("and prints no half sum", text("trsum") === "", text("trsum"));

  render({
    "sensor.bm_slim_handelen_status": {
      state: "waiting", attributes: { ...TRADE, margin_eur_kwh: 0.02, why: "margin_too_small" },
    },
  });
  check("too little margin is ordinary, not a fault",
    text("trwhy") === TRADE_WHY.margin_too_small && !/Vul|meet/.test(text("trwhy")),
    text("trwhy"));
  check("every reason the coordinator gives has words",
    ["no_battery_price", "no_capacity", "no_export_value", "no_refill_price", "margin_too_small"]
      .every((k) => typeof TRADE_WHY[k] === "string"),
    Object.keys(TRADE_WHY));

  render({
    "sensor.bm_slim_handelen_status": { state: "off", attributes: { ...TRADE, why: "margin_too_small" } },
  });
  check("off shows no sum and no reason",
    text("trhead") === "Uit" && text("trsum") === "" && text("trwhy") === "",
    [text("trhead"), text("trsum"), text("trwhy")]);

  render({
    "sensor.bm_slim_handelen_status": {
      state: "waiting", attributes: { ...TRADE, saldering: false },
    },
  });
  check("after saldering it says so", /Saldering is voorbij/.test(text("trsal")), text("trsal"));
}

// --- the mode buttons ----------------------------------------------------
{
  const { card, calls } = render();
  check("three buttons, the current one marked",
    (html("trmodes").match(/<button/g) || []).length === 3 &&
    /data-option="shadow" class="on"/.test(html("trmodes")),
    html("trmodes"));
  card._onClick({ target: { dataset: { option: "on" } } });
  check("a tap sets the trade mode, on the trade select",
    calls.length === 1 &&
    calls[0][0] === "select" && calls[0][1] === "select_option" &&
    calls[0][2].entity_id === "select.bm_slim_handelen" && calls[0][2].option === "on",
    calls);
  card._onClick({ target: { dataset: {} } });
  check("a tap elsewhere does nothing", calls.length === 1, calls);
}

// --- money ---------------------------------------------------------------
{
  render();
  check("savings in euros, Dutch style",
    text("trtoday") === "€0,42" && text("trmonth") === "€12,50",
    [text("trtoday"), text("trmonth")]);
  check("a loss reads as one", text("trtotal") === "−€3,20", text("trtotal"));
  check("shadow's evening is reported",
    /Schaduw vandaag: zou 6\.7 kWh verkocht hebben, winst €1,28/.test(text("trsold")),
    text("trsold"));
  check("and nothing about real sales that did not happen", !/Verkocht vandaag/.test(text("trsold")),
    text("trsold"));
  check("euro handles nothing", euro(null) === "—" && euro(undefined) === "—", euro(null));
}

// --- payback -------------------------------------------------------------
{
  render();
  check("the owner's question first, with saldering beside it",
    text("trpay") === "Zonder saldering in 10,0 jaar terugverdiend (met saldering 20,5 jaar).",
    text("trpay"));
  check("says how far to trust a fortnight",
    /nog 9,6 jaar te gaan/i.test(text("trpaynote")) && /nog te kort/.test(text("trpaynote")),
    text("trpaynote"));

  const unavailable = paybackSays({ state: "unavailable", attributes: {} });
  check("unavailable says what it needs",
    unavailable.main === "Nog niet te zeggen." && /aanschafprijs/.test(unavailable.note),
    unavailable);
  const never = paybackSays({
    state: "unknown",
    attributes: { known: true, years_without_saldering: null, years_to_go: null, counted_days: 40, reliable: true },
  });
  check("a saving that never repays says so",
    /verdient hij zich met deze besparing niet terug/.test(never.main) && /winter/.test(never.note),
    never);
  const done = paybackSays({
    state: "0", attributes: { known: true, years_without_saldering: 8, years_to_go: 0, counted_days: 400, reliable: true },
  });
  check("repaid is repaid", /Al terugverdiend/.test(done.note), done);
}

// --- nothing configured --------------------------------------------------
{
  nodes.clear();
  const card = fakeCard();
  card.setConfig({});
  card.hass = { states: {}, callService: () => {} };
  check("no entities at all says so rather than throwing",
    text("trhead") === "Slim handelen-sensor niet gevonden" && text("trtoday") === "—",
    [text("trhead"), text("trtoday")]);
}

console.log(fails ? `\n${fails} FAILED` : "\ntrade card checks pass");
process.exit(fails ? 1 : 0);

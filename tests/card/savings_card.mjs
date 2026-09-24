// The savings card: what the packs saved, green or red, and the payback time.
//
// Split off the trading card on the owner's wish - selling belongs beside the
// controls, savings on a dashboard of their own - and coloured on his ask:
// "de cijfers groen als je geld bespaart en rood als je verliest". Nought is
// neither, so a fresh day does not flicker between the two.
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

const { moneyTone, paybackSays } = new Function(
  src + ";return {moneyTone, paybackSays};"
)();
const Savings = _defined.get("battery-management-savings-card");
check("the card is registered", !!Savings, [..._defined.keys()]);
check("and advertised in the picker",
  (globalThis.customCards || []).some((c) => c.type === "battery-management-savings-card"),
  globalThis.customCards);

const nodes = new Map();
function fakeCard() {
  const card = new Savings();
  Object.defineProperty(card, "innerHTML", {
    set(html) { this._html = html; },
    get() { return this._html || ""; },
    configurable: true,
  });
  card.querySelector = (sel) => {
    const id = sel.replace("#", "");
    if (!nodes.has(id)) nodes.set(id, { textContent: "", innerHTML: "", className: "" });
    return nodes.get(id);
  };
  return card;
}
const node = (id) => nodes.get(id) || { textContent: "", className: "" };

function states({ today = "0.42", month = "12.5", total = "-3.2", saldering = true, payback } = {}) {
  return {
    "sensor.bm_besparing_vandaag": {
      state: today, attributes: { period: "day", saved_actual_eur: parseFloat(today) },
    },
    "sensor.bm_besparing_deze_maand": {
      state: month, attributes: { period: "month", saved_actual_eur: parseFloat(month) },
    },
    "sensor.bm_besparing_sinds_start": {
      state: total,
      attributes: { saved_actual_eur: parseFloat(total), saldering, saldering_until: "2027-01-01" },
    },
    "sensor.bm_terugverdientijd": payback || {
      state: "10.0",
      attributes: {
        known: true, years_without_saldering: 10.0, years_with_saldering: 20.5,
        years_to_go: 9.6, counted_days: 14, reliable: false,
      },
    },
  };
}

function render(opts) {
  nodes.clear();
  const card = fakeCard();
  card.setConfig({});
  card.hass = { states: states(opts), callService: () => {} };
  return card;
}

// --- the figures and their colours ------------------------------------------
{
  render();
  check("euros, Dutch style",
    node("svtoday").textContent === "€0,42" && node("svmonth").textContent === "€12,50",
    [node("svtoday").textContent, node("svmonth").textContent]);
  check("a saving is green", /\bgain\b/.test(node("svtoday").className), node("svtoday").className);
  check("a loss reads as one, and is red",
    node("svtotal").textContent === "−€3,20" && /\bloss\b/.test(node("svtotal").className),
    [node("svtotal").textContent, node("svtotal").className]);

  render({ today: "0.001" });
  check("nought is neither colour", !/gain|loss/.test(node("svtoday").className), node("svtoday").className);

  render({ today: "-0.03" });
  check("the morning's stored sun is a (small) loss, in red",
    /\bloss\b/.test(node("svtoday").className), node("svtoday").className);

  check("the tone rule itself",
    moneyTone(1) === "gain" && moneyTone(-1) === "loss" && moneyTone(0) === "" &&
    moneyTone(0.004) === "" && moneyTone(-0.004) === "" && moneyTone(NaN) === "" &&
    moneyTone(null) === "",
    [moneyTone(1), moneyTone(-1), moneyTone(0.004)]);

  const card = render();
  check("the colours are Home Assistant's own, with a fallback",
    /\.gain \{ color: var\(--success-color/.test(card.innerHTML) &&
    /\.loss \{ color: var\(--error-color/.test(card.innerHTML),
    card.innerHTML.length);
}

// --- which footing ----------------------------------------------------------
{
  render({ saldering: true });
  check("says it counts with saldering", /met saldering/.test(node("svfooting").textContent),
    node("svfooting").textContent);
  render({ saldering: false });
  check("and without, once it has ended", /zonder saldering/.test(node("svfooting").textContent),
    node("svfooting").textContent);
}

// --- payback ----------------------------------------------------------------
{
  render();
  check("the owner's question first, with saldering beside it",
    node("svpay").textContent === "Zonder saldering in 10,0 jaar terugverdiend (met saldering 20,5 jaar).",
    node("svpay").textContent);
  check("says how far to trust a fortnight",
    /nog 9,6 jaar te gaan/i.test(node("svpaynote").textContent) &&
    /nog te kort/.test(node("svpaynote").textContent),
    node("svpaynote").textContent);

  render({
    payback: {
      state: "unknown",
      attributes: { known: false, battery_price_eur: 0, counted_days: 3, years_to_go: null },
    },
  });
  check("no purchase price says where to fill it in",
    node("svpay").textContent === "Nog niet te zeggen." &&
    /Instellen → Slim handelen/.test(node("svpaynote").textContent),
    node("svpaynote").textContent);

  render({
    payback: {
      state: "unknown",
      attributes: { known: false, battery_price_eur: 8500, counted_days: 0.08, years_to_go: null },
    },
  });
  check("too little measured says how far along it is",
    node("svpaynote").textContent === "Eerst een dag meten: nu 2 van de 24 uur gemeten.",
    node("svpaynote").textContent);

  render({ payback: { state: "unavailable", attributes: {} } });
  check("an older install, still unavailable, says both",
    node("svpay").textContent === "Nog niet te zeggen." && /aanschafprijs/.test(node("svpaynote").textContent),
    [node("svpay").textContent, node("svpaynote").textContent]);

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

// --- config -----------------------------------------------------------------
{
  const stub = Savings.getStubConfig({ states: states() });
  check("the stub config names the savings and payback sensors only",
    stub.savings_today === "sensor.bm_besparing_vandaag" &&
    stub.payback === "sensor.bm_terugverdientijd" && !("trade" in stub) && !("trade_mode" in stub),
    stub);

  nodes.clear();
  const card = fakeCard();
  card.setConfig({});
  card.hass = { states: {}, callService: () => {} };
  check("no entities at all shows dashes rather than throwing",
    node("svtoday").textContent === "—" && !/gain|loss/.test(node("svtoday").className),
    node("svtoday"));
}

console.log(fails ? `\n${fails} FAILED` : "\nsavings card checks pass");
process.exit(fails ? 1 : 0);

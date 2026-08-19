// Looking back a day, and forward to tomorrow.
//
// Forward is free: the plan sensor already carries every slot the supplier has
// released. Backward has to be fetched, and it is fetched from long-term
// statistics rather than raw history, because the recorder purges after ten
// days and statistics do not.
//
// The rule that matters most here is what history is *not* allowed to say.
// Green on this chart means "this is where the coordinator buys" - decided at
// the time, against the ranking published at the time, and recorded nowhere.
// Re-colouring yesterday with today's ranking would draw decisions that were
// never taken, so history is grey. That is asserted, not assumed.
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

class FakeEl {
  constructor() {
    this.listeners = {};
    this.style = {};
    this.innerHTML = "";
    this.textContent = "";
    this.value = "";
    this.dataset = {};
    this.classes = new Set();
    this.classList = {
      toggle: (name, on) => (on ? this.classes.add(name) : this.classes.delete(name)),
    };
  }
  addEventListener(type, fn) {
    (this.listeners[type] = this.listeners[type] || []).push(fn);
  }
  appendChild() {}
}
globalThis.document = { createElement: () => new FakeEl() };

const [, Prices, helpers] = new Function(
  src +
    ";return [BatteryManagementCard, BatteryManagementPricesCard, " +
    "{dayOf, dayKey, dayRange, slotsOnDay, historySlots, dayLabel, dayAverage}];"
)();

const { dayOf, dayKey, dayRange, slotsOnDay, historySlots, dayLabel, dayAverage } =
  helpers;

// ------------------------------------------------------------ the calendar
// Frank publishes in UTC. A slot at 23:30 UTC is already the next day here,
// and slicing the string would have filed it under the wrong one - the same
// mistake the axis labels made before clock.mjs pinned them.
check("a UTC slot is filed under the reader's own day",
  dayOf("2026-08-18T23:30:00Z") === "2026-08-19", dayOf("2026-08-18T23:30:00Z"));
check("and one before the switch is not",
  dayOf("2026-08-18T21:30:00Z") === "2026-08-18", dayOf("2026-08-18T21:30:00Z"));
check("rubbish in, nothing out", dayOf("not a date") === "", dayOf("not a date"));

const NOON = new Date(2026, 7, 19, 12, 0, 0).getTime();
check("today", dayKey(0, NOON) === "2026-08-19", dayKey(0, NOON));
check("tomorrow", dayKey(1, NOON) === "2026-08-20", dayKey(1, NOON));
check("yesterday", dayKey(-1, NOON) === "2026-08-18", dayKey(-1, NOON));
// crossing a month is where hand-rolled date arithmetic usually breaks
check("and it survives a month boundary",
  dayKey(1, new Date(2026, 7, 31, 12).getTime()) === "2026-09-01",
  dayKey(1, new Date(2026, 7, 31, 12).getTime()));

const range = dayRange("2026-08-19");
check("a day runs local midnight to local midnight",
  dayOf(range.start) === "2026-08-19" && dayOf(range.end) === "2026-08-20", range);

check("labels read as words where words exist",
  dayLabel("2026-08-19", NOON) === "vandaag" &&
  dayLabel("2026-08-20", NOON) === "morgen" &&
  dayLabel("2026-08-18", NOON) === "gisteren",
  [dayLabel("2026-08-19", NOON), dayLabel("2026-08-20", NOON)]);
check("and as a date where they do not",
  /aug/.test(dayLabel("2026-08-15", NOON)), dayLabel("2026-08-15", NOON));

// --------------------------------------------------------------- splitting
const published = [
  { start: "2026-08-19T09:00:00Z", end: "2026-08-19T10:00:00Z", price: 0.3, role: "normal" },
  { start: "2026-08-19T22:00:00Z", end: "2026-08-19T23:00:00Z", price: 0.2, role: "cheap" },
  { start: "2026-08-19T23:00:00Z", end: "2026-08-20T00:00:00Z", price: 0.1, role: "cheap" },
];
// In August, Amsterdam is two hours ahead: 22:00Z is already midnight here,
// so two of these three belong to the *next* day. Writing this expectation the
// other way round first is exactly the mistake the split exists to prevent.
check("slots are split by the reader's calendar, not the publisher's",
  slotsOnDay(published, "2026-08-19").length === 1 &&
  slotsOnDay(published, "2026-08-20").length === 2,
  [slotsOnDay(published, "2026-08-19").length, slotsOnDay(published, "2026-08-20").length]);

// --------------------------------------------------------------- the past
const stats = [
  { start: Date.UTC(2026, 7, 18, 6), end: Date.UTC(2026, 7, 18, 7), mean: 0.25 },
  { start: Date.UTC(2026, 7, 18, 7), end: Date.UTC(2026, 7, 18, 8), mean: 0.31 },
];
const past = historySlots(stats);
check("statistics become slots the chart can draw",
  past.length === 2 && past[0].price === 0.25, past);
check("history is grey, never coloured",
  past.every((h) => h.role === "past"), past.map((h) => h.role));
check("a missing end is assumed to be an hour",
  historySlots([{ start: Date.UTC(2026, 7, 18, 6), mean: 0.2 }])[0].end ===
    new Date(Date.UTC(2026, 7, 18, 7)).toISOString(),
  historySlots([{ start: Date.UTC(2026, 7, 18, 6), mean: 0.2 }])[0]);
check("older cores hand back ISO strings, and that works too",
  historySlots([{ start: "2026-08-18T06:00:00Z", mean: 0.2 }])[0].price === 0.2,
  historySlots([{ start: "2026-08-18T06:00:00Z", mean: 0.2 }]));
// an hour the recorder has no mean for is a hole, not a zero
check("a gap is dropped rather than drawn as free electricity",
  historySlots([{ start: Date.UTC(2026, 7, 18, 6), mean: null }]).length === 0,
  historySlots([{ start: Date.UTC(2026, 7, 18, 6), mean: null }]));
check("nothing at all is not a crash", historySlots(undefined).length === 0, []);

check("the average is what a finished day is about",
  Math.abs(dayAverage(past) - 0.28) < 1e-9, dayAverage(past));
check("and an empty day has none", dayAverage([]) === null, dayAverage([]));

// ------------------------------------------------------- the card, driving
const PLAN = "sensor.bm_plan";
const PRICE = "sensor.bm_current_price";

function build(hours, { asked = [], answer = null, ws = true } = {}) {
  const els = new Map();
  const card = new Prices();
  card.querySelector = (sel) => {
    const key = String(sel).replace(/^#/, "");
    if (!els.has(key)) els.set(key, new FakeEl());
    return els.get(key);
  };
  card.setConfig({ prices: PLAN });
  card._els = els;
  const hass = {
    states: {
      [PLAN]: { state: "ok", attributes: { hours } },
      [PRICE]: { state: "0.3", attributes: {} },
    },
    entities: { [PLAN]: { device_id: "d" }, [PRICE]: { device_id: "d" } },
  };
  if (ws) {
    hass.callWS = async (msg) => {
      asked.push(msg);
      return answer === null ? { [PRICE]: [] } : answer;
    };
  }
  card.hass = hass;
  return card;
}

const today = dayKey(0);
const todayHours = Array.from({ length: 24 }, (_, i) => {
  const start = new Date(`${today}T00:00:00`);
  start.setHours(i);
  const end = new Date(start.getTime() + 3600000);
  return {
    start: start.toISOString(),
    end: end.toISOString(),
    price: 0.2 + i / 100,
    role: "normal",
  };
});

let card = build(todayHours);
check("it opens on today", card._day === today, card._day);
check("and says so", card._els.get("pday").textContent === "vandaag",
  card._els.get("pday").textContent);
check("with no day left to go forward to",
  card._els.get("pnext").classes.has("off"), [...card._els.get("pnext").classes]);

// stepping back has to actually ask the recorder, for the right window
const asked = [];
card = build(todayHours, { asked });
card._els.get("pprev").listeners.click[0]();
check("stepping back moves the label", card._els.get("pday").textContent === "gisteren",
  card._els.get("pday").textContent);
check("and asks the recorder for that day", asked.length === 1, asked);
check("for hourly means of the price sensor",
  asked[0] && asked[0].type === "recorder/statistics_during_period" &&
  asked[0].period === "hour" && asked[0].statistic_ids[0] === PRICE,
  asked[0]);
check("over exactly that day",
  dayOf(asked[0].start_time) === dayKey(-1) && dayOf(asked[0].end_time) === today,
  [asked[0].start_time, asked[0].end_time]);

// and the forward arrow opens up again once there is somewhere to go
check("forward is available again once looking at the past",
  !card._els.get("pnext").classes.has("off"), [...card._els.get("pnext").classes]);

// the label is the way home
card._els.get("pday").listeners.click[0]();
check("tapping the label returns to today", card._day === today, card._day);

// a tapped bar belongs to the day it was tapped on
card._picked = 5;
card._els.get("pprev").listeners.click[0]();
check("changing day clears the tapped bar", card._picked === null, card._picked);

// one request per day, however often the card redraws
const twice = [];
card = build(todayHours, { asked: twice });
card._els.get("pprev").listeners.click[0]();
card.hass = card._hass;
card.hass = card._hass;
check("a day is asked for once, not once per redraw", twice.length === 1, twice.length);

// no recorder at all must not turn into a request per tick
const none = build(todayHours, { ws: false });
none._els.get("pprev").listeners.click[0]();
check("a frontend without callWS degrades quietly",
  none._els.get("psub").textContent.includes("Geen prijzen bewaard"),
  none._els.get("psub").textContent);

console.log(fails ? `\n${fails} FAILED` : "\nhistory checks pass");
process.exit(fails ? 1 : 0);

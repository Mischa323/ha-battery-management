/**
 * Battery Management management card.
 * Vanilla web component (no build step). Ships with the integration and is
 * auto-registered, so `type: custom:battery-management-card` just works.
 */

/**
 * When this file ran, and as what. Measured, not assumed.
 *
 * Four fixes were shipped at this problem on reasoning alone and none of them
 * landed, so the file now reports its own circumstances instead. Two facts
 * decide almost everything:
 *
 * - `document.currentScript` is an element inside a classic script and null
 *   inside a module. That is the difference between running *during* parsing
 *   and running after it, and Lovelace builds its cards in between.
 * - `document.readyState` says how far the page had got. "loading" means this
 *   ran early enough by construction; "interactive" or "complete" means it
 *   cannot have been.
 */
const BOOT = {
  at: typeof performance !== "undefined" ? Math.round(performance.now()) : -1,
  readyState: typeof document !== "undefined" ? document.readyState : "?",
  loadedAs:
    typeof document !== "undefined" && document.currentScript ? "script" : "module",
  src:
    typeof document !== "undefined" && document.currentScript
      ? document.currentScript.src
      : "",
};

const FILL = {
  charging: "var(--info-color, #039be5)",
  discharging: "var(--warning-color, #ff9800)",
  fast_charge: "var(--info-color, #039be5)",
  // a coordinator that has stopped steering must not look like a calm one
  degraded: "var(--error-color, #f44336)",
  off: "var(--secondary-text-color)",
};

/** Config values end up in innerHTML, so keep markup out of them. */
const esc = (value) =>
  String(value ?? "").replace(
    /[&<>"']/g,
    (ch) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[ch]
  );

/** The three decisions a price slot can belong to, and how they are drawn. */
const PRICE_COLOUR = {
  cheap: "#089408",
  dear: "#e07070",
  normal: "var(--disabled-text-color, #8a8a8a)",
  // an hour that has gone and has no recorded verdict - the integration was
  // not running during it, or it comes from the recorder rather than the plan
  past: "var(--disabled-text-color, #8a8a8a)",
};
// The hours the charging is actually aimed at, drawn as a ring around the bar
// rather than a fourth fill colour. Two reasons. The plan is a *subset* of the
// cheap band, so it has to sit on top of green rather than replace it - a bar
// cannot be two fills at once. And green/red already carries the band, which
// is the worst pair for deuteranopia; blue is the one hue that stays distinct
// from both under every common CVD, so the third channel does not need hue to
// be told apart in the first place - it is a ring, and nothing else is.
const PRICE_BUY_RING = "#3b82f6";
// Both bands are prices now, and neither says what the packs will do - that is
// the ring's job. "cheap" said "goedkoop genoeg om te kopen" while it was
// ranked over a rolling window and meant "an hour we would buy on"; ranked over
// the calendar day it simply names the cheapest hours of today, and wording it
// as an offer would promise something three other conditions still gate.
// "dear" lost its claim earlier the same day, when the discharge hold went.
const PRICE_SAYS = {
  cheap: "bij de goedkoopste uren van vandaag",
  dear: "duur uur",
  normal: "gewoon de meter volgen",
  past: "geweest",
};
// The same in the past tense. The colour survives the hour because a price
// does, but the wording must not: "de goedkoopste uren van vandaag" reads as
// something still on offer when it is said about 09:00 at teatime.
const PRICE_WAS = {
  cheap: "was een van de goedkoopste uren",
  dear: "was een duur uur",
  normal: "gewoon de meter gevolgd",
  past: "geweest",
};

/** What one bar says: its hour, its price, its verdict, and whether we bought.
 *
 * Three facts, strongest last and only one of them shown. "bought" is the grid
 * actually being paid and only ever appears on an hour that has happened, so
 * it can never read as a promise. "buy" is an intention and is worded as one -
 * the pack still has to be low enough when the hour comes round, and the sun
 * may have made it moot by then.
 */
function planSays(item, past) {
  if (item.bought) return " — toen geladen";
  if (!item.buy) return "";
  return past ? " — hier zou hij laden" : " — hier gaat hij laden";
}

function slotLabel(bar) {
  const says = (bar.past ? PRICE_WAS : PRICE_SAYS)[bar.role] || "";
  return (
    `${hhmm(bar.start)} — ${bar.price.toFixed(3)} €/kWh — ${says}` +
    planSays(bar, bar.past)
  );
}

/** Chart styles, shared by both cards. */
const PRICE_CSS = `
          .phead { display:flex; justify-content:space-between; align-items:baseline;
                   font-size:.92em; margin-bottom:8px; }
          .plot { display:flex; align-items:flex-end; gap:2px; height:96px; position:relative; }
          .zero { position:absolute; left:0; right:0; height:1px; background: var(--divider-color); }
          .slot { flex:1 1 0; height:100%; position:relative; min-width:0; }
          .pbar { position:absolute; left:0; right:0; }
          .pbar.up { border-radius:4px 4px 0 0; }
          .pbar.down { border-radius:0 0 4px 4px; }
  .pbar.past { opacity:.35; }
          .slot { cursor:pointer; touch-action:manipulation; }
  .slot.now .pbar { outline:2px solid var(--primary-text-color); outline-offset:1px; }
  /* dashed, so a tapped bar never gets mistaken for the current one */
  .slot.picked .pbar { outline:2px dashed var(--primary-text-color); outline-offset:1px; }
  /* box-shadow and not outline: "now" and "picked" have both already claimed
     the outline, and a planned hour can be either of those at the same time.
     A ring drawn this way sits inside theirs instead of overwriting it. */
  .pbar.buy { box-shadow:0 0 0 2px ${PRICE_BUY_RING}; }
          .paxis { display:flex; gap:2px; margin-top:4px; font-size:.72em;
                   color: var(--secondary-text-color); }
          .paxis span { flex:1 1 0; text-align:center; min-width:0; }
          .legend { display:flex; flex-wrap:wrap; gap:12px; margin-top:8px; font-size:.78em;
                    color: var(--secondary-text-color); }
          .legend i { display:inline-block; width:10px; height:10px; border-radius:3px;
                      margin-right:5px; vertical-align:middle; font-style:normal; }
          .pnav { display:flex; align-items:center; justify-content:center; gap:14px;
                  margin-top:8px; font-size:.86em; }
          .pnav .pbtn { cursor:pointer; user-select:none; padding:1px 10px 3px;
                        border-radius:8px; touch-action:manipulation;
                        background: var(--secondary-background-color);
                        color: var(--primary-text-color);
                        border:1px solid var(--divider-color); }
          .pnav .pbtn.off { opacity:.3; cursor:default; }
          .pnav .pday { min-width:8.5em; text-align:center; cursor:pointer;
                        color: var(--secondary-text-color); }
`;

/** The day picker, shared by both charts. */
const PRICE_NAV = `
            <div class="pnav">
              <span class="pbtn" id="pprev" title="dag terug">‹</span>
              <span class="pday" id="pday" title="terug naar vandaag"></span>
              <span class="pbtn" id="pnext" title="dag verder">›</span>
            </div>`;

const PRICE_LEGEND = `
            <div class="legend">
              <span><i style="background:${PRICE_COLOUR.cheap}"></i>Goedkoopste uren van vandaag</span>
              <span><i style="background:transparent;box-shadow:inset 0 0 0 2px ${PRICE_BUY_RING}"></i>Hier gaat hij laden</span>
              <span><i style="background:${PRICE_COLOUR.dear}"></i>Duur uur</span>
              <span><i style="background:${PRICE_COLOUR.normal}"></i>Verder de meter volgen</span>
              <span><i style="background:${PRICE_COLOUR.cheap};opacity:.35"></i>Geweest (vervaagd, kleur blijft)</span>
            </div>`;

/**
 * A published timestamp, shown in the reader's own clock.
 *
 * Slicing the ISO string was two hours wrong for half of Europe: Frank
 * publishes in UTC, so midnight arrived on the chart labelled 22:00 and the
 * evening peak looked like an afternoon one. Parse it, then format it.
 */
/**
 * Every entity we could refer to.
 *
 * Home Assistant hands `getStubConfig` the entities *not already used* on the
 * dashboard, which is not the same thing at all: once the Plan sensor is on a
 * card, it disappears from that list and a second card can no longer find it.
 * The suggestion is a fine starting point, so it goes first, with everything
 * Home Assistant knows about behind it.
 */
function knownEntities(hass, entities) {
  const suggested = Array.isArray(entities) ? entities : [];
  const everything = Object.keys((hass && hass.states) || {});
  return [...suggested, ...everything.filter((id) => !suggested.includes(id))];
}

/**
 * A state, or one particular option of it, in the reader's own language.
 *
 * The integration already ships nl/en translations for every mode and every
 * policy. Repeating them here would give the card a second vocabulary that
 * drifts from the entity's the first time one is reworded, so this asks Home
 * Assistant's own resolver instead. It takes an explicit value, which is what
 * lets the dropdown label the options the coordinator is *not* currently in.
 *
 * Older frontends have no such method — then the raw key, tidied up. Ugly, but
 * never wrong, and the raw key is what the documentation calls it anyway.
 */
function stateLabel(hass, stateObj, value) {
  const raw = value === undefined ? stateObj && stateObj.state : value;
  if (raw === undefined || raw === null || raw === "") return "";
  if (stateObj && typeof hass.formatEntityState === "function") {
    try {
      const out = hass.formatEntityState(stateObj, value);
      if (out) return out;
    } catch (err) {
      /* fall through to the raw key */
    }
  }
  return String(raw).replace(/_/g, " ").replace(/^./, (ch) => ch.toUpperCase());
}

/**
 * The entity-id suffixes for each length of the charge split.
 *
 * Written out rather than built from the period name, so the ids the card
 * looks for cannot move if the period keys are ever reworded - and so this
 * file and `const.py` can be diffed against each other by eye. Mirrors
 * `PERIOD_SUFFIX` there.
 */
const CHARGE_SUFFIX = {
  day: ["_charged_today", "_charged_from_grid_today"],
  week: ["_charged_this_week", "_charged_from_grid_this_week"],
  month: ["_charged_this_month", "_charged_from_grid_this_month"],
};
const DEFAULT_PERIOD = "month";

/**
 * How much of the charge came off the roof rather than off the meter.
 *
 * The sun is the *remainder*, not a third measurement, so the two halves add
 * up to the total that really went in. Two independent sensors would drift
 * apart within a day and the card would then be splitting something that is
 * not the whole.
 *
 * Both inputs are measured by the packs themselves and integrated by Home
 * Assistant — never derived from what this integration commanded. That
 * distinction is the whole point: a command is a plan, and the packs answer
 * 10–30 s later, so integrating our own orders would produce an
 * authoritative-looking number that is not what happened.
 *
 * One figure without the other is still shown, but it gets no split: knowing
 * only what came off the meter says nothing about the share, and "0 % from the
 * sun" is a far worse answer than no answer.
 */
function chargeSplit(total, grid) {
  if (total === null && grid === null) return null;
  if (total === null || grid === null) {
    return { total, grid, solar: null, share: null };
  }
  // two meters that never quite agree can put grid fractionally over the
  // total; the split has to remain a split, so it is capped rather than
  // allowed to render a negative sun
  const net = Math.min(Math.max(grid, 0), Math.max(total, 0));
  const solar = Math.max(total - net, 0);
  return {
    total,
    grid: net,
    solar,
    share: total > 0 ? (solar / total) * 100 : null,
  };
}

/**
 * Kilowatt-hours, at a precision that does not lie about small ones.
 *
 * One decimal renders a freshly-installed counter at 0.01 kWh as "0.0 kWh",
 * three times over, which reads as broken rather than as new - and new is
 * exactly what it will be for everyone the first time they switch this on.
 * Below a kilowatt-hour the second decimal is the whole number.
 */
const kwh = (value) => {
  if (value === null || value === undefined) return "—";
  return `${value.toFixed(Math.abs(value) < 1 ? 2 : 1)} kWh`;
};

const hhmm = (iso) => {
  const at = new Date(iso);
  if (isNaN(at.getTime())) return String(iso).slice(11, 16);
  return `${String(at.getHours()).padStart(2, "0")}:${String(
    at.getMinutes()
  ).padStart(2, "0")}`;
};

/** Comparing ISO strings as text only works while every one carries the same
 *  offset. A sensor publishing +02:00 alongside our own Z would sort wrong, so
 *  moments are compared as moments. */
const at = (iso) => new Date(iso).getTime();
const covers = (slot, moment) => at(slot.start) <= moment && moment < at(slot.end);

/**
 * A sibling entity: same integration, same device, known suffix.
 *
 * Asks the entity registry which device the anchor is on, rather than reading
 * a name off it. A slug is a snapshot of what the device was called when that
 * particular entity was created, so entities from before a rename keep the old
 * one while later ones get the new - one install, two prefixes, and half of it
 * unreachable by name. That cost four rounds of "not found" on an install
 * whose counters were working the whole time.
 *
 * The name-derived route stays as a fallback: older frontends expose no
 * registry, and an install that was never renamed resolves there fine. A
 * registry entry without a state is skipped, so a disabled entity is never
 * handed back as though it could be read.
 */
function resolveRelated(hass, anchor, anchorSuffix, suffix, domain = "sensor") {
  if (!hass || !anchor || !anchor.endsWith(anchorSuffix)) return undefined;
  // A plain string, not a template literal, and [0-9] rather than a backslash
  // escape: the backslash did not survive being written into a source file,
  // twice, and a template literal ate it silently rather than complaining.
  const tail = new RegExp(suffix + "(_[0-9]+)?$");

  const reg = hass.entities;
  const device = reg && reg[anchor] && reg[anchor].device_id;
  if (device) {
    const onDevice = Object.keys(reg)
      .filter(
        (id) =>
          id.startsWith(domain + ".") &&
          reg[id].device_id === device &&
          tail.test(id) &&
          hass.states[id]
      )
      .sort((a, b) => a.length - b.length)[0];
    if (onDevice) return onDevice;
  }

  const prefix = anchor.slice(anchor.indexOf(".") + 1, -anchorSuffix.length);
  const exact = `${domain}.${prefix}${suffix}`;
  if (hass.states[exact]) return exact;
  // Home Assistant appends _2 when the id it wants is already taken.
  const head = `${domain}.${prefix}`;
  return Object.keys(hass.states)
    .filter((id) => id.startsWith(head) && tail.test(id))
    .sort((a, b) => a.length - b.length)[0];
}

/** A moment's calendar day in the reader's own zone, as YYYY-MM-DD. */
const localDay = (moment) => {
  const pad = (n) => String(n).padStart(2, "0");
  return `${moment.getFullYear()}-${pad(moment.getMonth() + 1)}-${pad(
    moment.getDate()
  )}`;
};

const dayOf = (iso) => {
  const moment = new Date(iso);
  return isNaN(moment.getTime()) ? "" : localDay(moment);
};

/** The day `offset` days from now, same format. */
const dayKey = (offset, now = Date.now()) => {
  const moment = new Date(now);
  moment.setDate(moment.getDate() + offset);
  return localDay(moment);
};

/** Local midnight to local midnight, which is what a reader means by a day. */
function dayRange(day) {
  const [y, m, d] = String(day).split("-").map(Number);
  return {
    start: new Date(y, m - 1, d).toISOString(),
    end: new Date(y, m - 1, d + 1).toISOString(),
  };
}

/** Only the published slots falling on one calendar day. */
const slotsOnDay = (hours, day) =>
  (hours || []).filter((h) => h && dayOf(h.start) === day);

/**
 * Recorder statistics, in the shape the chart already draws.
 *
 * Deliberately **without a role**, and that is the thing not to get clever
 * about. Green here does not mean "a low price", it means "this is where the
 * coordinator buys" - a decision taken at the time, against the ranking
 * published at the time, and recorded nowhere. Re-colouring yesterday with
 * today's ranking would draw decisions that were never taken. Grey is honest,
 * and it is the same grey today's spent hours already get.
 *
 * `start` arrives as epoch milliseconds on current cores and as an ISO string
 * on older ones, and `end` is sometimes absent. Normalised here so nothing
 * downstream has to know which core it is talking to.
 */
function historySlots(rows) {
  return (rows || [])
    .filter((r) => r && r.mean !== null && r.mean !== undefined && r.start != null)
    .map((r) => {
      const start = new Date(r.start);
      const end =
        r.end != null ? new Date(r.end) : new Date(start.getTime() + 3600000);
      return {
        start: start.toISOString(),
        end: end.toISOString(),
        price: Number(r.mean),
        role: "past",
      };
    })
    .filter((h) => !isNaN(at(h.start)) && !isNaN(h.price))
    .sort((a, b) => at(a.start) - at(b.start));
}

/** "vandaag", "morgen", "gisteren", or the date spelled out. */
function dayLabel(day, now = Date.now()) {
  for (const [offset, word] of [[0, "vandaag"], [1, "morgen"], [-1, "gisteren"]]) {
    if (day === dayKey(offset, now)) return word;
  }
  const [y, m, d] = String(day).split("-").map(Number);
  return new Date(y, m - 1, d).toLocaleDateString("nl-NL", {
    weekday: "short",
    day: "numeric",
    month: "short",
  });
}

/**
 * Bar geometry. The baseline is zero and negative prices hang below it rather
 * than being clipped: on a dynamic tariff they are real, and they are exactly
 * the hours worth noticing.
 */
function priceBars(hours) {
  const prices = hours.map((h) => Number(h.price) || 0);
  const top = Math.max(0, ...prices);
  const bottom = Math.min(0, ...prices);
  const span = top - bottom || 1;
  const zero = ((0 - bottom) / span) * 100;
  const now = Date.now();
  return {
    zero,
    bars: hours.map((h) => {
      const price = Number(h.price) || 0;
      const size = (Math.abs(price) / span) * 100;
      const role = PRICE_COLOUR[h.role] ? h.role : "normal";
      // "past" is now a flag rather than a role, because an hour that has gone
      // keeps the verdict it was given at the time. It is drawn faded, not
      // grey: the shape of the day and the hours that were bought on are both
      // worth seeing, and only one of them survives being greyed out.
      const past = h.past === true || role === "past" || at(h.end) <= now;
      const bar = {
        role,
        past,
        buy: h.buy === true,
        bought: h.bought === true,
        start: h.start,
        price,
        bottom: zero + (price < 0 ? -size : 0),
        height: size,
        down: price < 0,
        current: covers(h, now),
      };
      bar.label = slotLabel(bar);
      return bar;
    }),
  };
}

/** Fill a plot and its axis from the plan's `hours`. */
function drawPrices(plot, axis, hours, picked) {
  const { zero, bars } = priceBars(hours);
  // a quarter-hourly feed is 96 bars; a 2 px gap between them would be most of
  // the chart, so the surface separator gives way once they get thin
  plot.style.gap = hours.length > 48 ? "1px" : "2px";
  plot.innerHTML =
    `<div class="zero" style="bottom:${zero}%"></div>` +
    bars
      .map(
        (b, i) =>
          `<div class="slot${b.current ? " now" : ""}` +
          `${i === picked ? " picked" : ""}" data-i="${i}" title="${esc(b.label)}">` +
          `<div class="pbar ${b.down ? "down" : "up"}${b.past ? " past" : ""}` +
          `${b.buy ? " buy" : ""}" ` +
          `style="bottom:${b.bottom}%;height:${b.height}%;` +
          `background:${PRICE_COLOUR[b.role]}"></div></div>`
      )
      .join("");
  if (!axis) return;
  // a label every few hours, not one on every bar
  const every = Math.max(1, Math.round(hours.length / 6));
  axis.innerHTML = hours
    .map((h, i) => `<span>${i % every === 0 ? hhmm(h.start) : ""}</span>`)
    .join("");
}

/**
 * Make the bars answer to a tap, not only to a hover.
 *
 * The `title` attribute is a desktop-only affordance: a phone has no hover, so
 * on a phone the chart could be looked at but not read - and a phone is where
 * it is mostly looked at. Tapping a bar moves the readout above the chart to
 * that slot, tapping it again goes back to now.
 *
 * Shared by both cards deliberately. The prices card had the CSS for a picked
 * bar, the `cursor: pointer`, and even printed "tik nogmaals voor nu" to the
 * reader - with nothing listening for the tap. Wiring it in one place is what
 * stops the two drifting apart again.
 */
function wirePlot(card) {
  const plot = card.querySelector("#plot");
  if (!plot) return;
  plot.addEventListener("click", (event) => {
    const slot = event.target.closest(".slot");
    if (!slot) return;
    const index = Number(slot.dataset.i);
    card._picked = card._picked === index ? null : index;
    card._update();
  });
}

/**
 * Move the chart a day at a time, and back to today on the label.
 *
 * Shared, because both charts need it and the last time one card got the
 * wiring and the other did not, the second was decorative for a fortnight
 * before anyone noticed - it had every affordance except the listener.
 */
function wireNav(card) {
  const step = (delta) => {
    const [y, m, d] = (card._day || dayKey(0)).split("-").map(Number);
    card._day = localDay(new Date(y, m - 1, d + delta));
    // a tapped bar belongs to the day it was tapped on
    card._picked = null;
    card._update();
  };
  const on = (id, fn) => {
    const el = card.querySelector(id);
    if (el) el.addEventListener("click", fn);
  };
  on("#pprev", () => step(-1));
  on("#pnext", () => step(1));
  on("#pday", () => {
    card._day = dayKey(0);
    card._picked = null;
    card._update();
  });
}

/** Which day is on show, and whether there is a later one to go to. */
function renderNav(card, hours) {
  const label = card.querySelector("#pday");
  if (!label) return;
  const day = card._day || (card._day = dayKey(0));
  label.textContent = dayLabel(day);
  const next = card.querySelector("#pnext");
  if (!next) return;
  // forward only as far as the supplier has published; back is unbounded,
  // because that is a question for the recorder rather than for the feed
  const published = (hours || []).map((h) => dayOf(h.start)).sort();
  const last = published[published.length - 1] || dayKey(0);
  next.classList.toggle("off", day >= last);
}

/**
 * The slots to draw: published if the day is covered, else the recorder.
 *
 * Forward is free - the plan sensor already carries every slot the supplier
 * has released, today and usually tomorrow. Backward has to be asked for, and
 * `sensor.…_current_price` is the one to ask about: it carries a state class,
 * so Home Assistant keeps long-term statistics of it, and those survive the
 * recorder's purge. Raw history would only reach back ten days.
 */
function chartSlots(card, hours) {
  const day = card._day || (card._day = dayKey(0));
  const published = slotsOnDay(hours, day);
  if (published.length) return published;
  const cached = (card._history || {})[day];
  if (cached) return cached;
  fetchDay(card, day);
  return [];
}

/** Ask the recorder for one day of hourly means, once. */
async function fetchDay(card, day) {
  card._history = card._history || {};
  card._fetching = card._fetching || {};
  if (card._fetching[day] || card._history[day]) return;

  const hass = card._hass;
  const entity = resolveRelated(
    hass,
    card._config && card._config.prices,
    "_plan",
    "_current_price"
  );
  if (!hass || !entity || typeof hass.callWS !== "function") {
    // nothing to ask, or nobody to ask: remember that, so a reader flicking
    // through days does not fire a request per tick for the rest of the day
    card._history[day] = [];
    return;
  }

  card._fetching[day] = true;
  const window = dayRange(day);
  try {
    const answer = await hass.callWS({
      type: "recorder/statistics_during_period",
      start_time: window.start,
      end_time: window.end,
      statistic_ids: [entity],
      // hourly on purpose: a quarter-hourly feed is 96 bars a day, which is
      // unreadable at this width and tells you nothing a mean does not
      period: "hour",
      types: ["mean"],
    });
    card._history[day] = historySlots(answer && answer[entity]);
  } catch (err) {
    card._history[day] = [];
  }
  delete card._fetching[day];
  card._update();
}

/** The plain average of a day, which is the number a past day is about. */
function dayAverage(slots) {
  const values = (slots || []).map((h) => Number(h.price)).filter((v) => !isNaN(v));
  if (!values.length) return null;
  return values.reduce((a, b) => a + b, 0) / values.length;
}

/**
 * Which slot the readout is about: the one tapped, else the one happening now.
 *
 * Kept apart from the drawing so it can be tested, and so both cards answer the
 * question the same way.
 */
function pickedSlot(hours, index) {
  const priced = hours.map((h) => ({ ...h, value: Number(h.price) || 0 }));
  if (Number.isInteger(index) && priced[index]) {
    return { slot: priced[index], live: false };
  }
  const now = Date.now();
  return { slot: priced.find((h) => covers(h, now)), live: true };
}


/** The numbers worth reading out loud: now, the extremes, and when they fall. */
function priceSummary(hours) {
  if (!hours.length) return null;
  const now = Date.now();
  const priced = hours.map((h) => ({ ...h, value: Number(h.price) || 0 }));
  const low = priced.reduce((a, b) => (b.value < a.value ? b : a));
  const high = priced.reduce((a, b) => (b.value > a.value ? b : a));
  const current = priced.find((h) => covers(h, now));
  return { current, low, high };
}

class BatteryManagementCard extends HTMLElement {
  setConfig(config) {
    if (!config) throw new Error("Invalid configuration");
    this._config = config;
    this._built = false;
    // Which length of the charge split to show. The month by default - a day
    // says little on its own and the lifetime total stops moving in any useful
    // way after a few months. `charge_period` sets what it opens on; tapping
    // changes it for the session, and deliberately does not write itself back
    // into the config, because a card cannot edit its own YAML and pretending
    // otherwise would lose the choice on the next reload without saying why.
    this._period = CHARGE_SUFFIX[config.charge_period]
      ? config.charge_period
      : DEFAULT_PERIOD;
  }

  set hass(hass) {
    this._hass = hass;
    if (!this._built) this._build();
    this._update();
  }

  /**
   * The hourly prices, coloured by the decision each hour belongs to.
   *
   * Green is not "a low number" - it is the hours this will actually buy on,
   * and red the hours it is keeping the charge for. The integration works that
   * out (a card picking its own threshold would draw a different plan than the
   * one being executed), so this only has to render what it is told.
   *
   * The two hues clear a colourblind-separation check on both a light and a
   * dark card (CVD dE 9.7), but they still never carry the meaning alone: every
   * bar names its role in its tooltip and the legend spells all three out.
   */
  _renderPrices() {
    const wrap = this.querySelector("#prices");
    if (!wrap) return;
    const hours = this._attrs(this._config.prices).hours;
    if (!Array.isArray(hours) || !hours.length) {
      wrap.style.display = "none";
      return;
    }
    wrap.style.display = "block";
    renderNav(this, hours);
    const slots = chartSlots(this, hours);
    drawPrices(
      this.querySelector("#plot"),
      this.querySelector("#paxis"),
      slots,
      this._picked
    );
    const { slot, live } = pickedSlot(slots, this._picked);
    const average = dayAverage(slots);
    this.querySelector("#pnow").textContent = slot
      ? `${live ? "nu" : hhmm(slot.start)} ${slot.value.toFixed(3)} €/kWh`
      : average !== null
      ? `gemiddeld ${average.toFixed(3)} €/kWh`
      : slots.length
      ? ""
      : "geen gegevens";
  }

  /**
   * What you get when the card is picked out of Home Assistant's card list.
   *
   * Without this it arrives empty and every entity has to be typed by hand,
   * which is not what "add a card" means anywhere else in Home Assistant.
   * Everything is derived from one anchor - the Setpoint sensor - so a renamed
   * device still resolves, and nothing is written into the config unless the
   * entity actually exists.
   */
  static getStubConfig(hass, entities) {
    const all = knownEntities(hass, entities);
    const config = { type: "custom:battery-management-card" };

    const setpoint = all.find(
      (id) => id.startsWith("sensor.") && id.endsWith("_setpoint")
    );
    if (!setpoint) return config;
    const prefix = setpoint.slice(7, -"_setpoint".length);
    const has = (id) => (all.includes(id) ? id : undefined);
    const put = (key, id) => {
      if (id) config[key] = id;
    };

    put("setpoint", setpoint);
    put("status", has(`sensor.${prefix}_status`));
    put("enable", has(`switch.${prefix}_coordinator_enabled`));
    put("fast_charge", has(`switch.${prefix}_fast_charge_emergency`));
    put("grid_power", has(`sensor.${prefix}_grid_power_as_read`));
    // the plan carries the whole price series, which is what the chart draws
    put("prices", has(`sensor.${prefix}_plan`));
    put("mode", has(`select.${prefix}_mode`));
    put("policy", has(`sensor.${prefix}_active_policy`));

    // The charge split, counted by the integration itself from the packs' own
    // charging power. Only present once a pack has that sensor configured, and
    // `has` already refuses to write an entity that does not exist - so a site
    // that never picked one simply gets a card without the split.
    // The month in progress, not the lifetime totals. "How much have these
    // packs ever charged" is a number that stops moving in any useful way
    // after a few months; "what did they do this month" is the one somebody
    // reads a battery card for. The lifetime pair is still published and is
    // what the Energy dashboard should be pointed at.
    put("charged_total", has(`sensor.${prefix}_charged_this_month`));
    put("charged_grid", has(`sensor.${prefix}_charged_from_grid_this_month`));

    // one row per pack, found by its target sensor - the state of charge comes
    // out of that sensor's own attributes, so no Anker entity has to be guessed
    const units = all
      .filter((id) => id.startsWith(`sensor.${prefix}_`) && id.endsWith("_target"))
      .sort()
      .map((target) => {
        const slug = target.slice(`sensor.${prefix}_`.length, -"_target".length);
        const name = slug.replace(/_/g, " ").replace(/^./, (c) => c.toUpperCase());
        const row = { name, target };
        const online = has(`binary_sensor.${prefix}_${slug}_online`);
        if (online) row.status = online;
        return row;
      });
    if (units.length) config.units = units;
    return config;
  }

  getCardSize() {
    const c = this._config;
    return (
      4 +
      (c?.units?.length || 0) +
      (c?.prices ? 3 : 0) +
      (c?.charged_total || c?.charged_grid ? 1 : 0)
    );
  }

  _s(id) {
    const st = this._hass && id ? this._hass.states[id] : undefined;
    return st ? st.state : undefined;
  }

  _num(id) {
    const v = parseFloat(this._s(id));
    return isNaN(v) ? null : v;
  }

  _attrs(id) {
    const st = this._hass && id ? this._hass.states[id] : undefined;
    return (st && st.attributes) || {};
  }

  _svc(domain, service, data) {
    this._hass.callService(domain, service, data);
  }

  /**
   * An entity this card needs: named in the config, or found next to the
   * setpoint sensor it was given.
   *
   * `getStubConfig` fills these in, but only for a card being *added*. A card
   * already on a dashboard keeps the config it was created with, so every
   * release introducing an entity would otherwise mean hand-editing YAML on
   * every dashboard that already has one - the thing this integration exists
   * to avoid, being installed at several sites and maintained from one place.
   */
  _entity(key, suffix, domain = "sensor") {
    const c = this._config;
    if (c[key]) return c[key];
    return resolveRelated(this._hass, c.setpoint, "_setpoint", suffix, domain);
  }


  _build() {
    const c = this._config;
    this.innerHTML = `
      <ha-card header="${esc(c.title || "Battery Management")}">
        <style>
          .sbc { padding: 8px 16px 16px; }
          .row { display:flex; align-items:center; justify-content:space-between; padding:8px 0; }
          .row.top { border-bottom:1px solid var(--divider-color); }
          .status { font-weight:600; }
          .muted { color: var(--secondary-text-color); }
          .btns { display:flex; gap:8px; margin:12px 0 4px; }
          .btn { flex:1; text-align:center; padding:10px; border-radius:10px; cursor:pointer;
                 background: var(--secondary-background-color); color: var(--primary-text-color);
                 border:1px solid var(--divider-color); user-select:none; }
          .btn.on { background: var(--primary-color); color: var(--text-primary-color, #fff); border-color: var(--primary-color); }
          .btn.warn { background: var(--warning-color, #ff9800); color:#fff; border-color: transparent; }
          .unit { padding:10px 0; border-top:1px solid var(--divider-color); }
          .uhead { display:flex; justify-content:space-between; align-items:baseline; }
          .uname { font-weight:600; }
          .bar { height:10px; border-radius:6px; background: var(--divider-color); overflow:hidden; margin:6px 0 4px; }
          .fill { height:100%; width:0%; background: var(--success-color, #4caf50); transition:width .4s; }
          .sel { font: inherit; max-width:62%; padding:6px 8px; border-radius:8px;
                 background: var(--secondary-background-color); color: var(--primary-text-color);
                 border:1px solid var(--divider-color); }
          .charge { padding:10px 0; border-top:1px solid var(--divider-color); }
          .bar.split { display:flex; }
          .bar.split .fill.sun { background: var(--success-color, #4caf50); }
          .bar.split .fill.net { background: var(--info-color, #039be5); }
          /* the swatches repeat the bar's colours, but the words carry the
             meaning on their own - nobody should have to match a hue */
          .cnote { font-size:.82em; margin-top:4px; }
          .pergroup { display:flex; gap:6px; margin:6px 0 8px; }
          .pergroup .pill { font-size:.78em; padding:2px 10px; border-radius:10px;
                            cursor:pointer; touch-action:manipulation;
                            color: var(--secondary-text-color);
                            border:1px solid var(--divider-color); }
          .pergroup .pill.on { background: var(--primary-color);
                               border-color: var(--primary-color);
                               color: var(--text-primary-color, #fff); }
          .cleg i { display:inline-block; width:9px; height:9px; border-radius:3px;
                    margin-right:5px; vertical-align:middle; font-style:normal; }
          .prices { margin-top:12px; padding:10px 10px 6px; border-radius:10px;
                    background: var(--secondary-background-color); }
${PRICE_CSS}
          .plan { margin-top:12px; padding:10px; border-radius:10px; background: var(--secondary-background-color); font-size:.92em; }
        </style>
        <div class="sbc">
          <div class="row top">
            <span>Status</span><span class="status" id="status">—</span>
          </div>
          <div class="row">
            <span class="muted">Setpoint / net</span><span id="power" class="muted">—</span>
          </div>
          <div class="row" id="moderow" style="display:none">
            <span>Modus</span>
            <select class="sel" id="mode"></select>
          </div>
          <div class="row" id="policyrow" style="display:none">
            <span class="muted">Actief beleid</span><span class="muted" id="policy">—</span>
          </div>
          <div class="btns">
            <div class="btn" id="enable">Coördinator</div>
            <div class="btn warn" id="fast">Snelladen</div>
          </div>
          <div id="units"></div>
          <div class="charge" id="charge" style="display:none">
            <div class="uhead"><span>Geladen</span><span class="muted" id="ctotal">—</span></div>
            <div class="pergroup" id="cper">
              <span class="pill" data-period="day">Dag</span>
              <span class="pill" data-period="week">Week</span>
              <span class="pill" data-period="month">Maand</span>
            </div>
            <div class="bar split" id="cbar"><div class="fill sun" id="csun"></div><div class="fill net" id="cnet"></div></div>
            <div class="muted cnote" id="cnote" style="display:none"></div>
            <div class="uhead muted cleg">
              <span><i style="background:var(--success-color,#4caf50)"></i><span id="csunt">—</span></span>
              <span><span id="cnett">—</span><i style="background:var(--info-color,#039be5);margin:0 0 0 5px"></i></span>
            </div>
          </div>
          <div class="prices" id="prices" style="display:none">
            <div class="phead"><span>Prijs per uur</span><span id="pnow" class="muted"></span></div>
            <div class="plot" id="plot"></div>
            <div class="paxis" id="paxis"></div>
${PRICE_NAV}
${PRICE_LEGEND}
          <div class="plan" id="plan" style="display:none"></div>
        </div>
      </ha-card>`;

    this.querySelector("#enable").addEventListener("click", () => {
      if (!c.enable) return;
      this._svc("switch", "toggle", { entity_id: c.enable });
    });
    wirePlot(this);
    wireNav(this);
    // Delegated to the group rather than bound per pill, so the handler
    // survives `_update` rewriting the pills' classes - and so adding a period
    // later is a markup change and nothing else.
    this.querySelector("#cper").addEventListener("click", (event) => {
      const pill = event.target.closest(".pill");
      if (!pill) return;
      this._period = pill.dataset.period;
      this._renderCharge();
    });
    // Changing the mode is the one control here that is not a toggle, so it is
    // a plain <select>: it is what every phone already knows how to open.
    this.querySelector("#mode").addEventListener("change", (event) => {
      const option = event.target.value;
      const entity = this._entity("mode", "_mode", "select");
      if (!entity || !option) return;
      this._svc("select", "select_option", { entity_id: entity, option });
    });
    this.querySelector("#fast").addEventListener("click", () => {
      if (!c.fast_charge) return;
      const on = this._s(c.fast_charge) === "on";
      if (on) {
        this._svc("switch", "turn_off", { entity_id: c.fast_charge });
      } else if (
        confirm(
          "Snelladen starten? Beide batterijen stoppen met ontladen en laden zo snel mogelijk vol op — ook vanaf het net indien nodig. Bij vol schakelt hij automatisch terug."
        )
      ) {
        this._svc("switch", "turn_on", { entity_id: c.fast_charge });
      }
    });

    // unit rows
    const uwrap = this.querySelector("#units");
    (c.units || []).forEach((u, i) => {
      const el = document.createElement("div");
      el.className = "unit";
      el.innerHTML = `
        <div class="uhead"><span class="uname">${esc(u.name || "Unit " + (i + 1))}<span class="muted" id="uph${i}"></span></span>
          <span class="muted" id="ust${i}">—</span></div>
        <div class="bar"><div class="fill" id="ufill${i}"></div></div>
        <div class="uhead muted"><span id="usoc${i}">—</span><span id="utar${i}">—</span></div>`;
      uwrap.appendChild(el);
    });
    this._built = true;
  }

  /**
   * The mode, as a control rather than a readout.
   *
   * The options are rebuilt only when the option list itself changes. `_update`
   * runs on every state change in the entire house - dozens per minute - and
   * replacing the innerHTML of an open dropdown closes it under the reader's
   * finger. That would make the mode readable but not actually changeable on a
   * phone, which is exactly where it gets changed.
   *
   * The list comes from the entity, never from a constant here: Dynamic only
   * exists once a price source is configured, so a hard-coded list would offer
   * a mode half the installs cannot take.
   */
  _renderMode() {
    const row = this.querySelector("#moderow");
    const sel = this.querySelector("#mode");
    const st = this._hass.states[this._entity("mode", "_mode", "select")];
    if (!st) {
      row.style.display = "none";
      return;
    }
    row.style.display = "flex";
    const options = Array.isArray(st.attributes.options) ? st.attributes.options : [];
    // stringified rather than joined: no separator to be wrong about
    const key = JSON.stringify(options);
    if (key !== this._modeOptions) {
      this._modeOptions = key;
      sel.innerHTML = options
        .map(
          (o) =>
            `<option value="${esc(o)}">${esc(stateLabel(this._hass, st, o))}</option>`
        )
        .join("");
    }
    if (sel.value !== st.state) sel.value = st.state;
  }

  /**
   * What is actually there, when what we looked for is not.
   *
   * The site's own diagnostics said the counters were working - 0.052 kWh
   * counted - while the card reported them missing, so the fault was in
   * guessing the entity id. Two rounds went by narrowing that down by
   * hypothesis. Listing the neighbours turns the next round into a reading:
   * either the real id is in the list and the matching is wrong, or nothing
   * is and the entity does not live under this prefix at all.
   */
  _nearby(word) {
    const c = this._config;
    if (!c.setpoint || !this._hass) return "";
    const head = "sensor." + c.setpoint.slice("sensor.".length, -"_setpoint".length);
    const near = Object.keys(this._hass.states)
      .filter((id) => id.startsWith(head) && id.includes(word))
      .sort();
    return near.length
      ? "Wel gevonden: " + near.slice(0, 4).join(", ") +
        (near.length > 4 ? " (+" + (near.length - 4) + ")" : "") + "."
      : "Er staat niets met '" + word + "' onder deze prefix.";
  }

  /**
   * Which rule is currently holding the packs where they are.
   *
   * The mode above says what is *allowed*; this says what is actually binding
   * right now, and the two are often different - "Volg de meter" with a flat
   * setpoint reads as a broken card until this line says "Accu's leeg". That
   * is the whole reason the sensor exists, so it belongs next to the graph it
   * explains rather than three screens away in the entity list.
   */
  _renderPolicy() {
    const row = this.querySelector("#policyrow");
    const st = this._hass.states[this._entity("policy", "_active_policy")];
    if (!st) {
      row.style.display = "none";
      return;
    }
    row.style.display = "flex";
    this.querySelector("#policy").textContent = stateLabel(this._hass, st);
  }

  /**
   * How much went into the packs this month, and how much of it was bought.
   *
   * The *monthly* pair, which starts again on the 1st. The lifetime totals are
   * still published under `_charged` / `_charged_from_grid` and are the ones
   * to point the Energy dashboard at; a card is read for "how are we doing",
   * and an all-time figure stops answering that after a few months.
   *
   * Both numbers are counted by the integration from the packs' own charging
   * power - `sensor.…_charged_this_month` and `…_charged_from_grid_this_month`.
   * Deliberately
   * *not* derived from its own commands: those are the plan, and the packs
   * answer 10-30 s later, so integrating them would put an authoritative-
   * looking number on the dashboard that is not what happened.
   */
  _renderCharge() {
    const wrap = this.querySelector("#charge");
    const note = this.querySelector("#cnote");
    const bar = this.querySelector("#cbar");
    const period = CHARGE_SUFFIX[this._period] ? this._period : DEFAULT_PERIOD;
    const [totalSuffix, gridSuffix] = CHARGE_SUFFIX[period];
    // `charged_total` / `charged_grid` pin the **month** pair only. They are
    // what `getStubConfig` writes and what older configs carry, so honouring
    // them for every period would show the month under all three labels.
    const explicit = period === DEFAULT_PERIOD;
    const total = this._entity(explicit ? "charged_total" : null, totalSuffix);
    const grid = this._entity(explicit ? "charged_grid" : null, gridSuffix);
    const split = chargeSplit(this._num(total), this._num(grid));

    for (const pill of this.querySelectorAll("#cper .pill")) {
      pill.classList.toggle("on", pill.dataset.period === period);
    }

    if (!split) {
      // Says why, rather than vanishing. A block that hides itself when it
      // cannot find its entities looks exactly like a card that is broken,
      // and the reader has no way to tell which - three rounds of "I still
      // do not see it" went by before this was written. Naming the id it
      // looked for turns the next question into a one-line answer.
      // Only where there is something to explain. Without a setpoint the card
      // does not know which integration it belongs to, so it has no id to
      // name and no business nagging about one.
      if (!this._config.setpoint) {
        wrap.style.display = "none";
        return;
      }
      wrap.style.display = "block";
      bar.style.display = "none";
      note.style.display = "block";
      // Two different faults, and they need two different answers. Found but
      // unreadable means the counter exists and has nothing to say yet, which
      // is what an unconfigured charge-power sensor looks like. Not found at
      // all means the card is looking in the wrong place, which is a bug here
      // rather than a setting there.
      note.textContent = total
        ? "Nog niets geteld: " + total + " heeft nog geen waarde. Vul per " +
          "accu de laadvermogen-sensor in bij de integratie."
        : "Laadtelling niet gevonden - gezocht naar sensor." +
          this._config.setpoint.slice("sensor.".length, -"_setpoint".length) +
          "_charged_this_month. " + this._nearby("charg");
      this.querySelector("#ctotal").textContent = "";
      this.querySelector("#csunt").textContent = "";
      this.querySelector("#cnett").textContent = "";
      return;
    }
    wrap.style.display = "block";
    bar.style.display = "flex";
    note.style.display = "none";
    this.querySelector("#ctotal").textContent = kwh(split.total);
    const sun = this.querySelector("#csun");
    const net = this.querySelector("#cnet");
    // no split without both halves; a half-drawn bar would read as a real
    // proportion, and it would be the wrong one
    const share = split.share === null ? 0 : split.share;
    sun.style.width = share + "%";
    net.style.width = (split.share === null ? 0 : 100 - share) + "%";
    this.querySelector("#csunt").textContent =
      split.solar === null ? "" : `zon ${kwh(split.solar)}`;
    this.querySelector("#cnett").textContent =
      split.grid === null ? "" : `net ${kwh(split.grid)}`;
  }

  _update() {
    if (!this._hass || !this._built) return;
    const c = this._config;

    this._renderMode();
    this._renderPolicy();
    this._renderCharge();

    const status = this._s(c.status) || "—";
    this.querySelector("#status").textContent = status;
    this.querySelector("#status").style.color = FILL[status] || "var(--primary-text-color)";

    const sp = this._num(c.setpoint);
    const grid = this._num(c.grid_power);
    const parts = [];
    if (sp !== null) parts.push((sp >= 0 ? "ontladen " : "laden ") + Math.abs(sp) + " W");
    if (grid !== null) parts.push("net " + grid + " W");
    this.querySelector("#power").textContent = parts.join(" · ") || "—";

    const enBtn = this.querySelector("#enable");
    const enOn = this._s(c.enable) === "on";
    enBtn.textContent = "Coördinator: " + (enOn ? "aan" : "uit");
    enBtn.classList.toggle("on", enOn);

    const fBtn = this.querySelector("#fast");
    const fOn = this._s(c.fast_charge) === "on";
    fBtn.textContent = fOn ? "Snelladen: bezig (stop)" : "Snelladen starten";

    (c.units || []).forEach((u, i) => {
      // the pack's own SoC sensor if one was named, otherwise the value our
      // target sensor already publishes alongside what it commanded
      const attr = this._attrs(u.target).soc;
      const soc = u.soc ? this._num(u.soc) : attr === undefined ? null : Number(attr);
      const tar = this._num(u.target);
      const ust = this._s(u.status);
      this.querySelector(`#usoc${i}`).textContent = soc !== null ? soc + "%" : "—";
      this.querySelector(`#utar${i}`).textContent = tar !== null ? "doel " + tar + " W" : "";
      this.querySelector(`#ust${i}`).textContent = ust || "";
      // which leg of the supply this pack is on, once it is known. Blank rather
      // than a guess: an unplaced pack is held back, not placed somewhere.
      const phase = this._s(u.phase);
      this.querySelector(`#uph${i}`).textContent =
        phase && phase !== "unknown" && phase !== "unavailable" ? "  ·  " + phase : "";
      const fill = this.querySelector(`#ufill${i}`);
      fill.style.width = (soc !== null ? Math.max(0, Math.min(100, soc)) : 0) + "%";
      fill.style.background =
        soc === null ? "var(--error-color,#f44)" :
        soc < 15 ? "var(--warning-color,#ff9800)" : "var(--success-color,#4caf50)";
    });

    this._renderPrices();

    const plan = this.querySelector("#plan");
    if (c.forecast_today || c.forecast_tomorrow) {
      const t = this._s(c.forecast_today);
      const m = this._s(c.forecast_tomorrow);
      plan.style.display = "block";
      // Only state what the integration actually does. It charges on grid
      // surplus; charging on the cheapest hours of a dynamic tariff is not
      // built yet, and claiming otherwise would be a lie on the dashboard.
      plan.innerHTML =
        `<b>Zonverwachting</b> — <b>${esc(t ?? "?")} kWh vandaag</b>` +
        (m ? `, <b>${esc(m)} kWh morgen</b>` : "") +
        `.<br><span class="muted">Laden volgt het netoverschot; laden op goedkope uren zit er nog niet in.</span>`;
    } else {
      plan.style.display = "none";
    }
  }
}

/**
 * Defining an element twice throws, and the throw kills the rest of the file.
 *
 * That is not hypothetical: if this script is loaded once by the integration
 * and once again as a hand-added Lovelace resource, the second copy dies here -
 * before it reaches the prices card - so the card list shows a stale entry and
 * the second card is simply missing. Registering is made idempotent instead.
 */
function defineCard(tag, cls, entry) {
  try {
    if (!customElements.get(tag)) customElements.define(tag, cls);
  } catch (err) {
    // Recorded rather than thrown. A throw here kills the rest of the file,
    // taking the second card with it - and the browser reports the casualty,
    // not the cause.
    (BOOT.failed = BOOT.failed || []).push(`${tag}: ${err && err.message}`);
  }
  window.customCards = window.customCards || [];
  const already = window.customCards.findIndex((c) => c.type === tag);
  if (already >= 0) window.customCards.splice(already, 1);
  window.customCards.push(entry);
}

defineCard("battery-management-card", BatteryManagementCard, {
  type: "battery-management-card",
  name: "Battery Management Card",
  description:
    "Controls, per-pack state of charge, and the hourly price chart.",
  preview: false,
});

console.info(
  "%c BATTERY-MANAGEMENT-CARD %c loaded ",
  "background:#039be5;color:#fff",
  ""
);


/**
 * Just the prices - so the supplier's app can stay shut.
 *
 * Deliberately its own card. Somebody deciding whether to run the dishwasher
 * wants the prices, not a battery control panel, and a chart bolted inside a
 * control panel cannot be put on a dashboard on its own.
 *
 * Same colours and the same meaning as the chart in the management card: green
 * is the hours the coordinator will buy on, not "a low number".
 */
class BatteryManagementPricesCard extends HTMLElement {
  setConfig(config) {
    if (!config) throw new Error("Invalid configuration");
    this._config = config;
    this._built = false;
  }

  set hass(hass) {
    this._hass = hass;
    if (!this._built) this._build();
    this._update();
  }

  static getStubConfig(hass, entities) {
    const all = knownEntities(hass, entities);
    const plan = all.find((id) => id.startsWith("sensor.") && id.endsWith("_plan"));
    const config = { type: "custom:battery-management-prices-card" };
    if (plan) config.prices = plan;
    return config;
  }

  getCardSize() {
    return 4;
  }

  _build() {
    const c = this._config;
    this.innerHTML = `
      <ha-card header="${esc(c.title || "Stroomprijs")}">
        <style>
          .pc { padding: 4px 16px 16px; }
          .big { font-size:2em; font-weight:600; line-height:1.15; }
          .sub { color: var(--secondary-text-color); font-size:.85em; margin-bottom:14px; }
          .ends { display:flex; gap:20px; margin-top:10px; font-size:.85em; }
          .muted { color: var(--secondary-text-color); }
${PRICE_CSS}
        </style>
        <div class="pc">
          <div class="big" id="pnow">—</div>
          <div class="sub" id="psub"></div>
          <div class="plot" id="plot"></div>
          <div class="paxis" id="paxis"></div>
${PRICE_NAV}
          <div class="ends" id="pends"></div>
${PRICE_LEGEND}
        </div>
      </ha-card>`;
    wirePlot(this);
    wireNav(this);
    this._built = true;
  }

  _update() {
    if (!this._hass || !this._built) return;
    const st = this._hass.states[this._config.prices];
    const hours =
      (st && Array.isArray(st.attributes.hours) && st.attributes.hours) || [];
    const big = this.querySelector("#pnow");
    const sub = this.querySelector("#psub");

    if (!hours.length) {
      big.textContent = "—";
      // which of the two it is; "no chart" on its own is not an answer
      sub.textContent = st
        ? "Nog geen prijzen ontvangen."
        : "Prijssensor niet gevonden — controleer de kaartinstelling.";
      for (const id of ["#plot", "#paxis", "#pends"]) {
        this.querySelector(id).innerHTML = "";
      }
      return;
    }

    renderNav(this, hours);
    const slots = chartSlots(this, hours);
    drawPrices(
      this.querySelector("#plot"),
      this.querySelector("#paxis"),
      slots,
      this._picked
    );

    if (!slots.length) {
      // a day with nothing on it: say which of the two it is, rather than
      // leaving an empty frame that reads as a broken chart
      big.textContent = "—";
      sub.textContent = (this._history || {})[this._day]
        ? "Geen prijzen bewaard voor deze dag."
        : "Bezig met ophalen…";
      this.querySelector("#pends").innerHTML = "";
      return;
    }

    const { slot, live } = pickedSlot(slots, this._picked);
    const s = { ...priceSummary(slots), current: slot };
    const average = dayAverage(slots);
    big.textContent = s.current
      ? `${s.current.value.toFixed(3)} €/kWh`
      : `${average.toFixed(3)} €/kWh`;
    big.style.color = s.current
      ? PRICE_COLOUR[PRICE_COLOUR[s.current.role] ? s.current.role : "normal"]
      : "var(--primary-text-color)";
    // a tapped hour that has gone gets the past tense, and says whether the
    // grid was actually paid during it - which is what the reader is asking
    // when they tap a green bar in this morning
    const gone = s.current && (s.current.past === true || at(s.current.end) <= Date.now());
    const says = s.current
      ? ((gone ? PRICE_WAS : PRICE_SAYS)[s.current.role] || "") +
        planSays(s.current, gone)
      : "";
    sub.textContent = s.current
      ? (live ? "nu" : `${hhmm(s.current.start)}–${hhmm(s.current.end)}`) +
        ` — ${says}` +
        (live ? `, tot ${hhmm(s.current.end)}` : " · tik nogmaals voor nu")
      // A day that is over, or one that has not started. The average is what
      // that day is about. Days before today come from the recorder, which
      // stores prices and not verdicts, so those bars stay grey: colouring
      // them against today's ranking would invent decisions never taken.
      : "gemiddeld over " + dayLabel(this._day) + " · tik een staaf aan";
    this.querySelector("#pends").innerHTML =
      `<span><span class="muted">laagste</span> <b>${s.low.value.toFixed(3)}</b>` +
      ` <span class="muted">om ${hhmm(s.low.start)}</span></span>` +
      `<span><span class="muted">hoogste</span> <b>${s.high.value.toFixed(3)}</b>` +
      ` <span class="muted">om ${hhmm(s.high.start)}</span></span>`;
  }
}

defineCard("battery-management-prices-card", BatteryManagementPricesCard, {
  type: "battery-management-prices-card",
  name: "Battery Management Prices",
  description:
    "Today's electricity prices per hour, with the cheap and dear ones marked.",
  preview: false,
});


/**
 * Why the split cannot be shown, in the reader's terms rather than as a key.
 *
 * Each of these is a fixable configuration gap, so the message names the thing
 * to go and fix. "Onbekend" would be true and useless.
 */
const NO_SPLIT = {
  no_capacity:
    "Vul eerst in hoe lang een volle lading duurt (Laadtijd leeg→vol, in de " +
    "opties). Zonder dat is niet te zeggen hoeveel er nog in kan.",
  no_forecast:
    "Geen zonprognose ingesteld. Zonder die kan hij niet bepalen hoeveel hij " +
    "aan de zon overlaat en hoeveel hij zelf koopt.",
  no_units: "Geen accu bereikbaar — dit zegt niets over de accu's zelf.",
};

/**
 * What an hour of buying became, told in the tense that hour is in.
 *
 * Split from the price chart's `planSays` on purpose: this card is a checklist
 * read down the page, so each row states its own outcome rather than appending
 * a clause to a price. `bought` outranks `buy` for the same reason it does
 * there - one is what was intended, the other is the grid actually being paid.
 */
function buyRowSays(hour) {
  if (hour.bought) return { text: "geladen", tone: "done" };
  if (!hour.past) return { text: "gaat laden", tone: "todo" };
  return { text: "niet geladen", tone: "miss" };
}

/**
 * Today's plan: how much goes in, from where, and at what times.
 *
 * Asked for by the owner - "zodat ik kan controleren wat hij doet", and then
 * specifically how much from the sun, how much from the grid, and when.
 *
 * **It deliberately does not draw a predicted setpoint.** That depends on the
 * house minute by minute, so a curve claiming to know today's would look
 * authoritative and be wrong - the same reason `plan()` itself refuses to
 * produce one. What genuinely *is* decided ahead of time is the split: the buy
 * ceiling is the statement "fill this much from the meter, leave that much for
 * the roof", and the cheap hours are when the first half happens. So that is
 * what this shows, and nothing past it.
 *
 * The hour list covers the whole of today rather than only what is left, each
 * row carrying what became of it. A plan card that forgets the morning by
 * teatime cannot be used to check anything.
 */
class BatteryManagementPlanCard extends HTMLElement {
  setConfig(config) {
    if (!config) throw new Error("Invalid configuration");
    this._config = config;
    this._built = false;
  }

  set hass(hass) {
    this._hass = hass;
    if (!this._built) this._build();
    this._update();
  }

  static getStubConfig(hass, entities) {
    const all = knownEntities(hass, entities);
    const find = (suffix, domain) =>
      all.find(
        (id) => id.startsWith((domain || "sensor") + ".") && id.endsWith(suffix)
      );
    const config = { type: "custom:battery-management-plan-card" };
    const plan = find("_plan");
    if (plan) config.plan = plan;
    const policy = find("_active_policy");
    if (policy) config.policy = policy;
    const mode = find("_mode", "select");
    if (mode) config.mode = mode;
    return config;
  }

  getCardSize() {
    return 4;
  }

  _build() {
    const c = this._config;
    this.innerHTML =
      '<ha-card header="' + esc(c.title || "Plan van vandaag") + '">' +
      `<style>
          .plc { padding: 4px 16px 16px; }
          .plc .muted { color: var(--secondary-text-color); }
          .plc .row { display:flex; justify-content:space-between; gap:12px;
                      align-items:baseline; padding:3px 0; }
          .plc h4 { margin:14px 0 4px; font-size:.8em; font-weight:600;
                    text-transform:uppercase; letter-spacing:.04em;
                    color: var(--secondary-text-color); }
          .split { display:flex; gap:10px; margin:6px 0 2px; }
          .half { flex:1 1 0; border-radius:10px; padding:10px 12px;
                  background: var(--secondary-background-color, #f2f2f2); }
          .half .n { font-size:1.5em; font-weight:600; line-height:1.2; }
          .half .l { font-size:.8em; }
          .half.sun { border-left:4px solid var(--success-color,#4caf50); }
          .half.net { border-left:4px solid var(--info-color,#039be5); }
          .hr { display:flex; justify-content:space-between; gap:10px;
                padding:3px 0; font-variant-numeric: tabular-nums; }
          .hr .when { min-width:6.5em; }
          .hr .tag { font-size:.82em; padding:1px 8px; border-radius:9px;
                     white-space:nowrap; }
          .tag.done { background:var(--success-color,#4caf50); color:#fff; }
          .tag.todo { background:var(--info-color,#039be5); color:#fff; }
          .tag.miss { background:var(--divider-color); }
          .note { font-size:.86em; margin:4px 0 0; }
        </style>
        <div class="plc">
          <div class="row"><b id="plnow">—</b><span class="muted" id="plmode"></span></div>
          <h4>Wil hij vandaag nog inladen</h4>
          <div class="split">
            <div class="half sun"><div class="n" id="plsun">—</div>
              <div class="l muted">via de zon</div></div>
            <div class="half net"><div class="n" id="plnet">—</div>
              <div class="l muted">via het net</div></div>
          </div>
          <div class="muted note" id="plwhy"></div>
          <h4>Wanneer van het net</h4>
          <div id="plhours"></div>
          <div class="muted note" id="plnone"></div>
        </div>
      </ha-card>`;
    this._built = true;
  }

  _state(key) {
    const id = this._config[key];
    return id && this._hass && this._hass.states[id];
  }

  _update() {
    const el = (id) => this.querySelector("#" + id);
    const planState = this._state("plan");
    const plan = (planState && planState.attributes) || {};

    // What is binding right now, which is what makes the rest legible: "Volg
    // de meter" beside a flat setpoint reads as a broken card until something
    // underneath says "Accu's leeg".
    const policy = this._state("policy");
    el("plnow").textContent = policy
      ? stateLabel(this._hass, policy)
      : planState
        ? "Plan"
        : "Plan-sensor niet ingesteld op deze kaart";
    const mode = this._state("mode");
    el("plmode").textContent = mode
      ? stateLabel(this._hass, mode)
      : plan.mode
        ? String(plan.mode).replace(/_/g, " ")
        : "";

    // ---- the split ----
    const expected = plan.expected || {};
    if (expected.known) {
      el("plsun").textContent = kwh(expected.solar_kwh);
      el("plnet").textContent = kwh(expected.grid_kwh);
      // Only where the two differ. Left alone they are the same number by
      // construction - the ceiling *is* "100 % minus the sun still coming" -
      // so saying it every time would be noise that buries the one case worth
      // seeing, where the owner's own bounds have overridden the calculation.
      const short =
        expected.room_for_solar_kwh > expected.solar_kwh + 0.05
          ? " Er staat " + kwh(expected.room_for_solar_kwh) +
            " ruimte vrij, maar er komt maar " +
            kwh(expected.solar_remaining_kwh) + " zon."
          : "";
      el("plwhy").textContent =
        "Koopt bij tot " + Math.round(expected.ceiling) +
        " % en laat de rest aan de zon." + short;
    } else {
      el("plsun").textContent = "—";
      el("plnet").textContent = "—";
      el("plwhy").textContent = planState
        ? NO_SPLIT[expected.reason] || "Nog niet te zeggen."
        : "";
    }

    // ---- when, and what became of it ----
    const today = slotsOnDay(plan.hours || [], dayKey(0));
    const rows = today.filter((h) => h.buy || h.bought);
    el("plhours").innerHTML = rows
      .map((h) => {
        const says = buyRowSays(h);
        return (
          '<div class="hr"><span class="when">' +
          hhmm(h.start) + "–" + hhmm(h.end) + "</span>" +
          '<span class="muted">' + Number(h.price).toFixed(3) + " €/kWh</span>" +
          '<span class="tag ' + says.tone + '">' + says.text + "</span></div>"
        );
      })
      .join("");

    // An empty list has four quite different meanings, and telling them apart
    // is most of what this card is for: "nothing planned" must never be able
    // to stand in for "no prices", "wrong mode" or "already full enough".
    el("plnone").textContent = !planState
      ? ""
      : rows.length
        ? ""
        : plan.mode !== "dynamic"
          ? "Koopt niet van het net in deze modus — volgt alleen de meter."
          : plan.has_prices === false
            ? "Geen prijzen binnen, dus er wordt niets van het net gekocht."
            : (plan.cheap_hours || []).length
              ? "Niets te kopen: de accu's halen het plafond al zonder het net."
              : "Geen uur is goedkoop genoeg om op te kopen.";
  }
}

defineCard("battery-management-plan-card", BatteryManagementPlanCard, {
  type: "battery-management-plan-card",
  name: "Battery Management Plan",
  description:
    "What it intends today: how much from the sun, how much from the grid, and when.",
  preview: false,
});

/**
 * One line that answers "did this arrive in time, and did it work".
 *
 * Also left on `window.batteryManagementCardBoot`, so it can be read back
 * afterwards rather than caught in the moment.
 */
window.batteryManagementCardBoot = {
  ...BOOT,
  finishedAt: typeof performance !== "undefined" ? Math.round(performance.now()) : -1,
  readyStateAtEnd: typeof document !== "undefined" ? document.readyState : "?",
  defined: [
    "battery-management-card",
    "battery-management-prices-card",
    "battery-management-plan-card",
  ].filter((tag) => !!customElements.get(tag)),
  advertised: (window.customCards || [])
    .map((c) => c.type)
    .filter((type) => String(type).startsWith("battery-management")),
};
console.info(
  "%c BATTERY-MANAGEMENT-CARD %c %s | readyState %s -> %s | defined %s | %s ",
  "background:#039be5;color:#fff",
  "",
  window.batteryManagementCardBoot.loadedAs,
  BOOT.readyState,
  window.batteryManagementCardBoot.readyStateAtEnd,
  window.batteryManagementCardBoot.defined.length,
  (BOOT.failed || []).join("; ") || "no define errors"
);

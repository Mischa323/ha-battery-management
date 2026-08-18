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
  // hours already gone: drawn so the shape of the day is there, but without
  // claiming a decision. The ranking looks forward, so they never had one.
  past: "var(--disabled-text-color, #8a8a8a)",
};
// Deliberately "mag" and not "wordt". Being a cheap hour is one of three
// conditions - the pack also has to be empty enough, and there must not be
// more sun coming - so promising that charging happens here is a claim the
// card cannot back up. A dashboard must not overstate what it knows.
const PRICE_SAYS = {
  cheap: "goedkoop genoeg om te kopen",
  dear: "duur, hiervoor wordt bewaard",
  normal: "gewoon de meter volgen",
  past: "geweest",
};

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
          .paxis { display:flex; gap:2px; margin-top:4px; font-size:.72em;
                   color: var(--secondary-text-color); }
          .paxis span { flex:1 1 0; text-align:center; min-width:0; }
          .legend { display:flex; flex-wrap:wrap; gap:12px; margin-top:8px; font-size:.78em;
                    color: var(--secondary-text-color); }
          .legend i { display:inline-block; width:10px; height:10px; border-radius:3px;
                      margin-right:5px; vertical-align:middle; font-style:normal; }
`;

const PRICE_LEGEND = `
            <div class="legend">
              <span><i style="background:${PRICE_COLOUR.cheap}"></i>Goedkoop genoeg om te kopen</span>
              <span><i style="background:${PRICE_COLOUR.dear}"></i>Duur — hiervoor wordt bewaard</span>
              <span><i style="background:${PRICE_COLOUR.normal}"></i>Verder de meter volgen</span>
              <span><i style="background:${PRICE_COLOUR.past};opacity:.35"></i>Geweest</span>
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
      return {
        role,
        past: role === "past",
        price,
        bottom: zero + (price < 0 ? -size : 0),
        height: size,
        down: price < 0,
        current: covers(h, now),
        label: `${hhmm(h.start)} — ${price.toFixed(3)} €/kWh — ${PRICE_SAYS[role]}`,
      };
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
          `<div class="pbar ${b.down ? "down" : "up"}${b.past ? " past" : ""}" ` +
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
    drawPrices(
      this.querySelector("#plot"),
      this.querySelector("#paxis"),
      hours,
      this._picked
    );
    const { slot, live } = pickedSlot(hours, this._picked);
    this.querySelector("#pnow").textContent = slot
      ? `${live ? "nu" : hhmm(slot.start)} ${slot.value.toFixed(3)} €/kWh`
      : "";
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
    put("charged_total", has(`sensor.${prefix}_charged`));
    put("charged_grid", has(`sensor.${prefix}_charged_from_grid`));

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
            <div class="bar split"><div class="fill sun" id="csun"></div><div class="fill net" id="cnet"></div></div>
            <div class="uhead muted cleg">
              <span><i style="background:var(--success-color,#4caf50)"></i><span id="csunt">—</span></span>
              <span><span id="cnett">—</span><i style="background:var(--info-color,#039be5);margin:0 0 0 5px"></i></span>
            </div>
          </div>
          <div class="prices" id="prices" style="display:none">
            <div class="phead"><span>Prijs per uur</span><span id="pnow" class="muted"></span></div>
            <div class="plot" id="plot"></div>
            <div class="paxis" id="paxis"></div>
${PRICE_LEGEND}
          <div class="plan" id="plan" style="display:none"></div>
        </div>
      </ha-card>`;

    this.querySelector("#enable").addEventListener("click", () => {
      if (!c.enable) return;
      this._svc("switch", "toggle", { entity_id: c.enable });
    });
    wirePlot(this);
    // Changing the mode is the one control here that is not a toggle, so it is
    // a plain <select>: it is what every phone already knows how to open.
    this.querySelector("#mode").addEventListener("change", (event) => {
      const option = event.target.value;
      if (!c.mode || !option) return;
      this._svc("select", "select_option", { entity_id: c.mode, option });
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
    const st = this._hass.states[this._config.mode];
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
    const st = this._hass.states[this._config.policy];
    if (!st) {
      row.style.display = "none";
      return;
    }
    row.style.display = "flex";
    this.querySelector("#policy").textContent = stateLabel(this._hass, st);
  }

  /**
   * How much went into the packs, and how much of it was bought.
   *
   * Both numbers are counted by the integration from the packs' own charging
   * power - `sensor.…_charged` and `sensor.…_charged_from_grid`. Deliberately
   * *not* derived from its own commands: those are the plan, and the packs
   * answer 10-30 s later, so integrating them would put an authoritative-
   * looking number on the dashboard that is not what happened.
   */
  _renderCharge() {
    const c = this._config;
    const wrap = this.querySelector("#charge");
    const split = chargeSplit(this._num(c.charged_total), this._num(c.charged_grid));
    if (!split) {
      wrap.style.display = "none";
      return;
    }
    wrap.style.display = "block";
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
          <div class="ends" id="pends"></div>
${PRICE_LEGEND}
        </div>
      </ha-card>`;
    wirePlot(this);
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

    drawPrices(
      this.querySelector("#plot"),
      this.querySelector("#paxis"),
      hours,
      this._picked
    );

    const { slot, live } = pickedSlot(hours, this._picked);
    const s = { ...priceSummary(hours), current: slot };
    big.textContent = s.current ? `${s.current.value.toFixed(3)} €/kWh` : "—";
    big.style.color = s.current
      ? PRICE_COLOUR[PRICE_COLOUR[s.current.role] ? s.current.role : "normal"]
      : "var(--primary-text-color)";
    sub.textContent = s.current
      ? (live ? "nu" : `${hhmm(s.current.start)}–${hhmm(s.current.end)}`) +
        ` — ${PRICE_SAYS[s.current.role] || ""}` +
        (live ? `, tot ${hhmm(s.current.end)}` : " · tik nogmaals voor nu")
      : "buiten de gepubliceerde uren";
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

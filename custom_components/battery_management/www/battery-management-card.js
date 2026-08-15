/**
 * Battery Management management card.
 * Vanilla web component (no build step). Ships with the integration and is
 * auto-registered, so `type: custom:battery-management-card` just works.
 */

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
    const attrs = this._attrs(this._config.prices);
    const hours = Array.isArray(attrs.hours) ? attrs.hours : [];
    if (!hours.length) {
      wrap.style.display = "none";
      return;
    }
    wrap.style.display = "block";

    const prices = hours.map((h) => Number(h.price) || 0);
    // negative prices are real on a dynamic tariff, so the baseline is 0 and
    // bars hang below it rather than being clipped away
    const top = Math.max(0, ...prices);
    const bottom = Math.min(0, ...prices);
    const span = top - bottom || 1;
    const zero = ((0 - bottom) / span) * 100;

    const nowIso = new Date().toISOString();
    const colour = {
      cheap: "#089408",
      dear: "#e07070",
      normal: "var(--disabled-text-color, #8a8a8a)",
    };
    const says = {
      cheap: "goedkoop, hier wordt geladen",
      dear: "duur, hiervoor wordt bewaard",
      normal: "gewoon de meter volgen",
    };

    const plot = this.querySelector("#plot");
    plot.innerHTML =
      `<div class="zero" style="bottom:${zero}%"></div>` +
      hours
        .map((h) => {
          const price = Number(h.price) || 0;
          const size = (Math.abs(price) / span) * 100;
          const base = zero + (price < 0 ? -size : 0);
          const role = colour[h.role] ? h.role : "normal";
          const now = h.start <= nowIso && nowIso < h.end ? " now" : "";
          const hour = String(h.start).slice(11, 16);
          const label = `${hour} — ${price.toFixed(3)} €/kWh — ${says[role]}`;
          return (
            `<div class="slot${now}" title="${esc(label)}">` +
            `<div class="pbar ${price < 0 ? "down" : "up"}" ` +
            `style="bottom:${base}%;height:${size}%;background:${colour[role]}"></div>` +
            `</div>`
          );
        })
        .join("");

    // a label every few hours, not on every bar
    const every = Math.max(1, Math.round(hours.length / 6));
    this.querySelector("#paxis").innerHTML = hours
      .map((h, i) => `<span>${i % every === 0 ? String(h.start).slice(11, 16) : ""}</span>`)
      .join("");

    const current = hours.find((h) => h.start <= nowIso && nowIso < h.end);
    this.querySelector("#pnow").textContent = current
      ? `nu ${Number(current.price).toFixed(3)} €/kWh`
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
    const all =
      (Array.isArray(entities) && entities.length ? entities : null) ||
      Object.keys((hass && hass.states) || {});
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
    return 4 + (this._config?.units?.length || 0) + (this._config?.prices ? 3 : 0);
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
          .prices { margin-top:12px; padding:10px 10px 6px; border-radius:10px;
                    background: var(--secondary-background-color); }
          .phead { display:flex; justify-content:space-between; align-items:baseline;
                   font-size:.92em; margin-bottom:8px; }
          /* the plot: one column per slot, 2px of surface between them */
          .plot { display:flex; align-items:flex-end; gap:2px; height:96px; position:relative; }
          .zero { position:absolute; left:0; right:0; height:1px;
                  background: var(--divider-color); }
          .slot { flex:1 1 0; height:100%; position:relative; min-width:0; }
          .pbar { position:absolute; left:0; right:0; }
          .pbar.up { border-radius:4px 4px 0 0; }
          .pbar.down { border-radius:0 0 4px 4px; }
          /* now is found by outline, not by hue - colour is already spoken for */
          .slot.now .pbar { outline:2px solid var(--primary-text-color); outline-offset:1px; }
          .paxis { display:flex; gap:2px; margin-top:4px; font-size:.72em;
                   color: var(--secondary-text-color); }
          .paxis span { flex:1 1 0; text-align:center; min-width:0; }
          .legend { display:flex; flex-wrap:wrap; gap:12px; margin-top:8px;
                    font-size:.78em; color: var(--secondary-text-color); }
          .legend i { display:inline-block; width:10px; height:10px; border-radius:3px;
                      margin-right:5px; vertical-align:middle; font-style:normal; }
          .plan { margin-top:12px; padding:10px; border-radius:10px; background: var(--secondary-background-color); font-size:.92em; }
        </style>
        <div class="sbc">
          <div class="row top">
            <span>Status</span><span class="status" id="status">—</span>
          </div>
          <div class="row">
            <span class="muted">Setpoint / net</span><span id="power" class="muted">—</span>
          </div>
          <div class="btns">
            <div class="btn" id="enable">Coördinator</div>
            <div class="btn warn" id="fast">Snelladen</div>
          </div>
          <div id="units"></div>
          <div class="prices" id="prices" style="display:none">
            <div class="phead"><span>Prijs per uur</span><span id="pnow" class="muted"></span></div>
            <div class="plot" id="plot"></div>
            <div class="paxis" id="paxis"></div>
            <div class="legend">
              <span><i style="background:#089408"></i>Goedkoop — hier wordt geladen</span>
              <span><i style="background:#e07070"></i>Duur — hiervoor wordt bewaard</span>
              <span><i style="background:var(--disabled-text-color,#8a8a8a)"></i>Verder de meter volgen</span>
            </div>
          </div>
          <div class="plan" id="plan" style="display:none"></div>
        </div>
      </ha-card>`;

    this.querySelector("#enable").addEventListener("click", () => {
      if (!c.enable) return;
      this._svc("switch", "toggle", { entity_id: c.enable });
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

  _update() {
    if (!this._hass || !this._built) return;
    const c = this._config;

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

customElements.define("battery-management-card", BatteryManagementCard);

window.customCards = window.customCards || [];
window.customCards.push({
  type: "battery-management-card",
  name: "Battery Management Card",
  description:
    "Controls, per-pack state of charge, and the hourly price chart.",
  preview: false,
});

console.info("%c BATTERY-MANAGEMENT-CARD %c loaded ", "background:#039be5;color:#fff", "");

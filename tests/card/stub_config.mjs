// getStubConfig, run against the primary site's real entity list.
import { readFileSync } from "node:fs";
const src = readFileSync(
  "custom_components/battery_management/www/battery-management-card.js",
  "utf8"
);
// stand up just enough browser for the module to evaluate
globalThis.window = globalThis;
// a registry with get(), because the card checks before defining
const _defined = new Map();
globalThis.customElements = {
  define: (tag, cls) => _defined.set(tag, cls),
  get: (tag) => _defined.get(tag),
};
globalThis.HTMLElement = class {};
globalThis.console.info = () => {};

const P = "sensor.battery_management_";
const entities = [
  `${P}setpoint`, `${P}status`, `${P}plan`, `${P}grid_power_as_read`,
  `${P}batterij_01_target`, `${P}batterij_02_target`,
  `${P}batterij_01_phase`, `${P}current_price`,
  `${P}active_policy`, "select.battery_management_mode",
  `${P}charged_this_month`, `${P}charged_from_grid_this_month`,
  "binary_sensor.battery_management_batterij_01_online",
  "binary_sensor.battery_management_batterij_02_online",
  "switch.battery_management_coordinator_enabled",
  "switch.battery_management_fast_charge_emergency",
  "sensor.p1_meter_power", "sensor.anker_solix_solarbank_max_ac_093_soc",
];

let fails = 0;
const check = (name, cond, got) => {
  if (!cond) { console.log("FAIL", name, JSON.stringify(got)); fails++; }
  else console.log("ok  ", name);
};

const [Manage, Prices] = new Function(
  src + ";return [BatteryManagementCard, BatteryManagementPricesCard];"
)();
const cfg = Manage.getStubConfig({ states: {} }, entities);
check("type", cfg.type === "custom:battery-management-card", cfg.type);
check("setpoint", cfg.setpoint === `${P}setpoint`, cfg.setpoint);
check("chart wired to the plan", cfg.prices === `${P}plan`, cfg.prices);
check("switches", cfg.enable && cfg.fast_charge, cfg);
check("both packs", cfg.units?.length === 2, cfg.units);
check("readable names", cfg.units?.[0].name === "Batterij 01", cfg.units?.[0]);
check("online sensor found", !!cfg.units?.[1].status, cfg.units?.[1]);
check("no soc entity guessed", cfg.units?.every((u) => !u.soc), cfg.units);
check("phase sensor not mistaken for a unit", cfg.units?.length === 2, cfg.units);
check("the mode select is wired up", cfg.mode === "select.battery_management_mode", cfg.mode);
check("the policy sensor is wired up", cfg.policy === `${P}active_policy`, cfg.policy);
// the energy counters are the integration's own, so they resolve off the same
// prefix as everything else
check("the charge counters are found",
  cfg.charged_total === `${P}charged_this_month` &&
  cfg.charged_grid === `${P}charged_from_grid_this_month`, cfg);
// no pack has a charge-power sensor -> the entities do not exist -> the card
// must not name them, or it renders a split of nothing
const noCounters = Manage.getStubConfig({ states: {} }, [`${P}setpoint`, `${P}plan`]);
check("no counters, nothing claimed",
  !noCounters.charged_total && !noCounters.charged_grid, noCounters);
// "charged_this_month" must not be mistaken for "charged_from_grid_this_month" or the reverse
const onlyTotal = Manage.getStubConfig({ states: {} }, [`${P}setpoint`, `${P}charged_this_month`]);
check("the two counters are told apart",
  onlyTotal.charged_total === `${P}charged_this_month` && !onlyTotal.charged_grid, onlyTotal);

// a site whose device was renamed, and one with nothing installed
const renamed = Manage.getStubConfig({ states: {} }, ["sensor.accus_setpoint", "sensor.accus_plan"]);
check("renamed device still resolves", renamed.prices === "sensor.accus_plan", renamed);
const empty = Manage.getStubConfig({ states: {} }, ["sensor.something_else"]);
check("nothing found -> bare config", Object.keys(empty).length === 1, empty);
// and nothing is written that does not exist
const partial = Manage.getStubConfig({ states: {} }, [`${P}setpoint`]);
check("no dangling entities", !partial.enable && !partial.prices, partial);

// the prices-only card needs nothing but the plan
const pc = Prices.getStubConfig({ states: {} }, entities);
check("prices card type", pc.type === "custom:battery-management-prices-card", pc);
check("prices card finds the plan", pc.prices === P + "plan", pc);
const bare = Prices.getStubConfig({ states: {} }, ["sensor.other"]);
check("prices card without a plan", !bare.prices, bare);


// Home Assistant passes the entities *not already used* on the dashboard. Once
// one card uses the plan sensor, a second card must still be able to find it.
const states = Object.fromEntries(entities.map((id) => [id, {}]));
const elsewhere = Manage.getStubConfig({ states }, ["sensor.some_thermostat"]);
check("setpoint found outside the suggestions", elsewhere.setpoint === `${P}setpoint`, elsewhere);
const pcElsewhere = Prices.getStubConfig({ states }, ["sensor.some_thermostat"]);
check("plan found outside the suggestions", pcElsewhere.prices === `${P}plan`, pcElsewhere);
const none = Prices.getStubConfig({ states: {} }, []);
check("nothing anywhere -> bare config", !none.prices, none);


console.log(fails ? fails + " FAILED" : "stub config checks pass");
process.exit(fails ? 1 : 0);

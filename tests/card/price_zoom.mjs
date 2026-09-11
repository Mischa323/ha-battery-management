// Reading a quarter-hourly day without a magnifying glass.
//
// Asked for by the owner on 2026-09-11, with a screenshot: 96 bars across a
// phone is about two pixels each, and he could not tell his own prices apart.
// So the chart opens folded to hours and the quarters are one tap away.
//
// What this file is really guarding is the honesty of the fold. An hour drawn
// as one bar is a summary, and a summary is where a chart gets to quietly
// overstate things - calling an hour cheap because one quarter of it was, or
// hiding that charging happens inside it. Both are checked below.
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

const helpers = new Function(
  src +
    ";return {foldToHours, hasQuarters, zoomLayout, slotSpan, clampScale," +
    " drawPrices, priceBars, ZOOM_QUARTER, ZOOM_HOUR, ZOOM_FIT, ZOOM_MAX," +
    " QUARTER_MIN_PX};"
)();
const {
  foldToHours, hasQuarters, zoomLayout, slotSpan, clampScale,
  drawPrices, priceBars, ZOOM_QUARTER, ZOOM_HOUR, ZOOM_FIT, ZOOM_MAX,
  QUARTER_MIN_PX,
} = helpers;

// A quarter starting `minutes` into 2026-09-11 09:00 UTC.
const q = (minutes, price, role = "normal", extra = {}) => {
  const from = Date.UTC(2026, 8, 11, 9, 0) + minutes * 60000;
  return {
    start: new Date(from).toISOString(),
    end: new Date(from + 900000).toISOString(),
    price,
    role,
    ...extra,
  };
};

// ---------------------------------------------------------------- detection
check("a quarter-hourly feed is recognised", hasQuarters([q(0, 0.1)]) === true);
check("an hourly one is not",
  hasQuarters([
    { start: "2026-09-11T09:00:00Z", end: "2026-09-11T10:00:00Z", price: 0.1 },
  ]) === false);
check("a slot that does not say how long it is counts as a quarter",
  slotSpan({ start: "2026-09-11T09:00:00Z" }) === 900000,
  slotSpan({ start: "2026-09-11T09:00:00Z" }));

// ------------------------------------------------------------- the fold
const hour = foldToHours([q(0, 0.10), q(15, 0.20), q(30, 0.30), q(45, 0.40)]);
check("four quarters become one hour", hour.length === 1, hour.length);
check("priced at the duration-weighted mean",
  Math.abs(hour[0].price - 0.25) < 1e-9, hour[0].price);
check("and it spans the whole hour",
  new Date(hour[0].end) - new Date(hour[0].start) === 3600000,
  [hour[0].start, hour[0].end]);

const two = foldToHours([q(0, 0.1), q(15, 0.1), q(60, 0.2), q(75, 0.2)]);
check("separate hours stay separate", two.length === 2, two.length);
check("and they come back in order",
  new Date(two[0].start) < new Date(two[1].start), two.map((h) => h.start));

// -------------------------------------------------------- the verdict
const allCheap = foldToHours([
  q(0, 0.1, "cheap"), q(15, 0.1, "cheap"), q(30, 0.1, "cheap"), q(45, 0.1, "cheap"),
]);
check("an hour that is cheap throughout is drawn cheap",
  allCheap[0].role === "cheap" && allCheap[0].mixed === false, allCheap[0]);

const mostlyCheap = foldToHours([
  q(0, 0.1, "cheap"), q(15, 0.1, "cheap"), q(30, 0.1, "cheap"), q(45, 0.9, "dear"),
]);
check("a clear majority carries the hour",
  mostlyCheap[0].role === "cheap", mostlyCheap[0].role);
check("but it is flagged as mixed, which is what sends you to the quarters",
  mostlyCheap[0].mixed === true, mostlyCheap[0]);

// The one that matters: an even split must not be called either thing. Half a
// cheap hour is not a cheap hour, and a chart that says otherwise is worse
// than one that says nothing.
const split = foldToHours([
  q(0, 0.1, "cheap"), q(15, 0.1, "cheap"), q(30, 0.9, "dear"), q(45, 0.9, "dear"),
]);
check("an evenly split hour claims neither verdict",
  split[0].role === "normal", split[0].role);

// --------------------------------------------------------------- the plan
const oneBuy = foldToHours([
  q(0, 0.1, "cheap", { buy: true }), q(15, 0.1, "cheap"),
  q(30, 0.1, "cheap"), q(45, 0.1, "cheap"),
]);
check("one bought quarter still rings the whole hour",
  oneBuy[0].buy === true, oneBuy[0].buy);
check("and the hour says how much of it that was",
  oneBuy[0].buyParts === 1 && oneBuy[0].parts === 4, oneBuy[0]);

const label = priceBars(oneBuy).bars[0].label;
check("so the tooltip cannot over-promise",
  /1 van de 4 kwartieren/.test(label), label);
// "was een van de goedkoopste uren" also contains "van de", which is how the
// first version of this check passed by accident. Match the unit, not the words.
const whole = foldToHours([0, 15, 30, 45].map((m) => q(m, 0.1, "cheap", { buy: true })));
const wholeLabel = priceBars(whole).bars[0].label;
check("and a whole planned hour is not qualified at all",
  !/kwartieren/.test(wholeLabel), wholeLabel);

// --------------------------------------------------------------- the zoom
// Zoom is one continuous number, and the resolution falls out of it: quarters
// are drawn once a quarter has enough pixels to be told apart from its
// neighbours. That is the whole model, so it is checked at its edges.
const day = [];
for (let i = 0; i < 96; i++) day.push(q(i * 15 - 540, 0.2));
const PHONE = 320;

const fitted = zoomLayout({}, day, PHONE);
check("it opens fitted to the width", fitted.scale === ZOOM_FIT, fitted.scale);
check("and that means 24 hourly bars, not 96",
  fitted.level === ZOOM_HOUR && fitted.slots.length === 24,
  [fitted.level, fitted.slots.length]);

const far = zoomLayout({ _scale: 4 }, day, PHONE);
check("pinched out far enough, the quarters appear",
  far.level === ZOOM_QUARTER && far.slots.length === 96,
  [far.level, far.slots.length]);
check("and each one is wide enough to be told apart",
  far.barPx >= QUARTER_MIN_PX, far.barPx);

// The threshold itself: just under it must still be hours, or the chart would
// hand back the smudge this whole change exists to remove.
const need = (QUARTER_MIN_PX * 96) / PHONE;
check("just below the threshold it is still hours",
  zoomLayout({ _scale: need - 0.01 }, day, PHONE).level === ZOOM_HOUR,
  zoomLayout({ _scale: need - 0.01 }, day, PHONE).level);
check("and just above it, quarters",
  zoomLayout({ _scale: need + 0.01 }, day, PHONE).level === ZOOM_QUARTER,
  zoomLayout({ _scale: need + 0.01 }, day, PHONE).level);

// The same scale has to mean the same thing on any screen, or "twice the day"
// would show half a day on a phone and a third of one on a tablet.
check("a wider screen reaches the quarters sooner, as it should",
  zoomLayout({ _scale: 1.2 }, day, 900).level === ZOOM_QUARTER &&
  zoomLayout({ _scale: 1.2 }, day, 320).level === ZOOM_HOUR,
  [zoomLayout({ _scale: 1.2 }, day, 900).level, zoomLayout({ _scale: 1.2 }, day, 320).level]);
// Asked for explicitly: hours by default, on every screen. A wall tablet is
// wide enough to fit 96 legible bars, and still opens on hours - so the day
// reads the same way everywhere and the quarters are something you ask for.
check("but no screen opens on the quarters by itself",
  zoomLayout({}, day, 900).level === ZOOM_HOUR &&
  zoomLayout({}, day, 2000).level === ZOOM_HOUR,
  [zoomLayout({}, day, 900).level, zoomLayout({}, day, 2000).level]);

check("the scale cannot be pinched past its ends",
  clampScale(0.01) === ZOOM_FIT && clampScale(9999) === ZOOM_MAX,
  [clampScale(0.01), clampScale(9999)]);
check("and rubbish falls back to fitted rather than to NaN",
  clampScale(undefined) === ZOOM_FIT && clampScale("x") === ZOOM_FIT,
  [clampScale(undefined), clampScale("x")]);

// An already-hourly feed has nothing finer to show, at any scale.
const hourly = [];
for (let i = 0; i < 24; i++) {
  const from = Date.UTC(2026, 8, 11) + i * 3600000;
  hourly.push({
    start: new Date(from).toISOString(),
    end: new Date(from + 3600000).toISOString(),
    price: 0.2,
    role: "normal",
  });
}
check("an hourly feed is passed through untouched",
  zoomLayout({}, hourly, PHONE).slots === hourly, zoomLayout({}, hourly, PHONE).slots.length);
check("and zooming it does not invent quarters",
  zoomLayout({ _scale: 6 }, hourly, PHONE).level === ZOOM_HOUR,
  zoomLayout({ _scale: 6 }, hourly, PHONE).level);

// ------------------------------------------------------------- the drawing
const el = () => {
  const classes = new Set();
  const props = {};
  return {
    style: { setProperty: (k, v) => (props[k] = v) },
    props,
    classes,
    innerHTML: "",
    classList: {
      toggle: (name, on) => (on ? classes.add(name) : classes.delete(name)),
      contains: (name) => classes.has(name),
    },
  };
};

let plot = el();
let axis = el();
drawPrices(plot, axis, fitted.slots, null, 0);
check("fitted, the strip does not scroll", !plot.classes.has("wide"), [...plot.classes]);
check("and it draws 24 bars",
  (plot.innerHTML.match(/class="slot/g) || []).length === 24,
  (plot.innerHTML.match(/class="slot/g) || []).length);

plot = el();
axis = el();
drawPrices(plot, axis, far.slots, null, far.barPx);
check("zoomed, the bars take the worked-out width and the strip scrolls",
  plot.classes.has("wide") && axis.classes.has("wide"),
  [[...plot.classes], [...axis.classes]]);
check("the width is handed to the CSS, on both, or the labels would drift",
  plot.props["--bw"] === axis.props["--bw"] && /px$/.test(plot.props["--bw"]),
  [plot.props["--bw"], axis.props["--bw"]]);
check("zoomed bars keep their gap rather than being hairlines",
  plot.style.gap === "2px", plot.style.gap);

// Labels thin out with the bars rather than staying at six for the whole day,
// which is what makes a scrolled strip navigable.
const labels = (a) => (a.innerHTML.match(/>\d\d:\d\d</g) || []).length;
const near = el();
drawPrices(el(), near, far.slots, null, far.barPx);
check("zoomed in there are more time labels, not the same six",
  labels(near) > 6, labels(near));

console.log(fails ? `\n${fails} FAILED` : "\nall zoom checks pass");
process.exit(fails ? 1 : 0);

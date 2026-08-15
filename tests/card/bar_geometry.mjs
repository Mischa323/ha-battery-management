// The bar geometry out of the card, checked on its own.
function geometry(prices) {
  const top = Math.max(0, ...prices);
  const bottom = Math.min(0, ...prices);
  const span = top - bottom || 1;
  const zero = ((0 - bottom) / span) * 100;
  return {
    zero,
    bars: prices.map((p) => {
      const size = (Math.abs(p) / span) * 100;
      return { bottom: zero + (p < 0 ? -size : 0), height: size, down: p < 0 };
    }),
  };
}
const r = (n) => Math.round(n * 100) / 100;
let fails = 0;
function check(name, cond, got) {
  if (!cond) { console.log("FAIL", name, got); fails++; } else console.log("ok  ", name);
}

// all positive: baseline at the bottom, tallest bar fills the plot
let g = geometry([0.10, 0.20, 0.30]);
check("positive: zero at bottom", r(g.zero) === 0, g.zero);
check("positive: tallest fills", r(g.bars[2].height) === 100, g.bars[2].height);
check("positive: none hang down", g.bars.every((b) => !b.down), g.bars);

// with a negative price the baseline lifts and that bar hangs below it
g = geometry([-0.05, 0.10, 0.15]);
check("negative: zero lifted", r(g.zero) === 25, g.zero);
check("negative: hangs down", g.bars[0].down === true, g.bars[0]);
check("negative: bottom below zero", r(g.bars[0].bottom) === 0, g.bars[0]);
check("negative: reaches the line", r(g.bars[0].bottom + g.bars[0].height) === 25, g.bars[0]);
check("negative: top bar reaches 100", r(g.bars[2].bottom + g.bars[2].height) === 100, g.bars[2]);

// every bar the same price: still drawn, not a flat invisible row
g = geometry([0.2, 0.2, 0.2]);
check("flat day: bars visible", g.bars.every((b) => b.height === 100), g.bars);

// all-zero series must not divide by zero
g = geometry([0, 0]);
check("all zero: finite", g.bars.every((b) => Number.isFinite(b.height)), g.bars);

console.log(fails ? `\n${fails} FAILED` : "\nall geometry checks pass");
process.exit(fails ? 1 : 0);

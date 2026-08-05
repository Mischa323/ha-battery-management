"""Run the real control loop against a closed-loop fake house and print a trace.

No Home Assistant, no Docker: this drives the actual `BatteryCoordinator` and
recomputes the meter exactly like `config/packages/simulator.yaml` does
(grid = house load - what the packs were told to do), so it is the 20-second
version of watching the loop regulate.

    python scripts/trace.py                 # 800 W of load, packs at 80/60 %
    python scripts/trace.py --load -2000    # PV export: both packs charge
    python scripts/trace.py --load 300      # below min_output: consolidation
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import conftest  # noqa: E402,F401  -- installs the HA stub when HA is absent

from custom_components.battery_management.const import (  # noqa: E402
    CONF_BIAS,
    CONF_CHARGE_LIMIT,
    CONF_DEADBAND,
    CONF_DISCHARGE_LIMIT,
    CONF_GRID_POWER,
    CONF_KP,
    CONF_MIN_OUTPUT,
    CONF_SOC_SENSOR,
    CONF_TARGET_NUMBER,
    CONF_UNIT_MAX,
    CONF_UNIT_NAME,
    CONF_UNITS,
)
from custom_components.battery_management.coordinator import (  # noqa: E402
    BatteryCoordinator,
)
from tests.conftest import FakeEntry, FakeHass, FakeState, unit_config  # noqa: E402

GRID = "sensor.p1_meter_power"


def build(args) -> tuple[BatteryCoordinator, FakeHass, list[dict]]:
    units = [
        unit_config("Batterij 01", "sim_01"),
        unit_config("Batterij 02", "sim_02"),
    ]
    states = {GRID: FakeState(args.load)}
    for cfg, soc in zip(units, args.soc):
        states[cfg[CONF_SOC_SENSOR]] = FakeState(soc)
        states[cfg[CONF_TARGET_NUMBER]] = FakeState(0, {"max": 3500})
        states[cfg[CONF_CHARGE_LIMIT]] = FakeState(100)
        states[cfg[CONF_DISCHARGE_LIMIT]] = FakeState(5)

    hass = FakeHass(states)
    entry = FakeEntry(
        {
            CONF_GRID_POWER: GRID,
            CONF_UNITS: units,
            CONF_BIAS: args.bias,
            CONF_DEADBAND: args.deadband,
            CONF_KP: args.kp,
            CONF_MIN_OUTPUT: args.min_output,
            CONF_UNIT_MAX: 3500,
        }
    )
    coordinator = BatteryCoordinator(hass, entry)
    coordinator.enabled = True
    return coordinator, hass, units


async def run(args) -> None:
    coordinator, hass, units = build(args)
    names = [u[CONF_UNIT_NAME] for u in units]

    print(
        f"house load {args.load:+} W   bias {args.bias} W   deadband "
        f"{args.deadband} W   Kp {args.kp}   min_output {args.min_output} W"
    )
    print()
    header = f"{'tick':>4} {'meter':>8} {'error':>8} {'setpoint':>9}  {'status':<12}"
    header += "".join(f"{n:>14}" for n in names)
    print(header)
    print("-" * len(header))

    for tick in range(1, args.ticks + 1):
        grid = float(hass.states.get(GRID).state)
        await coordinator._async_tick(None)

        # close the loop, exactly like the template sensor in simulator.yaml
        packs = sum(coordinator.unit_status[n].target for n in names)
        hass.states.set(GRID, args.load - packs)

        row = (
            f"{tick:>4} {grid:>8.0f} {grid - args.bias:>8.0f} "
            f"{coordinator.setpoint:>9.0f}  {coordinator.status:<12}"
        )
        for n in names:
            status = coordinator.unit_status[n]
            row += f"{status.target:>+9} {status.flow[:4] if status.flow else '-':>4}"
        print(row)

    final = float(hass.states.get(GRID).state)
    print()
    print(f"meter settles at {final:+.0f} W (target: {args.bias:+} W)")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--load", type=float, default=800, help="house load in W")
    parser.add_argument("--soc", type=float, nargs=2, default=[80, 60])
    parser.add_argument("--ticks", type=int, default=12)
    parser.add_argument("--kp", type=float, default=0.25)
    parser.add_argument("--bias", type=float, default=30)
    parser.add_argument("--deadband", type=float, default=100)
    parser.add_argument("--min-output", type=float, default=150)
    asyncio.run(run(parser.parse_args()))


if __name__ == "__main__":
    main()

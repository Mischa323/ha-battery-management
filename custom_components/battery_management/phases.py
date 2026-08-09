"""Keeping each leg of the supply under its fuse.

Pure arithmetic, no Home Assistant: the coordinator reads the sensors and this
module decides what the numbers mean, so all of it can be tested directly.

Two problems live here, and they are separate.

*The bound.* The control loop regulates the household total. The packs are
single-phase. A pack charging at 3500 W on a leg already pulling 20 A takes that
leg to 35 A, while the other two sit idle and the total looks fine. So each leg
gets its own ceiling, expressed - like every other limit in this integration -
as a bound the integrator regulates inside, not as a separate behaviour.

*The wiring.* That bound is worthless without knowing which pack is on which
leg, and nothing in the Modbus data says. It is worked out by asking one pack to
move and watching which leg moves with it.

Sign convention throughout, matching the rest of the integration: grid power is
**+ import / - export**, and our own targets are **+ discharge / - charge**. A
pack's contribution to the meter is therefore the negative of its target.
"""
from __future__ import annotations

from collections import defaultdict

from .const import PHASE_PROBE_MARGIN, PHASE_PROBE_MIN_FRACTION


def effective_limit_w(amps: float, volts: float, margin_pct: float) -> float:
    """The watts one leg may carry, in either direction.

    Either direction is not a slip: the main fuse carries the *net* current
    through it, and a leg exporting 25 A is as far into the fuse as a leg
    importing 25 A. Discharging a pack into a leg that is already exporting a
    roof full of sun is a real way to trip one.
    """
    return max(amps * volts * (1.0 - margin_pct / 100.0), 0.0)


def other_load(
    phase_power: dict[int, float], our_targets: dict[int, float]
) -> dict[int, float]:
    """What each leg would read if our packs were not on it.

    `phase = other - our_target`, because a discharge (+) subtracts from the
    meter, so `other = phase + our_target`. This is the same trick the surplus
    calculation uses, and it has the same weakness: it uses what we *commanded*,
    which per gotcha 2 is not what the packs are doing for the next 10-30 s. The
    margin on the fuse is what covers that gap.
    """
    return {p: watts + our_targets.get(p, 0.0) for p, watts in phase_power.items()}


def room(other: dict[int, float], limit_w: float) -> tuple[dict[int, float], dict[int, float]]:
    """Watts still available on each leg, as (discharge, charge).

    Discharging pushes the leg down towards export, so the room is how far it
    is from the export limit; charging pushes it up towards the import limit.
    Never negative: a leg already over its fuse offers nothing, it does not owe
    us a correction.
    """
    discharge = {p: max(watts + limit_w, 0.0) for p, watts in other.items()}
    charge = {p: max(limit_w - watts, 0.0) for p, watts in other.items()}
    return discharge, charge


def unit_ceilings(
    names: list[str],
    phase_of: dict[str, int | None],
    available: dict[int, float],
    unit_max: dict[str, float],
) -> dict[str, float]:
    """Turn per-leg room into a watt ceiling for each unit.

    Units known to share a leg split its room evenly - they are the same
    hardware, and a split that depended on the state of charge would move the
    ceiling every tick for no benefit.

    A unit whose leg is *unknown* is treated as if it were on whichever leg has
    least left, because it might be. That is deliberately the pessimistic
    reading: the alternative is guessing, and guessing wrong here drops the
    main fuse. In practice it is invisible while the house is quiet - a leg
    doing 500 W of 5175 has more room than a pack can use - and only bites when
    it was going to matter anyway.
    """
    by_phase: dict[int, list[str]] = defaultdict(list)
    unknown: list[str] = []
    for name in names:
        phase = phase_of.get(name)
        if phase is None or phase not in available:
            unknown.append(name)
        else:
            by_phase[phase].append(name)

    ceilings: dict[str, float] = {}
    claimed: dict[int, float] = {p: 0.0 for p in available}
    for phase, members in by_phase.items():
        share = available[phase] / len(members)
        for name in members:
            ceilings[name] = min(unit_max[name], max(share, 0.0))
            claimed[phase] += ceilings[name]

    if unknown:
        spare = min(
            (available[p] - claimed.get(p, 0.0) for p in available), default=0.0
        )
        share = max(spare, 0.0) / len(unknown)
        for name in unknown:
            ceilings[name] = min(unit_max[name], share)
    return ceilings


def attribute_phase(
    baseline: dict[int, float],
    during: dict[int, float],
    probe_w: float,
    charging: bool,
) -> tuple[int | None, dict]:
    """Decide which leg moved when one pack was told to.

    Returns the leg and the evidence, or `None` and the evidence. Refusing to
    answer is a normal outcome and much better than a wrong one: a pack placed
    on the wrong leg means the fuse protection guards the leg that was never in
    danger. The caller can simply try again later.

    Two conditions, and both must hold. The winning leg has to show a decent
    fraction of what we asked for - otherwise the pack never obeyed, or the
    meter has not caught up - and it has to stand clearly above the runner-up,
    or else the oven that switched on halfway through gets a vote.
    """
    # a discharge pushes the leg down, so measure it the same way up
    sign = 1.0 if charging else -1.0
    deltas = {
        p: sign * (during[p] - baseline[p]) for p in baseline if p in during
    }
    detail = {
        "deltas": {p: round(d, 1) for p, d in deltas.items()},
        "probe_w": round(probe_w, 1),
        "charging": charging,
    }
    if not deltas:
        return None, {**detail, "reason": "no_readings"}

    ranked = sorted(deltas, key=lambda p: deltas[p], reverse=True)
    winner = ranked[0]
    best = deltas[winner]
    runner_up = max(deltas[ranked[1]], 0.0) if len(ranked) > 1 else 0.0
    detail["winner"] = winner
    detail["runner_up_w"] = round(runner_up, 1)

    if best < PHASE_PROBE_MIN_FRACTION * probe_w:
        return None, {**detail, "reason": "too_small"}
    if best < PHASE_PROBE_MARGIN * runner_up:
        return None, {**detail, "reason": "not_distinct"}
    return winner, {**detail, "reason": "ok"}

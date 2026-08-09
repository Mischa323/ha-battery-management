"""Working out which pack is on which leg.

Nothing in the Modbus data says how the installer ran the cables, so it is
measured: hold everything at rest, command one pack, and see which leg moves
with it. Crude, and deliberately so - the alternative is asking the owner of a
site nobody lives at to read a meter cupboard, which is how a safety feature
ends up switched off.

The fake house here closes the loop: whatever gets commanded lands on the leg
the pack is *really* on, which is exactly what detection has to discover.
"""
from __future__ import annotations

import pytest

from custom_components.battery_management.const import (
    CONF_PHASE_DETECT,
    CONF_PHASE_REDETECT,
    FLOW_CHARGE,
    FLOW_DISCHARGE,
    PHASE_DETECT_BLOCKED,
    PHASE_DETECT_DONE,
    PHASE_DETECT_INCONCLUSIVE,
    PHASE_DETECT_MANUAL,
    PHASE_DETECT_OFF,
    PHASE_DETECT_UNKNOWN,
    POLICY_PHASE_DETECT,
)

EVEN = (("093", 50.0), ("052", 50.0))
QUIET = (400, 300, 200)


async def probe(system, on_phase: dict[str, int], *, obeys: bool = True):
    """Run one full detection pass against a house wired like `on_phase`."""
    system.wire_house(on_phase, obeys=obeys)
    await system.coordinator._async_tick(None)
    await system.run_background()


async def test_finds_each_pack_on_its_own_leg(build_system):
    system = build_system(grid=0, units=EVEN, phases=QUIET)

    await probe(system, {"Batterij 1": 3, "Batterij 2": 1})

    assert system.coordinator.unit_phase == {"Batterij 1": 3, "Batterij 2": 1}
    assert system.coordinator.phase_detection == PHASE_DETECT_DONE


async def test_finds_two_packs_sharing_one_leg(build_system):
    system = build_system(grid=0, units=EVEN, phases=QUIET)

    await probe(system, {"Batterij 1": 2, "Batterij 2": 2})

    assert system.coordinator.unit_phase == {"Batterij 1": 2, "Batterij 2": 2}


async def test_it_charges_to_probe_when_there_is_room_to(build_system):
    """A charge is a load like any other and cannot collide with an export."""
    system = build_system(grid=0, units=EVEN, phases=QUIET)

    await probe(system, {"Batterij 1": 1, "Batterij 2": 2})

    assert FLOW_CHARGE in system.hass.services.options_set().values()


async def test_it_discharges_to_probe_a_pack_that_is_already_full(build_system):
    system = build_system(grid=0, units=(("093", 100.0), ("052", 100.0)), phases=QUIET)

    await probe(system, {"Batterij 1": 1, "Batterij 2": 2})

    assert system.hass.services.options_set()[system.flow(0)] == FLOW_DISCHARGE
    assert system.coordinator.unit_phase["Batterij 1"] == 1


async def test_a_pack_with_nowhere_to_go_is_skipped_not_forced(build_system):
    """Full and at its floor at once: there is nothing safe to probe with."""
    system = build_system(
        grid=0,
        units=(("093", 50.0), ("052", 50.0)),
        charge_limit=50.0,
        discharge_limit=50.0,
        phases=QUIET,
    )

    await probe(system, {"Batterij 1": 1, "Batterij 2": 2})

    assert system.coordinator.unit_phase["Batterij 1"] is None
    assert system.coordinator.phase_probe_detail["Batterij 1"]["reason"] == "no_soc_room"


async def test_everything_is_left_at_zero_afterwards(build_system):
    """The packs have no watchdog: a probe must never walk away from a command."""
    system = build_system(grid=0, units=EVEN, phases=QUIET)

    await probe(system, {"Batterij 1": 1, "Batterij 2": 2})

    assert system.allocation() == {"Batterij 1": 0, "Batterij 2": 0}
    assert all(s.target == 0 for s in system.coordinator.unit_status.values())


async def test_it_holds_the_other_packs_still_while_probing(build_system):
    """One pack moving is the whole signal; two moving is noise."""
    system = build_system(grid=0, units=EVEN, phases=QUIET)
    seen: list[dict] = []

    system.wire_house({"Batterij 1": 1, "Batterij 2": 2})
    house = system.coordinator._sleep

    async def watch(seconds):
        seen.append(
            {n: s.target for n, s in system.coordinator.unit_status.items()}
        )
        await house(seconds)

    system.coordinator._sleep = watch
    await system.coordinator._async_tick(None)
    await system.run_background()

    # every wait sees at most one pack away from zero
    assert all(sum(1 for w in snap.values() if w) <= 1 for snap in seen)


async def test_it_declines_when_the_fuse_leaves_no_room_to_probe_with(build_system):
    """A busy evening is not the moment. Waiting is the right answer."""
    system = build_system(grid=0, units=EVEN, phases=(5000, 5000, 5000))

    await probe(system, {"Batterij 1": 1, "Batterij 2": 2})

    assert system.coordinator.unit_phase["Batterij 1"] is None
    detail = system.coordinator.phase_probe_detail["Batterij 1"]
    assert detail["reason"] == "no_fuse_room"


async def test_a_pack_that_ignores_the_command_is_not_placed(build_system):
    """No answer beats a wrong one: the guard would watch the wrong leg."""
    system = build_system(grid=0, units=EVEN, phases=QUIET)

    await probe(system, {"Batterij 1": 1, "Batterij 2": 2}, obeys=False)

    assert system.coordinator.unit_phase["Batterij 1"] is None
    assert system.coordinator.phase_detection == PHASE_DETECT_INCONCLUSIVE
    assert system.coordinator.phase_probe_detail["Batterij 1"]["reason"] == "too_small"


async def test_an_inconclusive_probe_is_retried_on_the_next_tick(build_system):
    system = build_system(grid=0, units=EVEN, phases=QUIET)
    await probe(system, {"Batterij 1": 1, "Batterij 2": 2}, obeys=False)
    assert system.coordinator.unit_phase["Batterij 1"] is None

    await probe(system, {"Batterij 1": 1, "Batterij 2": 2})

    assert system.coordinator.unit_phase["Batterij 1"] == 1


# -- when it runs -------------------------------------------------------------


async def test_a_typed_in_phase_is_never_probed_for(build_system):
    """Somebody who read the meter cupboard knows better than a measurement."""
    system = build_system(grid=0, units=EVEN, phases=QUIET, unit_phase=(2, 3))

    await system.coordinator._async_tick(None)

    assert system.hass.tasks == []
    assert system.coordinator.unit_phase == {"Batterij 1": 2, "Batterij 2": 3}
    assert system.coordinator.phase_detection == PHASE_DETECT_MANUAL


async def test_one_phase_sensor_means_one_leg_and_nothing_to_probe(build_system):
    """A single-phase house needs the fuse guard, not the detection."""
    system = build_system(grid=0, units=EVEN, phases=(500,))

    await system.coordinator._async_tick(None)

    assert system.hass.tasks == []
    assert system.coordinator.unit_phase == {"Batterij 1": 1, "Batterij 2": 1}


async def test_a_pack_that_dropped_out_is_placed_again_when_it_returns(build_system):
    """The owner's rule. A pack that disappeared is no longer provably the pack
    we measured - it could have been moved, or replaced under warranty."""
    system = build_system(grid=0, units=EVEN, phases=QUIET)
    await probe(system, {"Batterij 1": 1, "Batterij 2": 2})
    assert system.coordinator.unit_phase["Batterij 2"] == 2

    system.hass.states.remove(system.soc(1))          # unit 052 goes offline
    await system.coordinator._async_tick(None)

    assert system.coordinator.unit_phase["Batterij 2"] is None
    assert system.coordinator.unit_phase["Batterij 1"] == 1   # untouched


async def test_a_probe_never_runs_during_a_shadow_run(build_system):
    """Dry run writes nothing, and this has to write to see anything."""
    system = build_system(grid=0, units=EVEN, phases=QUIET, dry_run=True)

    await system.coordinator._async_tick(None)

    assert system.hass.tasks == []
    assert system.hass.services.calls == []
    assert system.coordinator.phase_detection == PHASE_DETECT_BLOCKED


async def test_a_probe_never_runs_while_the_coordinator_is_off(build_system):
    """Switched off means hands off, and this one moves the packs."""
    system = build_system(grid=0, units=EVEN, phases=QUIET, enabled=False)

    await system.coordinator._async_tick(None)

    assert system.hass.tasks == []


async def test_probing_can_be_switched_off_entirely(build_system):
    system = build_system(
        grid=0, units=EVEN, phases=QUIET, **{CONF_PHASE_DETECT: False}
    )

    await system.coordinator._async_tick(None)

    assert system.hass.tasks == []
    assert system.coordinator.phase_detection == PHASE_DETECT_OFF


async def test_the_loop_does_not_regulate_underneath_a_running_probe(build_system):
    """It would be measuring its own interference."""
    system = build_system(grid=-4000, units=EVEN, phases=QUIET)
    system.wire_house({"Batterij 1": 1, "Batterij 2": 2})

    await system.coordinator._async_tick(None)
    assert system.coordinator.active_policy == POLICY_PHASE_DETECT

    system.coordinator._detecting = True
    system.hass.services.clear()
    await system.coordinator._async_tick(None)

    assert system.hass.services.calls == []


async def test_the_button_asks_for_a_fresh_look(build_system):
    system = build_system(grid=0, units=EVEN, phases=QUIET)
    await probe(system, {"Batterij 1": 1, "Batterij 2": 2})
    assert system.coordinator.phase_detection == PHASE_DETECT_DONE

    system.coordinator.async_request_phase_detection()

    assert system.coordinator.unit_phase == {"Batterij 1": None, "Batterij 2": None}
    assert system.coordinator.phase_detection == PHASE_DETECT_UNKNOWN


async def test_the_button_leaves_a_typed_in_phase_alone(build_system):
    """It is re-applied every tick, so clearing it would only look like it worked."""
    system = build_system(grid=0, units=EVEN, phases=QUIET, unit_phase=(2, 0))

    system.coordinator.async_request_phase_detection()
    await system.coordinator._async_tick(None)

    assert system.coordinator.unit_phase["Batterij 1"] == 2


# -- surviving a restart ------------------------------------------------------


async def test_it_probes_again_after_a_restart_by_default(build_system):
    """The owner asked for this: an integration that has been down cannot vouch
    for what an electrician did while it was."""
    system = build_system(grid=0, units=EVEN, phases=QUIET)
    system.coordinator._store.data = {
        "enabled": True,
        "unit_phase": {"Batterij 1": 1, "Batterij 2": 2},
    }

    await system.coordinator._async_restore()

    assert system.coordinator.unit_phase == {"Batterij 1": None, "Batterij 2": None}


async def test_the_placement_can_be_kept_across_restarts(build_system):
    """Once the wiring is known, a probe every restart is a minute of packs
    parked at zero - and during development that is every few minutes."""
    system = build_system(
        grid=0, units=EVEN, phases=QUIET, **{CONF_PHASE_REDETECT: False}
    )
    system.coordinator._store.data = {
        "enabled": True,
        "unit_phase": {"Batterij 1": 1, "Batterij 2": 2},
        "phase_detected_at": 1234.0,
    }

    await system.coordinator._async_restore()

    assert system.coordinator.unit_phase == {"Batterij 1": 1, "Batterij 2": 2}
    assert system.coordinator.phase_detection == PHASE_DETECT_DONE
    assert system.coordinator.phase_detected_at == 1234.0


async def test_what_was_found_is_written_down(build_system):
    system = build_system(
        grid=0, units=EVEN, phases=QUIET, **{CONF_PHASE_REDETECT: False}
    )

    await probe(system, {"Batterij 1": 3, "Batterij 2": 1})

    assert system.coordinator._store.data["unit_phase"] == {
        "Batterij 1": 3,
        "Batterij 2": 1,
    }


async def test_the_diagnostics_carry_the_evidence(build_system):
    """A placement that landed on the wrong leg is only findable here."""
    system = build_system(grid=0, units=EVEN, phases=QUIET)

    await probe(system, {"Batterij 1": 3, "Batterij 2": 1})
    report = system.coordinator.diagnostics()

    assert report["units"][0]["phase"] == 3
    assert report["units"][0]["phase_source"] == "measured"
    assert report["state"]["phase_protection"]["detection"] == PHASE_DETECT_DONE
    assert report["state"]["phase_protection"]["probes"]["Batterij 1"]["reason"] == "ok"


async def test_unloading_cancels_a_running_probe(build_system):
    """A probe that outlives the unload commands a pack *after* the safe revert
    let go of it - and per gotcha 1 that pack then holds power for good."""
    system = build_system(grid=0, units=EVEN, phases=QUIET)
    system.wire_house({"Batterij 1": 1, "Batterij 2": 2})
    await system.coordinator._async_tick(None)
    assert system.coordinator._detecting

    cancelled = []

    class Task:
        def cancel(self):
            cancelled.append(True)

    system.coordinator._detect_task = Task()
    await system.coordinator.async_stop()

    assert cancelled == [True]
    assert system.coordinator._detecting is False
    # and the revert still happened, in the right order
    assert system.allocation() == {"Batterij 1": 0, "Batterij 2": 0}


# -- showing it -----------------------------------------------------------------


async def test_each_unit_reports_its_own_phase_as_a_state(build_system):
    """An attribute needs a template to put on a dashboard; a state does not."""
    system = build_system(grid=0, units=EVEN, phases=QUIET)

    await probe(system, {"Batterij 1": 3, "Batterij 2": 1})

    assert system.coordinator.phase_source("Batterij 1") == "measured"
    assert system.coordinator.unit_phase["Batterij 1"] == 3


async def test_a_typed_in_phase_says_so(build_system):
    """Worth distinguishing: a measurement can be wrong, a meter cupboard cannot."""
    system = build_system(grid=0, units=EVEN, phases=QUIET, unit_phase=(2, 0))

    await system.coordinator._async_tick(None)

    assert system.coordinator.phase_source("Batterij 1") == "manual"
    assert system.coordinator.phase_source("Batterij 2") == "unknown"

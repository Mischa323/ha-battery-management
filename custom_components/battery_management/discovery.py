"""Resolve a unit's six control entities from one Anker device.

Kept free of Home Assistant imports so the matching can be unit-tested without
Home Assistant installed; the config flow passes in the device's entity ids.

The matches are only ever *suggestions* - the wizard still shows them for
review. Guessing wrong and silently accepting it is how you end up with a
coordinator steering on the wrong entity.
"""
from __future__ import annotations

from .const import (
    CONF_CHARGE_LIMIT,
    CONF_CHARGE_POWER_SENSOR,
    CONF_DISCHARGE_LIMIT,
    CONF_FLOW_SELECT,
    CONF_MODE_SELECT,
    CONF_SOC_SENSOR,
    CONF_UNIT_POWER_SENSOR,
    CONF_TARGET_NUMBER,
)


def _object_id(entity_id: str) -> str:
    return entity_id.partition(".")[2]


def _score_mode(name: str) -> int:
    return 2 if "operating_mode" in name else 0


def _score_flow(name: str) -> int:
    return 2 if "grid_flow" in name else 0


def _score_target(name: str) -> int:
    if "target_grid_power" in name:
        return 3
    if "target" in name and "power" in name:
        return 2
    return 0


def _score_soc(name: str) -> int:
    if name.endswith("_soc") or name == "soc":
        return 3
    if "state_of_charge" in name:
        return 2
    return 0


def _score_power(name: str) -> int:
    """The pack's own AC power reading, for the trace only.

    `ac_output` is preferred over `battery_discharging_power` on purpose:
    gotcha 2 records that the latter can read 0 while the pack is plainly
    delivering, so it is the worse of the two for spotting a pack that is not
    following orders - which is the whole reason this field exists.
    """
    if name.endswith("_ac_output") or name == "ac_output":
        return 3
    if name.endswith("_ac_power") or name.endswith("_output_power"):
        return 2
    if "discharging_power" in name or "charging_power" in name:
        return 1
    return 0


def _score_charge_power(name: str) -> int:
    """The pack's own charging power, for the sun-versus-grid split.

    "discharging_power" contains "charging_power", so it is ruled out first -
    exactly the trap the charge-limit scorer below already sidesteps. Matching
    it would attribute every discharge to the charge counter and put a figure
    on the dashboard that is not merely wrong but backwards.
    """
    if "discharg" in name:
        return 0
    if "battery_charging_power" in name:
        return 3
    if "charging_power" in name or "charge_power" in name:
        return 2
    return 0


def _score_charge_limit(name: str) -> int:
    # "discharge_limit" contains "charge_limit", so rule it out first
    if "discharge" in name:
        return 0
    if "charging_limit" in name or "charge_limit" in name:
        return 2
    return 0


def _score_discharge_limit(name: str) -> int:
    return 2 if "discharge_limit" in name else 0


#: field -> (allowed domain, scorer)
_RULES = {
    CONF_MODE_SELECT: ("select", _score_mode),
    CONF_FLOW_SELECT: ("select", _score_flow),
    CONF_TARGET_NUMBER: ("number", _score_target),
    CONF_SOC_SENSOR: ("sensor", _score_soc),
    CONF_UNIT_POWER_SENSOR: ("sensor", _score_power),
    CONF_CHARGE_POWER_SENSOR: ("sensor", _score_charge_power),
    CONF_CHARGE_LIMIT: ("number", _score_charge_limit),
    CONF_DISCHARGE_LIMIT: ("number", _score_discharge_limit),
}


def match_unit_entities(entity_ids: list[str]) -> dict[str, str]:
    """Best guess per field. Ambiguous or absent fields are simply left out.

    A tie is treated as "no match": better to leave the picker empty than to
    pick the wrong one of two equally plausible entities.
    """
    matches: dict[str, str] = {}

    for field, (domain, score) in _RULES.items():
        best: list[str] = []
        best_score = 0
        for entity_id in entity_ids:
            if not entity_id.startswith(f"{domain}."):
                continue
            value = score(_object_id(entity_id))
            if value == 0:
                continue
            if value > best_score:
                best_score, best = value, [entity_id]
            elif value == best_score:
                best.append(entity_id)
        if len(best) == 1:
            matches[field] = best[0]

    return matches

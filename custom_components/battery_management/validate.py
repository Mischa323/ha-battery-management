"""Validation rules for the setup wizard.

Deliberately free of Home Assistant imports so the rules can be unit-tested
without Home Assistant installed. The config flow passes `hass.states.get` in
as `get_state`.

These exist because a plausible-looking mistake used to sail straight through:
pointing a unit's SoC-limit field at its own target-power entity. The
coordinator then compared a percentage against watts and, because it writes to
that same entity, fed its own output back in - the two packs ping-ponged the
whole load every single tick.
"""
from __future__ import annotations

from typing import Callable

from .const import (
    FLOW_CHARGE,
    FLOW_DISCHARGE,
    CONF_CHARGE_LIMIT,
    CONF_DISCHARGE_LIMIT,
    CONF_FLOW_SELECT,
    CONF_MODE_SELECT,
    CONF_SOC_SENSOR,
    CONF_TARGET_NUMBER,
    CONF_UNIT_NAME,
)

#: every entity field on one unit, in wizard order
ENTITY_FIELDS = (
    CONF_MODE_SELECT,
    CONF_FLOW_SELECT,
    CONF_TARGET_NUMBER,
    CONF_SOC_SENSOR,
    CONF_CHARGE_LIMIT,
    CONF_DISCHARGE_LIMIT,
)

#: the SoC-limit fields, which must be percentages
LIMIT_FIELDS = (CONF_CHARGE_LIMIT, CONF_DISCHARGE_LIMIT)

POWER_UNITS = {"W", "kW", "VA", "kVA", "Wh", "kWh"}


def limit_is_implausible(state) -> bool:
    """True when an entity clearly cannot be a state-of-charge percentage.

    Conservative on purpose: an unknown entity, or one that simply reports no
    unit, is accepted. Only reject what is definitely wrong, so an unusual but
    legitimate Anker firmware is never blocked.
    """
    if state is None:
        return False
    unit = state.attributes.get("unit_of_measurement")
    if isinstance(unit, str) and unit.strip() in POWER_UNITS:
        return True
    try:
        maximum = state.attributes.get("max")
        if maximum is not None and float(maximum) > 100:
            return True
    except (TypeError, ValueError):
        pass
    return False


#: what each select must be able to be told, or the coordinator is mute
REQUIRED_OPTIONS = {
    # The mode select is deliberately absent: which of its options means
    # "take control" and "hand back" is chosen from the entity's own list in the
    # next step, because firmwares differ - a unit without its own P1 meter does
    # not even offer self-consumption. Grid flow is checked, since charge and
    # discharge are what the coordinator writes literally.
    CONF_FLOW_SELECT: (FLOW_CHARGE, FLOW_DISCHARGE),
}


def missing_options(state, required: tuple[str, ...]) -> bool:
    """True when a select plainly cannot accept the commands we will send.

    Worth checking at setup rather than at runtime: a firmware that renames
    these leaves the coordinator writing options nobody accepts, and in dry run
    it would never find out - it is exactly the failure a shadow month cannot
    surface, because nothing is ever written.

    Permissive on purpose: an entity we cannot see, or one that does not publish
    its options, is accepted rather than blocked on a guess.
    """
    if state is None:
        return False
    options = state.attributes.get("options")
    if not isinstance(options, (list, tuple)) or not options:
        return False
    return any(option not in options for option in required)


def validate_unit(
    user_input: dict,
    other_names: list[str],
    get_state: Callable[[str], object] | None = None,
) -> dict[str, str]:
    """Return {field: error_key} for one unit's wizard input; empty means OK."""
    errors: dict[str, str] = {}

    name = str(user_input.get(CONF_UNIT_NAME) or "").strip()
    if not name:
        errors[CONF_UNIT_NAME] = "name_required"
    elif name.casefold() in {n.strip().casefold() for n in other_names}:
        # snaps/unit_status are keyed by name, so duplicates would silently
        # collapse two packs into one
        errors[CONF_UNIT_NAME] = "duplicate_name"

    # no entity may be used twice on the same unit
    seen: dict[str, str] = {}
    for field in ENTITY_FIELDS:
        entity_id = user_input.get(field)
        if not entity_id:
            continue
        if entity_id in seen:
            errors[field] = "duplicate_entity"
            errors.setdefault(seen[entity_id], "duplicate_entity")
        else:
            seen[entity_id] = field

    # the selects must accept the options we are going to send them
    if get_state is not None:
        for field, required in REQUIRED_OPTIONS.items():
            entity_id = user_input.get(field)
            if not entity_id or field in errors:
                continue
            if missing_options(get_state(entity_id), required):
                errors[field] = "missing_options"

    # the SoC limits must be percentages, not watts
    if get_state is not None:
        for field in LIMIT_FIELDS:
            entity_id = user_input.get(field)
            if not entity_id or field in errors:
                continue
            if limit_is_implausible(get_state(entity_id)):
                errors[field] = "limit_not_percentage"

    return errors

"""Switch entities: coordinator enable (kill-switch) and fast-charge."""
from __future__ import annotations

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import BatteryCoordinator


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: BatteryCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        [
            CoordinatorEnableSwitch(coordinator, entry),
            FastChargeSwitch(coordinator, entry),
            DryRunSwitch(coordinator, entry),
        ]
    )


class _BaseSwitch(SwitchEntity):
    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, coordinator: BatteryCoordinator, entry: ConfigEntry) -> None:
        self.coordinator = coordinator
        self._entry = entry
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name="Battery Management",
            manufacturer="Battery Management",
        )

    async def async_added_to_hass(self) -> None:
        self.coordinator.async_add_listener(self.async_write_ha_state)


class CoordinatorEnableSwitch(_BaseSwitch):
    _attr_name = "Coordinator enabled"
    _attr_icon = "mdi:solar-power-variant"

    def __init__(self, coordinator, entry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_enabled"

    @property
    def is_on(self) -> bool:
        return self.coordinator.enabled

    async def async_turn_on(self, **kwargs) -> None:
        await self.coordinator.async_set_enabled(True)

    async def async_turn_off(self, **kwargs) -> None:
        await self.coordinator.async_set_enabled(False)


class DryRunSwitch(_BaseSwitch):
    """Decide everything, command nothing.

    Exists so this can be watched for a month next to a site's existing
    automations before it is trusted with the packs. It blocks every write -
    targets, grid flow, the operating-mode select and the safe revert - because
    a half dry run that still set third_party_control would fight those
    automations, which is worse than not testing at all.

    The per-unit target sensors keep publishing what it *would* have done, so
    the comparison accumulates in long-term statistics on its own.
    """

    _attr_name = "Dry run"
    _attr_icon = "mdi:test-tube"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator, entry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_dry_run"

    @property
    def is_on(self) -> bool:
        return self.coordinator.dry_run

    @property
    def extra_state_attributes(self) -> dict:
        # proof of life: a shadow run that decided nothing is a broken shadow run
        return {"suppressed_commands": self.coordinator.suppressed_commands}

    async def async_turn_on(self, **kwargs) -> None:
        await self.coordinator.async_set_dry_run(True)

    async def async_turn_off(self, **kwargs) -> None:
        await self.coordinator.async_set_dry_run(False)


class FastChargeSwitch(_BaseSwitch):
    _attr_name = "Fast charge (emergency)"
    _attr_icon = "mdi:battery-charging-high"

    def __init__(self, coordinator, entry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_fast_charge"

    @property
    def is_on(self) -> bool:
        return self.coordinator.fast_charge

    async def async_turn_on(self, **kwargs) -> None:
        await self.coordinator.async_set_fast_charge(True)

    async def async_turn_off(self, **kwargs) -> None:
        await self.coordinator.async_set_fast_charge(False)

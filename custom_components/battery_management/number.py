"""Number entity: the state-of-charge reserve."""
from __future__ import annotations

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE, EntityCategory
from homeassistant.core import HomeAssistant
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
            SocReserveNumber(coordinator, entry),
            BuyCeilingMinNumber(coordinator, entry),
            BuyCeilingMaxNumber(coordinator, entry),
        ]
    )


class SocReserveNumber(NumberEntity):
    """Keep this much charge back, in every mode.

    Grid-zero regulation will happily empty the packs by late afternoon and then
    import through the evening peak. This is the floor that stops it. It raises
    each unit's own discharge limit rather than clamping the pack as a whole, so
    the SoC weighting tapers towards it instead of stopping dead.

    Defaults to 0 - off - because nothing here is mandatory.
    """

    _attr_has_entity_name = True
    _attr_should_poll = False
    _attr_name = "SoC reserve"
    _attr_icon = "mdi:battery-lock"
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_native_min_value = 0
    _attr_native_max_value = 100
    _attr_native_step = 1
    _attr_mode = NumberMode.SLIDER
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: BatteryCoordinator, entry: ConfigEntry) -> None:
        self.coordinator = coordinator
        self._attr_unique_id = f"{entry.entry_id}_soc_reserve"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name="Battery Management",
            manufacturer="Battery Management",
        )

    async def async_added_to_hass(self) -> None:
        self.coordinator.async_add_listener(self.async_write_ha_state)

    @property
    def native_value(self) -> float:
        return self.coordinator.soc_reserve

    async def async_set_native_value(self, value: float) -> None:
        await self.coordinator.async_set_soc_reserve(value)


class _CeilingNumber(NumberEntity):
    """A hand-set bound on how full grid buying may fill the packs.

    The computed ceiling leans on the solar forecast, and a site whose forecast
    reads half the real production would buy far too much. These are the knobs
    for that, adjustable while you watch what actually happened rather than
    through a reload.

    They only touch buying from the grid on cheap hours. Charging from your own
    surplus is never limited by them - that would be throwing sun away.
    """

    _attr_has_entity_name = True
    _attr_should_poll = False
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_native_min_value = 0
    _attr_native_max_value = 100
    _attr_native_step = 5
    _attr_mode = NumberMode.SLIDER
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: BatteryCoordinator, entry: ConfigEntry) -> None:
        self.coordinator = coordinator
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name="Battery Management",
            manufacturer="Battery Management",
        )

    async def async_added_to_hass(self) -> None:
        self.coordinator.async_add_listener(self.async_write_ha_state)


class BuyCeilingMinNumber(_CeilingNumber):
    _attr_name = "Buy at least to"
    _attr_icon = "mdi:arrow-collapse-up"

    def __init__(self, coordinator, entry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_buy_ceiling_min"

    @property
    def native_value(self) -> float:
        return self.coordinator.buy_ceiling_min

    async def async_set_native_value(self, value: float) -> None:
        await self.coordinator.async_set_buy_ceiling(low=value)


class BuyCeilingMaxNumber(_CeilingNumber):
    _attr_name = "Buy at most to"
    _attr_icon = "mdi:arrow-collapse-down"

    def __init__(self, coordinator, entry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_buy_ceiling_max"

    @property
    def native_value(self) -> float:
        return self.coordinator.buy_ceiling_max

    async def async_set_native_value(self, value: float) -> None:
        await self.coordinator.async_set_buy_ceiling(high=value)

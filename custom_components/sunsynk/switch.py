"""Switch platform for writable boolean Sunsynk inverter settings."""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from homeassistant.components.switch import SwitchEntity, SwitchEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import SunsynkCoordinator
from .helpers import build_device_info

if TYPE_CHECKING:
    from .tariff import TariffChargingManager


@dataclass(frozen=True)
class SunsynkSwitchEntityDescription(SwitchEntityDescription):
    setting_key: str = ""


WRITABLE_SWITCHES: tuple[SunsynkSwitchEntityDescription, ...] = (
    SunsynkSwitchEntityDescription(
        key="setting_solar_sell",
        name="Solar Sell",
        setting_key="solarSell",
        entity_category=EntityCategory.CONFIG,
    ),
    SunsynkSwitchEntityDescription(
        key="setting_battery_on",
        name="Battery On",
        setting_key="batteryOn",
        entity_category=EntityCategory.CONFIG,
    ),
    SunsynkSwitchEntityDescription(
        key="setting_time1on",
        name="Time Slot 1 On",
        setting_key="time1on",
        entity_category=EntityCategory.CONFIG,
    ),
    SunsynkSwitchEntityDescription(
        key="setting_time2on",
        name="Time Slot 2 On",
        setting_key="time2on",
        entity_category=EntityCategory.CONFIG,
    ),
    SunsynkSwitchEntityDescription(
        key="setting_time3on",
        name="Time Slot 3 On",
        setting_key="time3on",
        entity_category=EntityCategory.CONFIG,
    ),
    SunsynkSwitchEntityDescription(
        key="setting_time4on",
        name="Time Slot 4 On",
        setting_key="time4on",
        entity_category=EntityCategory.CONFIG,
    ),
    SunsynkSwitchEntityDescription(
        key="setting_time5on",
        name="Time Slot 5 On",
        setting_key="time5on",
        entity_category=EntityCategory.CONFIG,
    ),
    SunsynkSwitchEntityDescription(
        key="setting_time6on",
        name="Time Slot 6 On",
        setting_key="time6on",
        entity_category=EntityCategory.CONFIG,
    ),
    SunsynkSwitchEntityDescription(
        key="setting_monday_on",
        name="Monday Active",
        setting_key="mondayOn",
        entity_category=EntityCategory.CONFIG,
    ),
    SunsynkSwitchEntityDescription(
        key="setting_tuesday_on",
        name="Tuesday Active",
        setting_key="tuesdayOn",
        entity_category=EntityCategory.CONFIG,
    ),
    SunsynkSwitchEntityDescription(
        key="setting_wednesday_on",
        name="Wednesday Active",
        setting_key="wednesdayOn",
        entity_category=EntityCategory.CONFIG,
    ),
    SunsynkSwitchEntityDescription(
        key="setting_thursday_on",
        name="Thursday Active",
        setting_key="thursdayOn",
        entity_category=EntityCategory.CONFIG,
    ),
    SunsynkSwitchEntityDescription(
        key="setting_friday_on",
        name="Friday Active",
        setting_key="fridayOn",
        entity_category=EntityCategory.CONFIG,
    ),
    SunsynkSwitchEntityDescription(
        key="setting_saturday_on",
        name="Saturday Active",
        setting_key="saturdayOn",
        entity_category=EntityCategory.CONFIG,
    ),
    SunsynkSwitchEntityDescription(
        key="setting_sunday_on",
        name="Sunday Active",
        setting_key="sundayOn",
        entity_category=EntityCategory.CONFIG,
    ),
    SunsynkSwitchEntityDescription(
        key="setting_gen_time1on",
        name="Generator Time Slot 1 On",
        setting_key="genTime1on",
        entity_category=EntityCategory.CONFIG,
    ),
    SunsynkSwitchEntityDescription(
        key="setting_gen_time2on",
        name="Generator Time Slot 2 On",
        setting_key="genTime2on",
        entity_category=EntityCategory.CONFIG,
    ),
    SunsynkSwitchEntityDescription(
        key="setting_gen_time3on",
        name="Generator Time Slot 3 On",
        setting_key="genTime3on",
        entity_category=EntityCategory.CONFIG,
    ),
    SunsynkSwitchEntityDescription(
        key="setting_gen_time4on",
        name="Generator Time Slot 4 On",
        setting_key="genTime4on",
        entity_category=EntityCategory.CONFIG,
    ),
    SunsynkSwitchEntityDescription(
        key="setting_gen_time5on",
        name="Generator Time Slot 5 On",
        setting_key="genTime5on",
        entity_category=EntityCategory.CONFIG,
    ),
    SunsynkSwitchEntityDescription(
        key="setting_gen_time6on",
        name="Generator Time Slot 6 On",
        setting_key="genTime6on",
        entity_category=EntityCategory.CONFIG,
    ),
    SunsynkSwitchEntityDescription(
        key="setting_gen_charge_on",
        name="Generator Charge On",
        setting_key="genChargeOn",
        entity_category=EntityCategory.CONFIG,
    ),
    SunsynkSwitchEntityDescription(
        key="setting_grid_always_on",
        name="Grid Always On",
        setting_key="gridAlwaysOn",
        entity_category=EntityCategory.CONFIG,
    ),
    SunsynkSwitchEntityDescription(
        key="setting_peak_and_valley",
        name="Peak and Valley",
        setting_key="peakAndVallery",
        entity_category=EntityCategory.CONFIG,
    ),
    SunsynkSwitchEntityDescription(
        key="setting_sd_charge_on",
        name="SD Charge On",
        setting_key="sdChargeOn",
        entity_category=EntityCategory.CONFIG,
    ),
    SunsynkSwitchEntityDescription(
        key="setting_allow_remote_control",
        name="Allow Remote Control",
        setting_key="allowRemoteControl",
        entity_category=EntityCategory.CONFIG,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    from .tariff import TariffChargingManager  # lazy import — avoids blocking load at module level

    coordinator: SunsynkCoordinator = hass.data[DOMAIN][entry.entry_id]
    entities: list[SunsynkSwitchEntity | TariffManagerSwitch] = []

    for serial in coordinator.serials:
        device_info = build_device_info(coordinator, serial)
        for description in WRITABLE_SWITCHES:
            entities.append(SunsynkSwitchEntity(coordinator, serial, description, device_info))

    tariff_manager: TariffChargingManager | None = hass.data[DOMAIN].get(
        f"{entry.entry_id}_tariff"
    )
    if tariff_manager is not None:
        first_serial = coordinator.serials[0]
        device_info = build_device_info(coordinator, first_serial)
        entities.append(
            TariffManagerSwitch(entry.entry_id, tariff_manager, device_info)
        )

    async_add_entities(entities)


class SunsynkSwitchEntity(CoordinatorEntity[SunsynkCoordinator], SwitchEntity):
    """A writable boolean setting for a Sunsynk inverter."""

    entity_description: SunsynkSwitchEntityDescription
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: SunsynkCoordinator,
        serial: str,
        description: SunsynkSwitchEntityDescription,
        device_info: DeviceInfo,
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._serial = serial
        self._attr_unique_id = f"{serial}_{description.key}"
        self._attr_device_info = device_info

    @property
    def is_on(self) -> bool | None:
        settings = (self.coordinator.data or {}).get(self._serial, {}).get("settings", {})
        value = settings.get(self.entity_description.setting_key)
        if value is None:
            return None
        if isinstance(value, bool):
            return value
        if isinstance(value, int):
            return bool(value)
        return str(value).lower() in ("1", "true", "yes", "on")

    async def async_turn_on(self, **kwargs) -> None:
        await self.coordinator.async_write_setting(
            self._serial, self.entity_description.setting_key, 1
        )

    async def async_turn_off(self, **kwargs) -> None:
        await self.coordinator.async_write_setting(
            self._serial, self.entity_description.setting_key, 0
        )


class TariffManagerSwitch(SwitchEntity):
    """Enable / disable the tariff charging+discharging manager without losing config."""

    _attr_has_entity_name = True
    _attr_name = "Tariff Manager"
    _attr_icon = "mdi:currency-sign"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_should_poll = False

    def __init__(
        self,
        entry_id: str,
        manager: TariffChargingManager,
        device_info: DeviceInfo,
    ) -> None:
        self._manager = manager
        self._unsub: Any = None
        self._attr_unique_id = f"{entry_id}_tariff_enabled"
        self._attr_device_info = device_info

    async def async_added_to_hass(self) -> None:
        self._unsub = self._manager.async_add_listener(self._handle_update)

    async def async_will_remove_from_hass(self) -> None:
        if self._unsub:
            self._unsub()
            self._unsub = None

    @callback
    def _handle_update(self) -> None:
        self.async_write_ha_state()

    @property
    def is_on(self) -> bool:
        return self._manager.is_enabled

    async def async_turn_on(self, **kwargs) -> None:
        self._manager.set_enabled(True)

    async def async_turn_off(self, **kwargs) -> None:
        self._manager.set_enabled(False)

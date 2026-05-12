"""Number platform for writable Sunsynk inverter settings."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from homeassistant.components.number import (
    NumberDeviceClass,
    NumberEntity,
    NumberEntityDescription,
    NumberMode,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    PERCENTAGE,
    UnitOfElectricCurrent,
    UnitOfPower,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import SunsynkCoordinator
from .helpers import build_device_info


@dataclass(frozen=True)
class SunsynkNumberEntityDescription(NumberEntityDescription):
    setting_key: str = ""


WRITABLE_NUMBERS: tuple[SunsynkNumberEntityDescription, ...] = (
    SunsynkNumberEntityDescription(
        key="setting_battery_shutdown_cap",
        name="Battery Shutdown Capacity",
        setting_key="batteryShutdownCap",
        native_min_value=0,
        native_max_value=100,
        native_step=1,
        native_unit_of_measurement=PERCENTAGE,
        entity_category=EntityCategory.CONFIG,
        mode=NumberMode.BOX,
    ),
    SunsynkNumberEntityDescription(
        key="setting_battery_restart_cap",
        name="Battery Restart Capacity",
        setting_key="batteryRestartCap",
        native_min_value=0,
        native_max_value=100,
        native_step=1,
        native_unit_of_measurement=PERCENTAGE,
        entity_category=EntityCategory.CONFIG,
        mode=NumberMode.BOX,
    ),
    SunsynkNumberEntityDescription(
        key="setting_battery_low_cap",
        name="Battery Low Capacity",
        setting_key="batteryLowCap",
        native_min_value=0,
        native_max_value=100,
        native_step=1,
        native_unit_of_measurement=PERCENTAGE,
        entity_category=EntityCategory.CONFIG,
        mode=NumberMode.BOX,
    ),
    SunsynkNumberEntityDescription(
        key="setting_battery_max_current_charge",
        name="Battery Max Charge Current",
        setting_key="batteryMaxCurrentCharge",
        native_min_value=0,
        native_max_value=300,
        native_step=1,
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        device_class=NumberDeviceClass.CURRENT,
        entity_category=EntityCategory.CONFIG,
        mode=NumberMode.BOX,
    ),
    SunsynkNumberEntityDescription(
        key="setting_battery_max_current_discharge",
        name="Battery Max Discharge Current",
        setting_key="batteryMaxCurrentDischarge",
        native_min_value=0,
        native_max_value=300,
        native_step=1,
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        device_class=NumberDeviceClass.CURRENT,
        entity_category=EntityCategory.CONFIG,
        mode=NumberMode.BOX,
    ),
    SunsynkNumberEntityDescription(
        key="setting_charge_current",
        name="Charge Current",
        setting_key="chargeCurrent",
        native_min_value=0,
        native_max_value=300,
        native_step=1,
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        device_class=NumberDeviceClass.CURRENT,
        entity_category=EntityCategory.CONFIG,
        mode=NumberMode.BOX,
    ),
    SunsynkNumberEntityDescription(
        key="setting_discharge_current",
        name="Discharge Current",
        setting_key="dischargeCurrent",
        native_min_value=0,
        native_max_value=300,
        native_step=1,
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        device_class=NumberDeviceClass.CURRENT,
        entity_category=EntityCategory.CONFIG,
        mode=NumberMode.BOX,
    ),
    SunsynkNumberEntityDescription(
        key="setting_zero_export_power",
        name="Zero Export Power",
        setting_key="zeroExportPower",
        native_min_value=0,
        native_max_value=30000,
        native_step=10,
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=NumberDeviceClass.POWER,
        entity_category=EntityCategory.CONFIG,
        mode=NumberMode.BOX,
    ),
    SunsynkNumberEntityDescription(
        key="setting_solar_max_sell_power",
        name="Solar Max Sell Power",
        setting_key="solarMaxSellPower",
        native_min_value=0,
        native_max_value=30000,
        native_step=10,
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=NumberDeviceClass.POWER,
        entity_category=EntityCategory.CONFIG,
        mode=NumberMode.BOX,
    ),
    SunsynkNumberEntityDescription(
        key="setting_pv_max_limit",
        name="PV Max Limit",
        setting_key="pvMaxLimit",
        native_min_value=0,
        native_max_value=100,
        native_step=1,
        native_unit_of_measurement=PERCENTAGE,
        entity_category=EntityCategory.CONFIG,
        mode=NumberMode.BOX,
    ),
    SunsynkNumberEntityDescription(
        key="setting_generator_start_cap",
        name="Generator Start Capacity",
        setting_key="generatorStartCap",
        native_min_value=0,
        native_max_value=100,
        native_step=1,
        native_unit_of_measurement=PERCENTAGE,
        entity_category=EntityCategory.CONFIG,
        mode=NumberMode.BOX,
    ),
    SunsynkNumberEntityDescription(
        key="setting_gen_on_cap",
        name="Generator On Capacity",
        setting_key="genOnCap",
        native_min_value=0,
        native_max_value=100,
        native_step=1,
        native_unit_of_measurement=PERCENTAGE,
        entity_category=EntityCategory.CONFIG,
        mode=NumberMode.BOX,
    ),
    SunsynkNumberEntityDescription(
        key="setting_gen_off_cap",
        name="Generator Off Capacity",
        setting_key="genOffCap",
        native_min_value=0,
        native_max_value=100,
        native_step=1,
        native_unit_of_measurement=PERCENTAGE,
        entity_category=EntityCategory.CONFIG,
        mode=NumberMode.BOX,
    ),
    SunsynkNumberEntityDescription(
        key="setting_sell_time1_pac",
        name="Sell Time 1 Power",
        setting_key="sellTime1Pac",
        native_min_value=0,
        native_max_value=30000,
        native_step=10,
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=NumberDeviceClass.POWER,
        entity_category=EntityCategory.CONFIG,
        mode=NumberMode.BOX,
    ),
    SunsynkNumberEntityDescription(
        key="setting_sell_time2_pac",
        name="Sell Time 2 Power",
        setting_key="sellTime2Pac",
        native_min_value=0,
        native_max_value=30000,
        native_step=10,
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=NumberDeviceClass.POWER,
        entity_category=EntityCategory.CONFIG,
        mode=NumberMode.BOX,
    ),
    SunsynkNumberEntityDescription(
        key="setting_sell_time3_pac",
        name="Sell Time 3 Power",
        setting_key="sellTime3Pac",
        native_min_value=0,
        native_max_value=30000,
        native_step=10,
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=NumberDeviceClass.POWER,
        entity_category=EntityCategory.CONFIG,
        mode=NumberMode.BOX,
    ),
    SunsynkNumberEntityDescription(
        key="setting_sell_time4_pac",
        name="Sell Time 4 Power",
        setting_key="sellTime4Pac",
        native_min_value=0,
        native_max_value=30000,
        native_step=10,
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=NumberDeviceClass.POWER,
        entity_category=EntityCategory.CONFIG,
        mode=NumberMode.BOX,
    ),
    SunsynkNumberEntityDescription(
        key="setting_sell_time5_pac",
        name="Sell Time 5 Power",
        setting_key="sellTime5Pac",
        native_min_value=0,
        native_max_value=30000,
        native_step=10,
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=NumberDeviceClass.POWER,
        entity_category=EntityCategory.CONFIG,
        mode=NumberMode.BOX,
    ),
    SunsynkNumberEntityDescription(
        key="setting_sell_time6_pac",
        name="Sell Time 6 Power",
        setting_key="sellTime6Pac",
        native_min_value=0,
        native_max_value=30000,
        native_step=10,
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=NumberDeviceClass.POWER,
        entity_category=EntityCategory.CONFIG,
        mode=NumberMode.BOX,
    ),
    SunsynkNumberEntityDescription(
        key="setting_cap1",
        name="Time Slot 1 Capacity",
        setting_key="cap1",
        native_min_value=0,
        native_max_value=100,
        native_step=1,
        native_unit_of_measurement=PERCENTAGE,
        entity_category=EntityCategory.CONFIG,
        mode=NumberMode.BOX,
    ),
    SunsynkNumberEntityDescription(
        key="setting_cap2",
        name="Time Slot 2 Capacity",
        setting_key="cap2",
        native_min_value=0,
        native_max_value=100,
        native_step=1,
        native_unit_of_measurement=PERCENTAGE,
        entity_category=EntityCategory.CONFIG,
        mode=NumberMode.BOX,
    ),
    SunsynkNumberEntityDescription(
        key="setting_cap3",
        name="Time Slot 3 Capacity",
        setting_key="cap3",
        native_min_value=0,
        native_max_value=100,
        native_step=1,
        native_unit_of_measurement=PERCENTAGE,
        entity_category=EntityCategory.CONFIG,
        mode=NumberMode.BOX,
    ),
    SunsynkNumberEntityDescription(
        key="setting_cap4",
        name="Time Slot 4 Capacity",
        setting_key="cap4",
        native_min_value=0,
        native_max_value=100,
        native_step=1,
        native_unit_of_measurement=PERCENTAGE,
        entity_category=EntityCategory.CONFIG,
        mode=NumberMode.BOX,
    ),
    SunsynkNumberEntityDescription(
        key="setting_cap5",
        name="Time Slot 5 Capacity",
        setting_key="cap5",
        native_min_value=0,
        native_max_value=100,
        native_step=1,
        native_unit_of_measurement=PERCENTAGE,
        entity_category=EntityCategory.CONFIG,
        mode=NumberMode.BOX,
    ),
    SunsynkNumberEntityDescription(
        key="setting_cap6",
        name="Time Slot 6 Capacity",
        setting_key="cap6",
        native_min_value=0,
        native_max_value=100,
        native_step=1,
        native_unit_of_measurement=PERCENTAGE,
        entity_category=EntityCategory.CONFIG,
        mode=NumberMode.BOX,
    ),
    SunsynkNumberEntityDescription(
        key="setting_batt_mode",
        name="Battery Mode",
        setting_key="battMode",
        native_min_value=0,
        native_max_value=2,
        native_step=1,
        entity_category=EntityCategory.CONFIG,
        mode=NumberMode.BOX,
    ),
    SunsynkNumberEntityDescription(
        key="setting_sys_work_mode",
        name="System Work Mode",
        setting_key="sysWorkMode",
        native_min_value=0,
        native_max_value=4,
        native_step=1,
        entity_category=EntityCategory.CONFIG,
        mode=NumberMode.BOX,
    ),
    SunsynkNumberEntityDescription(
        key="setting_energy_mode",
        name="Energy Mode",
        setting_key="energyMode",
        native_min_value=0,
        native_max_value=1,
        native_step=1,
        entity_category=EntityCategory.CONFIG,
        mode=NumberMode.BOX,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: SunsynkCoordinator = hass.data[DOMAIN][entry.entry_id]
    entities: list[SunsynkNumberEntity] = []

    for serial in coordinator.serials:
        device_info = build_device_info(coordinator, serial)
        for description in WRITABLE_NUMBERS:
            entities.append(SunsynkNumberEntity(coordinator, serial, description, device_info))

    async_add_entities(entities)


class SunsynkNumberEntity(CoordinatorEntity[SunsynkCoordinator], NumberEntity):
    """A writable numeric setting for a Sunsynk inverter."""

    entity_description: SunsynkNumberEntityDescription
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: SunsynkCoordinator,
        serial: str,
        description: SunsynkNumberEntityDescription,
        device_info: DeviceInfo,
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._serial = serial
        self._attr_unique_id = f"{serial}_{description.key}"
        self._attr_device_info = device_info

    @property
    def native_value(self) -> float | None:
        settings = (self.coordinator.data or {}).get(self._serial, {}).get("settings", {})
        value = settings.get(self.entity_description.setting_key)
        if value is None:
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    async def async_set_native_value(self, value: float) -> None:
        setting_key = self.entity_description.setting_key
        int_fields = {
            "batteryShutdownCap", "batteryRestartCap", "batteryLowCap",
            "batteryMaxCurrentCharge", "batteryMaxCurrentDischarge",
            "chargeCurrent", "dischargeCurrent", "zeroExportPower",
            "solarMaxSellPower", "pvMaxLimit", "generatorStartCap",
            "genOnCap", "genOffCap", "sellTime1Pac", "sellTime2Pac",
            "sellTime3Pac", "sellTime4Pac", "sellTime5Pac", "sellTime6Pac",
            "cap1", "cap2", "cap3", "cap4", "cap5", "cap6",
            "battMode", "sysWorkMode", "energyMode",
        }
        write_value: Any = int(value) if setting_key in int_fields else value
        await self.coordinator.async_write_setting(self._serial, setting_key, write_value)

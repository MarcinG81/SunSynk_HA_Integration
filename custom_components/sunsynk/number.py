"""Number platform for writable Sunsynk inverter settings."""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

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
    UnitOfTime,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import SunsynkCoordinator
from .helpers import build_device_info

if TYPE_CHECKING:
    from .forecast_guard import ForecastExportGuard
    from .tariff import TariffChargingManager


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
        key="setting_grid_charge_current",
        name="Grid Charge Current",
        setting_key="sdBatteryCurrent",
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
        name="Slot 1 Power",
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
        name="Slot 2 Power",
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
        name="Slot 3 Power",
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
        name="Slot 4 Power",
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
        name="Slot 5 Power",
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
        name="Slot 6 Power",
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
        name="Time Slot 1 Limit",
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
        name="Time Slot 2 Limit",
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
        name="Time Slot 3 Limit",
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
        name="Time Slot 4 Limit",
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
        name="Time Slot 5 Limit",
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
        name="Time Slot 6 Limit",
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


@dataclass(frozen=True)
class TariffNumberEntityDescription:
    key: str
    name: str
    native_min_value: float
    native_max_value: float
    native_step: float
    manager_attr: str
    manager_setter: str
    native_unit_of_measurement: str | None = None
    suggested_display_precision: int = 3
    icon: str | None = None


TARIFF_NUMBERS: tuple[TariffNumberEntityDescription, ...] = (
    TariffNumberEntityDescription(
        key="cheap_threshold",
        name="Tariff Cheap Threshold",
        native_min_value=-1.0,
        native_max_value=10.0,
        native_step=0.001,
        manager_attr="_cheap_threshold",
        manager_setter="set_cheap_threshold",
        icon="mdi:arrow-down-circle-outline",
    ),
    TariffNumberEntityDescription(
        key="cheap_charge_current",
        name="Tariff Cheap Charge Current",
        native_min_value=0,
        native_max_value=500,
        native_step=1,
        manager_attr="_cheap_current",
        manager_setter="set_cheap_current",
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        suggested_display_precision=0,
        icon="mdi:battery-charging-high",
    ),
    TariffNumberEntityDescription(
        key="normal_charge_current",
        name="Tariff Normal Charge Current",
        native_min_value=0,
        native_max_value=500,
        native_step=1,
        manager_attr="_normal_charge_current",
        manager_setter="set_normal_charge_current",
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        suggested_display_precision=0,
        icon="mdi:battery-charging",
    ),
    TariffNumberEntityDescription(
        key="target_soc",
        name="Tariff Charge Target SOC",
        native_min_value=10,
        native_max_value=100,
        native_step=1,
        manager_attr="_target_soc",
        manager_setter="set_target_soc",
        native_unit_of_measurement=PERCENTAGE,
        suggested_display_precision=0,
        icon="mdi:battery-arrow-up",
    ),
    TariffNumberEntityDescription(
        key="expensive_threshold",
        name="Tariff Expensive Threshold",
        native_min_value=-1.0,
        native_max_value=10.0,
        native_step=0.001,
        manager_attr="_expensive_threshold",
        manager_setter="set_expensive_threshold",
        icon="mdi:arrow-up-circle-outline",
    ),
    TariffNumberEntityDescription(
        key="peak_discharge_current",
        name="Tariff Peak Discharge Current",
        native_min_value=0,
        native_max_value=500,
        native_step=1,
        manager_attr="_peak_discharge_current",
        manager_setter="set_peak_discharge_current",
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        suggested_display_precision=0,
        icon="mdi:battery-arrow-down",
    ),
    TariffNumberEntityDescription(
        key="normal_discharge_current",
        name="Tariff Normal Discharge Current",
        native_min_value=0,
        native_max_value=500,
        native_step=1,
        manager_attr="_normal_discharge_current",
        manager_setter="set_normal_discharge_current",
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        suggested_display_precision=0,
        icon="mdi:battery-charging-outline",
    ),
    TariffNumberEntityDescription(
        key="discharge_min_soc",
        name="Tariff Discharge Min SOC",
        native_min_value=0,
        native_max_value=90,
        native_step=1,
        manager_attr="_discharge_min_soc",
        manager_setter="set_discharge_min_soc",
        native_unit_of_measurement=PERCENTAGE,
        suggested_display_precision=0,
        icon="mdi:battery-arrow-down-outline",
    ),
)


@dataclass(frozen=True)
class ForecastGuardNumberEntityDescription:
    key: str
    name: str
    native_min_value: float
    native_max_value: float
    native_step: float
    guard_attr: str
    guard_setter: str
    native_unit_of_measurement: str | None = None
    suggested_display_precision: int = 0
    icon: str | None = None


FORECAST_GUARD_NUMBERS: tuple[ForecastGuardNumberEntityDescription, ...] = (
    ForecastGuardNumberEntityDescription(
        key="forecast_guard_margin",
        name="Forecast Guard Margin",
        native_min_value=50,
        native_max_value=200,
        native_step=1,
        guard_attr="_margin_percent",
        guard_setter="set_margin_percent",
        native_unit_of_measurement=PERCENTAGE,
        icon="mdi:shield-sun",
    ),
    ForecastGuardNumberEntityDescription(
        key="forecast_guard_sunrise_offset",
        name="Forecast Guard Sunrise Offset",
        native_min_value=0,
        native_max_value=240,
        native_step=5,
        guard_attr="_sunrise_offset_minutes",
        guard_setter="set_sunrise_offset_minutes",
        native_unit_of_measurement=UnitOfTime.MINUTES,
        icon="mdi:weather-sunset-up",
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: SunsynkCoordinator = hass.data[DOMAIN][entry.entry_id]
    entities: list[NumberEntity] = []

    for serial in coordinator.serials:
        device_info = build_device_info(coordinator, serial)
        for description in WRITABLE_NUMBERS:
            entities.append(SunsynkNumberEntity(coordinator, serial, description, device_info))
        entities.append(PlantEnergyPriceNumberEntity(coordinator, serial, device_info))

    tariff_manager: TariffChargingManager | None = hass.data[DOMAIN].get(
        f"{entry.entry_id}_tariff"
    )
    if tariff_manager is not None:
        first_serial = coordinator.serials[0]
        device_info = build_device_info(coordinator, first_serial)
        for description in TARIFF_NUMBERS:
            entities.append(
                TariffNumberEntity(entry.entry_id, tariff_manager, description, device_info)
            )

    forecast_guard: ForecastExportGuard | None = hass.data[DOMAIN].get(
        f"{entry.entry_id}_forecast_guard"
    )
    if forecast_guard is not None:
        first_serial = coordinator.serials[0]
        device_info = build_device_info(coordinator, first_serial)
        for description in FORECAST_GUARD_NUMBERS:
            entities.append(
                ForecastGuardNumberEntity(entry.entry_id, forecast_guard, description, device_info)
            )

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
            "chargeCurrent", "dischargeCurrent", "sdBatteryCurrent", "zeroExportPower",
            "solarMaxSellPower", "pvMaxLimit", "generatorStartCap",
            "genOnCap", "genOffCap", "sellTime1Pac", "sellTime2Pac",
            "sellTime3Pac", "sellTime4Pac", "sellTime5Pac", "sellTime6Pac",
            "cap1", "cap2", "cap3", "cap4", "cap5", "cap6",
            "battMode", "sysWorkMode", "energyMode",
        }
        write_value: Any = int(value) if setting_key in int_fields else value
        await self.coordinator.async_write_setting(self._serial, setting_key, write_value)


class TariffNumberEntity(NumberEntity):
    """Writable runtime parameter for the Tariff Charging Manager."""

    _attr_has_entity_name = True
    _attr_should_poll = False
    _attr_entity_category = EntityCategory.CONFIG
    _attr_mode = NumberMode.BOX

    def __init__(
        self,
        entry_id: str,
        manager: TariffChargingManager,
        description: TariffNumberEntityDescription,
        device_info: DeviceInfo,
    ) -> None:
        self._manager = manager
        self._description = description
        self._unsub: Any = None
        self._attr_unique_id = f"{entry_id}_tariff_{description.key}"
        self._attr_name = description.name
        self._attr_device_info = device_info
        self._attr_native_min_value = description.native_min_value
        self._attr_native_max_value = description.native_max_value
        self._attr_native_step = description.native_step
        self._attr_native_unit_of_measurement = description.native_unit_of_measurement
        self._attr_suggested_display_precision = description.suggested_display_precision
        self._attr_icon = description.icon

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
    def native_value(self) -> float | None:
        value = getattr(self._manager, self._description.manager_attr)
        if value is None:
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    async def async_set_native_value(self, value: float) -> None:
        getattr(self._manager, self._description.manager_setter)(value)


class PlantEnergyPriceNumberEntity(CoordinatorEntity[SunsynkCoordinator], NumberEntity):
    """Manually set a constant electricity price for the inverter's plant.

    Plant-level (not inverter-level) setting. Writing this REPLACES the
    plant's entire pricing configuration on the Sunsynk portal with a
    single Constant Price entry — any existing Time-of-Use or Live Price
    setup for that plant is overwritten. See coordinator.async_write_plant_price.
    """

    _attr_has_entity_name = True
    _attr_name = "Manual Energy Price"
    _attr_native_min_value = 0
    _attr_native_max_value = 10
    _attr_native_step = 0.0001
    _attr_mode = NumberMode.BOX
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(
        self, coordinator: SunsynkCoordinator, serial: str, device_info: DeviceInfo
    ) -> None:
        super().__init__(coordinator)
        self._serial = serial
        self._attr_unique_id = f"{serial}_plant_manual_energy_price"
        self._attr_device_info = device_info

    @property
    def native_value(self) -> float | None:
        plant = (self.coordinator.data or {}).get(self._serial, {}).get("plant", {})
        charges = plant.get("charges") or []
        if len(charges) != 1:
            return None
        try:
            return float(charges[0].get("price"))
        except (TypeError, ValueError):
            return None

    async def async_set_native_value(self, value: float) -> None:
        await self.coordinator.async_write_plant_price(self._serial, value)


class ForecastGuardNumberEntity(NumberEntity):
    """Writable config parameter for the Forecast Export Guard."""

    _attr_has_entity_name = True
    _attr_should_poll = False
    _attr_entity_category = EntityCategory.CONFIG
    _attr_mode = NumberMode.BOX

    def __init__(
        self,
        entry_id: str,
        guard: ForecastExportGuard,
        description: ForecastGuardNumberEntityDescription,
        device_info: DeviceInfo,
    ) -> None:
        self._guard = guard
        self._description = description
        self._unsub: Any = None
        self._attr_unique_id = f"{entry_id}_{description.key}"
        self._attr_name = description.name
        self._attr_device_info = device_info
        self._attr_native_min_value = description.native_min_value
        self._attr_native_max_value = description.native_max_value
        self._attr_native_step = description.native_step
        self._attr_native_unit_of_measurement = description.native_unit_of_measurement
        self._attr_suggested_display_precision = description.suggested_display_precision
        self._attr_icon = description.icon

    async def async_added_to_hass(self) -> None:
        self._unsub = self._guard.async_add_listener(self._handle_update)

    async def async_will_remove_from_hass(self) -> None:
        if self._unsub:
            self._unsub()
            self._unsub = None

    @callback
    def _handle_update(self) -> None:
        self.async_write_ha_state()

    @property
    def native_value(self) -> float | None:
        value = getattr(self._guard, self._description.guard_attr)
        if value is None:
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    async def async_set_native_value(self, value: float) -> None:
        getattr(self._guard, self._description.guard_setter)(value)

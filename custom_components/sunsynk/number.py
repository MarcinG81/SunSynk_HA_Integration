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
    UnitOfEnergy,
    UnitOfPower,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    CONF_CHEAP_CHARGE_CURRENT,
    CONF_CHEAP_TARGET_SOC,
    CONF_CHEAP_THRESHOLD,
    CONF_DISCHARGE_MIN_SOC,
    CONF_EXPENSIVE_THRESHOLD,
    CONF_NORMAL_CHARGE_CURRENT,
    CONF_NORMAL_DISCHARGE_CURRENT,
    CONF_PEAK_DISCHARGE_CURRENT,
    CONF_PLAN_BUY_KWH,
    CONF_PLAN_SELL_KWH,
    DOMAIN,
    is_deyecloud,
)
from .coordinator import SunsynkCoordinator
from .helpers import build_device_info, deyecloud_can_change, persist_runtime_option
from .write_validation import MAX_CURRENT_A

if TYPE_CHECKING:
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
        native_max_value=30000,
        native_step=10,
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=NumberDeviceClass.POWER,
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
        name="System Work Mode (number)",
        setting_key="sysWorkMode",
        # Replaced by the System Work Mode select; kept so automations that
        # use it keep working, but off on new installs.
        entity_registry_enabled_default=False,
        native_min_value=0,
        native_max_value=2,
        native_step=1,
        entity_category=EntityCategory.CONFIG,
        mode=NumberMode.BOX,
    ),
    SunsynkNumberEntityDescription(
        key="setting_energy_mode",
        name="Energy Mode (number)",
        setting_key="energyMode",
        # Replaced by the Energy Mode select; kept so automations that use it
        # keep working, but off on new installs.
        entity_registry_enabled_default=False,
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
    option_key: str
    native_unit_of_measurement: str | None = None
    suggested_display_precision: int = 3
    icon: str | None = None
    # Only created when the price plan for this direction has price entities.
    plan_direction: str | None = None


TARIFF_NUMBERS: tuple[TariffNumberEntityDescription, ...] = (
    TariffNumberEntityDescription(
        key="cheap_threshold",
        name="Tariff Cheap Threshold",
        native_min_value=-1.0,
        native_max_value=10.0,
        native_step=0.001,
        manager_attr="_cheap_threshold",
        manager_setter="set_cheap_threshold",
        option_key=CONF_CHEAP_THRESHOLD,
        icon="mdi:arrow-down-circle-outline",
    ),
    TariffNumberEntityDescription(
        key="cheap_charge_current",
        name="Tariff Cheap Charge Current",
        native_min_value=0,
        native_max_value=MAX_CURRENT_A,
        native_step=1,
        manager_attr="_cheap_current",
        manager_setter="set_cheap_current",
        option_key=CONF_CHEAP_CHARGE_CURRENT,
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        suggested_display_precision=0,
        icon="mdi:battery-charging-high",
    ),
    TariffNumberEntityDescription(
        key="normal_charge_current",
        name="Tariff Normal Charge Current",
        native_min_value=0,
        native_max_value=MAX_CURRENT_A,
        native_step=1,
        manager_attr="_normal_charge_current",
        manager_setter="set_normal_charge_current",
        option_key=CONF_NORMAL_CHARGE_CURRENT,
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
        option_key=CONF_CHEAP_TARGET_SOC,
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
        option_key=CONF_EXPENSIVE_THRESHOLD,
        icon="mdi:arrow-up-circle-outline",
    ),
    TariffNumberEntityDescription(
        key="peak_discharge_current",
        name="Tariff Peak Discharge Current",
        native_min_value=0,
        native_max_value=MAX_CURRENT_A,
        native_step=1,
        manager_attr="_peak_discharge_current",
        manager_setter="set_peak_discharge_current",
        option_key=CONF_PEAK_DISCHARGE_CURRENT,
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        suggested_display_precision=0,
        icon="mdi:battery-arrow-down",
    ),
    TariffNumberEntityDescription(
        key="normal_discharge_current",
        name="Tariff Normal Discharge Current",
        native_min_value=0,
        native_max_value=MAX_CURRENT_A,
        native_step=1,
        manager_attr="_normal_discharge_current",
        manager_setter="set_normal_discharge_current",
        option_key=CONF_NORMAL_DISCHARGE_CURRENT,
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
        option_key=CONF_DISCHARGE_MIN_SOC,
        native_unit_of_measurement=PERCENTAGE,
        suggested_display_precision=0,
        icon="mdi:battery-arrow-down-outline",
    ),
    # Changed day to day, e.g. from the solar forecast, without Configure
    # (#42). For the whole system; 0 turns that side of the plan off.
    TariffNumberEntityDescription(
        key="plan_buy_kwh",
        name="Tariff Plan kWh to Buy",
        native_min_value=0,
        native_max_value=1000,
        native_step=0.1,
        manager_attr="plan_buy_kwh",
        manager_setter="set_plan_buy_kwh",
        option_key=CONF_PLAN_BUY_KWH,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        suggested_display_precision=1,
        icon="mdi:transmission-tower-import",
        plan_direction="charge",
    ),
    TariffNumberEntityDescription(
        key="plan_sell_kwh",
        name="Tariff Plan kWh to Sell",
        native_min_value=0,
        native_max_value=1000,
        native_step=0.1,
        manager_attr="plan_sell_kwh",
        manager_setter="set_plan_sell_kwh",
        option_key=CONF_PLAN_SELL_KWH,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        suggested_display_precision=1,
        icon="mdi:transmission-tower-export",
        plan_direction="discharge",
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: SunsynkCoordinator = hass.data[DOMAIN][entry.entry_id]
    entities: list[NumberEntity] = []

    deyecloud = is_deyecloud(entry.data)
    for serial in coordinator.serials:
        device_info = build_device_info(coordinator, serial)
        for description in WRITABLE_NUMBERS:
            if deyecloud and not deyecloud_can_change(
                coordinator, serial, description.setting_key
            ):
                continue
            entities.append(
                SunsynkNumberEntity(coordinator, serial, description, device_info)
            )
        if not deyecloud:
            entities.append(
                PlantEnergyPriceNumberEntity(coordinator, serial, device_info)
            )

    tariff_manager: TariffChargingManager | None = hass.data[DOMAIN].get(
        f"{entry.entry_id}_tariff"
    )
    if tariff_manager is not None:
        first_serial = coordinator.serials[0]
        device_info = build_device_info(coordinator, first_serial)
        for description in TARIFF_NUMBERS:
            if description.plan_direction and not (
                tariff_manager.plans_configured(description.plan_direction)
            ):
                continue
            entities.append(
                TariffNumberEntity(
                    entry.entry_id, tariff_manager, description, device_info, entry
                )
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
    def available(self) -> bool:
        """Unavailable when this inverter doesn't report the setting at all.

        Not every model has every field: a Deye without per-slot sell flags
        showed Sell Time switches that could never stick (#33).
        """
        settings = (
            (self.coordinator.data or {}).get(self._serial, {}).get("settings", {})
        )
        return super().available and self.entity_description.setting_key in settings

    @property
    def native_value(self) -> float | None:
        settings = (
            (self.coordinator.data or {}).get(self._serial, {}).get("settings", {})
        )
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
            "batteryShutdownCap",
            "batteryRestartCap",
            "batteryLowCap",
            "batteryMaxCurrentCharge",
            "batteryMaxCurrentDischarge",
            "chargeCurrent",
            "dischargeCurrent",
            "sdBatteryCurrent",
            "zeroExportPower",
            "solarMaxSellPower",
            "pvMaxLimit",
            "generatorStartCap",
            "genOnCap",
            "genOffCap",
            "sellTime1Pac",
            "sellTime2Pac",
            "sellTime3Pac",
            "sellTime4Pac",
            "sellTime5Pac",
            "sellTime6Pac",
            "cap1",
            "cap2",
            "cap3",
            "cap4",
            "cap5",
            "cap6",
            "battMode",
            "sysWorkMode",
            "energyMode",
        }
        write_value: Any = int(value) if setting_key in int_fields else value
        await self.coordinator.async_write_setting(
            self._serial, setting_key, write_value
        )


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
        entry: ConfigEntry | None = None,
    ) -> None:
        self._manager = manager
        self._entry = entry
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
        if self._description.native_step >= 1:
            value = int(value)
        getattr(self._manager, self._description.manager_setter)(value)
        if self._entry is not None:
            persist_runtime_option(
                self.hass, self._entry, self._description.option_key, value
            )


class PlantEnergyPriceNumberEntity(CoordinatorEntity[SunsynkCoordinator], NumberEntity):
    """Edit an existing single constant electricity price for the plant."""

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

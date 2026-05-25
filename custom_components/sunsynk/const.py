"""Constants for the Sunsynk integration."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import (
    PERCENTAGE,
    UnitOfElectricCurrent,
    UnitOfElectricPotential,
    UnitOfEnergy,
    UnitOfFrequency,
    UnitOfPower,
    UnitOfTemperature,
)
from homeassistant.helpers.entity import EntityCategory

DOMAIN: Final = "sunsynk"

CONF_API_SERVER: Final = "api_server"
CONF_SERIALS: Final = "serials"
CONF_REFRESH_INTERVAL: Final = "refresh_interval"

API_SERVER_SUNSYNK: Final = "api.sunsynk.net"
API_SERVER_INTELESS: Final = "pv.inteless.com"

API_SERVERS: Final = {
    "Sunsynk (api.sunsynk.net)": API_SERVER_SUNSYNK,
    "Deye / Inteless (pv.inteless.com)": API_SERVER_INTELESS,
}

DEFAULT_REFRESH_INTERVAL: Final = 300  # seconds
MIN_REFRESH_INTERVAL: Final = 60
MAX_REFRESH_INTERVAL: Final = 3600

# Solar Forecast (Open-Meteo)
CONF_LATITUDE: Final = "latitude"
CONF_LONGITUDE: Final = "longitude"
CONF_PANEL_KWP: Final = "panel_kwp"
CONF_PERFORMANCE_RATIO: Final = "performance_ratio"
DEFAULT_PERFORMANCE_RATIO: Final = 0.80
SOLAR_FORECAST_UPDATE_INTERVAL: Final = 30  # minutes

# Tariff-aware charging / discharging
CONF_PRICE_ENTITY: Final = "price_entity"
CONF_CHEAP_THRESHOLD: Final = "cheap_threshold"
CONF_CHEAP_CHARGE_CURRENT: Final = "cheap_charge_current"
CONF_NORMAL_CHARGE_CURRENT: Final = "normal_charge_current"
CONF_CHEAP_TARGET_SOC: Final = "cheap_target_soc"
DEFAULT_CHEAP_TARGET_SOC: Final = 100
CONF_EXPENSIVE_THRESHOLD: Final = "expensive_threshold"
CONF_PEAK_DISCHARGE_CURRENT: Final = "peak_discharge_current"
CONF_NORMAL_DISCHARGE_CURRENT: Final = "normal_discharge_current"
CONF_DISCHARGE_MIN_SOC: Final = "discharge_min_soc"
DEFAULT_DISCHARGE_MIN_SOC: Final = 10
CONF_TARIFF_START_HOUR: Final = "tariff_start_hour"
CONF_TARIFF_END_HOUR: Final = "tariff_end_hour"
CONF_PRICE_MAX_AGE: Final = "price_max_age"
DEFAULT_PRICE_MAX_AGE: Final = 90  # minutes


@dataclass(frozen=True)
class SunsynkSensorEntityDescription(SensorEntityDescription):
    endpoint: str = ""
    data_key: str = ""


INVERTER_SENSORS: tuple[SunsynkSensorEntityDescription, ...] = (
    SunsynkSensorEntityDescription(
        key="inverter_etotal",
        name="Generation Total",
        endpoint="inverter",
        data_key="etotal",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
    ),
    SunsynkSensorEntityDescription(
        key="inverter_emonth",
        name="Generation This Month",
        endpoint="inverter",
        data_key="emonth",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
    ),
    SunsynkSensorEntityDescription(
        key="inverter_etoday",
        name="Generation Today",
        endpoint="inverter",
        data_key="etoday",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
    ),
    SunsynkSensorEntityDescription(
        key="inverter_eyear",
        name="Generation This Year",
        endpoint="inverter",
        data_key="eyear",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
    ),
    SunsynkSensorEntityDescription(
        key="inverter_pac",
        name="AC Power",
        endpoint="inverter",
        data_key="pac",
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    SunsynkSensorEntityDescription(
        key="inverter_rate_power",
        name="Rated Power",
        endpoint="inverter",
        data_key="ratePower",
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    SunsynkSensorEntityDescription(
        key="inverter_status",
        name="Status",
        endpoint="inverter",
        data_key="status",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    SunsynkSensorEntityDescription(
        key="inverter_run_status",
        name="Run Status",
        endpoint="inverter",
        data_key="runStatus",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    SunsynkSensorEntityDescription(
        key="inverter_sn",
        name="Serial Number",
        endpoint="inverter",
        data_key="sn",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    SunsynkSensorEntityDescription(
        key="inverter_alias",
        name="Alias",
        endpoint="inverter",
        data_key="alias",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    SunsynkSensorEntityDescription(
        key="inverter_gsn",
        name="GSN",
        endpoint="inverter",
        data_key="gsn",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    SunsynkSensorEntityDescription(
        key="inverter_brand",
        name="Brand",
        endpoint="inverter",
        data_key="brand",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    SunsynkSensorEntityDescription(
        key="inverter_model",
        name="Model",
        endpoint="inverter",
        data_key="model",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    SunsynkSensorEntityDescription(
        key="inverter_type",
        name="Type",
        endpoint="inverter",
        data_key="type",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    SunsynkSensorEntityDescription(
        key="inverter_equip_type",
        name="Equipment Type",
        endpoint="inverter",
        data_key="equipType",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    SunsynkSensorEntityDescription(
        key="inverter_comm_type",
        name="Communication Type",
        endpoint="inverter",
        data_key="commType",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    SunsynkSensorEntityDescription(
        key="inverter_update_at",
        name="Last Cloud Update",
        endpoint="inverter",
        data_key="updateAt",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    SunsynkSensorEntityDescription(
        key="inverter_master_ver",
        name="Firmware Master Version",
        endpoint="inverter",
        data_key="version.masterVer",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    SunsynkSensorEntityDescription(
        key="inverter_soft_ver",
        name="Firmware Soft Version",
        endpoint="inverter",
        data_key="version.softVer",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    SunsynkSensorEntityDescription(
        key="inverter_hard_ver",
        name="Firmware Hard Version",
        endpoint="inverter",
        data_key="version.hardVer",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    SunsynkSensorEntityDescription(
        key="inverter_hmi_ver",
        name="Firmware HMI Version",
        endpoint="inverter",
        data_key="version.hmiVer",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    SunsynkSensorEntityDescription(
        key="inverter_bms_ver",
        name="Firmware BMS Version",
        endpoint="inverter",
        data_key="version.bmsVer",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    SunsynkSensorEntityDescription(
        key="inverter_comm_ver",
        name="Firmware Comm Version",
        endpoint="inverter",
        data_key="version.commVer",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    SunsynkSensorEntityDescription(
        key="inverter_plant_name",
        name="Plant Name",
        endpoint="inverter",
        data_key="plant.name",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    SunsynkSensorEntityDescription(
        key="inverter_address",
        name="Address",
        endpoint="inverter",
        data_key="address",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    SunsynkSensorEntityDescription(
        key="inverter_opened",
        name="Opened",
        endpoint="inverter",
        data_key="opened",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
)

PV_SENSORS: tuple[SunsynkSensorEntityDescription, ...] = (
    SunsynkSensorEntityDescription(
        key="pv_pac",
        name="PV Total Power",
        endpoint="pv",
        data_key="pac",
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    SunsynkSensorEntityDescription(
        key="pv_etoday",
        name="PV Generation Today",
        endpoint="pv",
        data_key="etoday",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
    ),
    SunsynkSensorEntityDescription(
        key="pv_etotal",
        name="PV Generation Total",
        endpoint="pv",
        data_key="etotal",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
    ),
    SunsynkSensorEntityDescription(
        key="pv_grid_tip_power",
        name="PV Grid Tip Power",
        endpoint="pv",
        data_key="grid_tip_power",
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
    ),
)

BATTERY_SENSORS: tuple[SunsynkSensorEntityDescription, ...] = (
    SunsynkSensorEntityDescription(
        key="battery_soc",
        name="Battery SOC",
        endpoint="battery",
        data_key="soc",
        native_unit_of_measurement=PERCENTAGE,
        device_class=SensorDeviceClass.BATTERY,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    SunsynkSensorEntityDescription(
        key="battery_bms_soc",
        name="Battery BMS SOC",
        endpoint="battery",
        data_key="bmsSoc",
        native_unit_of_measurement=PERCENTAGE,
        device_class=SensorDeviceClass.BATTERY,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    SunsynkSensorEntityDescription(
        key="battery_power",
        name="Battery Power",
        endpoint="battery",
        data_key="power",
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    SunsynkSensorEntityDescription(
        key="battery_voltage",
        name="Battery Voltage",
        endpoint="battery",
        data_key="voltage",
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        device_class=SensorDeviceClass.VOLTAGE,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    SunsynkSensorEntityDescription(
        key="battery_current",
        name="Battery Current",
        endpoint="battery",
        data_key="current",
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        device_class=SensorDeviceClass.CURRENT,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    SunsynkSensorEntityDescription(
        key="battery_temp",
        name="Battery Temperature",
        endpoint="battery",
        data_key="temp",
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    SunsynkSensorEntityDescription(
        key="battery_bms_volt",
        name="Battery BMS Voltage",
        endpoint="battery",
        data_key="bmsVolt",
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        device_class=SensorDeviceClass.VOLTAGE,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    SunsynkSensorEntityDescription(
        key="battery_bms_current",
        name="Battery BMS Current",
        endpoint="battery",
        data_key="bmsCurrent",
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        device_class=SensorDeviceClass.CURRENT,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    SunsynkSensorEntityDescription(
        key="battery_bms_temp",
        name="Battery BMS Temperature",
        endpoint="battery",
        data_key="bmsTemp",
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    SunsynkSensorEntityDescription(
        key="battery_etoday_charge",
        name="Battery Charge Today",
        endpoint="battery",
        data_key="etodayChg",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
    ),
    SunsynkSensorEntityDescription(
        key="battery_etoday_discharge",
        name="Battery Discharge Today",
        endpoint="battery",
        data_key="etodayDischg",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
    ),
    SunsynkSensorEntityDescription(
        key="battery_emonth_charge",
        name="Battery Charge This Month",
        endpoint="battery",
        data_key="emonthChg",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
    ),
    SunsynkSensorEntityDescription(
        key="battery_emonth_discharge",
        name="Battery Discharge This Month",
        endpoint="battery",
        data_key="emonthDischg",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
    ),
    SunsynkSensorEntityDescription(
        key="battery_eyear_charge",
        name="Battery Charge This Year",
        endpoint="battery",
        data_key="eyearChg",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
    ),
    SunsynkSensorEntityDescription(
        key="battery_eyear_discharge",
        name="Battery Discharge This Year",
        endpoint="battery",
        data_key="eyearDischg",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
    ),
    SunsynkSensorEntityDescription(
        key="battery_etotal_charge",
        name="Battery Charge Total",
        endpoint="battery",
        data_key="etotalChg",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
    ),
    SunsynkSensorEntityDescription(
        key="battery_etotal_discharge",
        name="Battery Discharge Total",
        endpoint="battery",
        data_key="etotalDischg",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
    ),
    SunsynkSensorEntityDescription(
        key="battery_capacity",
        name="Battery Capacity",
        endpoint="battery",
        data_key="capacity",
        native_unit_of_measurement="Ah",
        state_class=SensorStateClass.MEASUREMENT,
    ),
    SunsynkSensorEntityDescription(
        key="battery_type",
        name="Battery Type",
        endpoint="battery",
        data_key="type",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    SunsynkSensorEntityDescription(
        key="battery_status",
        name="Battery Status",
        endpoint="battery",
        data_key="status",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    SunsynkSensorEntityDescription(
        key="battery_charge_volt",
        name="Battery Charge Voltage",
        endpoint="battery",
        data_key="chargeVolt",
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        device_class=SensorDeviceClass.VOLTAGE,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    SunsynkSensorEntityDescription(
        key="battery_discharge_volt",
        name="Battery Discharge Voltage",
        endpoint="battery",
        data_key="dischargeVolt",
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        device_class=SensorDeviceClass.VOLTAGE,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    SunsynkSensorEntityDescription(
        key="battery_charge_current_limit",
        name="Battery Charge Current Limit",
        endpoint="battery",
        data_key="chargeCurrentLimit",
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        device_class=SensorDeviceClass.CURRENT,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    SunsynkSensorEntityDescription(
        key="battery_discharge_current_limit",
        name="Battery Discharge Current Limit",
        endpoint="battery",
        data_key="dischargeCurrentLimit",
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        device_class=SensorDeviceClass.CURRENT,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    SunsynkSensorEntityDescription(
        key="battery_max_charge_current_limit",
        name="Battery Max Charge Current Limit",
        endpoint="battery",
        data_key="maxChargeCurrentLimit",
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        device_class=SensorDeviceClass.CURRENT,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    SunsynkSensorEntityDescription(
        key="battery_max_discharge_current_limit",
        name="Battery Max Discharge Current Limit",
        endpoint="battery",
        data_key="maxDischargeCurrentLimit",
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        device_class=SensorDeviceClass.CURRENT,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    SunsynkSensorEntityDescription(
        key="battery_number_of_batteries",
        name="Number of Batteries",
        endpoint="battery",
        data_key="numberOfBatteries",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
)

GRID_SENSORS: tuple[SunsynkSensorEntityDescription, ...] = (
    SunsynkSensorEntityDescription(
        key="grid_pac",
        name="Grid Power",
        endpoint="grid",
        data_key="pac",
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    SunsynkSensorEntityDescription(
        key="grid_qac",
        name="Grid Reactive Power",
        endpoint="grid",
        data_key="qac",
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    SunsynkSensorEntityDescription(
        key="grid_fac",
        name="Grid Frequency",
        endpoint="grid",
        data_key="fac",
        native_unit_of_measurement=UnitOfFrequency.HERTZ,
        device_class=SensorDeviceClass.FREQUENCY,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    SunsynkSensorEntityDescription(
        key="grid_pf",
        name="Grid Power Factor",
        endpoint="grid",
        data_key="pf",
        state_class=SensorStateClass.MEASUREMENT,
    ),
    SunsynkSensorEntityDescription(
        key="grid_status",
        name="Grid Status",
        endpoint="grid",
        data_key="status",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    SunsynkSensorEntityDescription(
        key="grid_ac_relay_status",
        name="Grid AC Relay Status",
        endpoint="grid",
        data_key="acRealyStatus",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    SunsynkSensorEntityDescription(
        key="grid_etoday_from",
        name="Grid Import Today",
        endpoint="grid",
        data_key="etodayFrom",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
    ),
    SunsynkSensorEntityDescription(
        key="grid_etoday_to",
        name="Grid Export Today",
        endpoint="grid",
        data_key="etodayTo",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
    ),
    SunsynkSensorEntityDescription(
        key="grid_etotal_from",
        name="Grid Import Total",
        endpoint="grid",
        data_key="etotalFrom",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
    ),
    SunsynkSensorEntityDescription(
        key="grid_etotal_to",
        name="Grid Export Total",
        endpoint="grid",
        data_key="etotalTo",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
    ),
    SunsynkSensorEntityDescription(
        key="grid_limiter_total_power",
        name="Grid Limiter Total Power",
        endpoint="grid",
        data_key="limiterTotalPower",
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
    ),
)

LOAD_SENSORS: tuple[SunsynkSensorEntityDescription, ...] = (
    SunsynkSensorEntityDescription(
        key="load_total_power",
        name="Load Total Power",
        endpoint="load",
        data_key="totalPower",
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    SunsynkSensorEntityDescription(
        key="load_total_used",
        name="Load Total Energy Used",
        endpoint="load",
        data_key="totalUsed",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
    ),
    SunsynkSensorEntityDescription(
        key="load_daily_used",
        name="Load Energy Used Today",
        endpoint="load",
        data_key="dailyUsed",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
    ),
    SunsynkSensorEntityDescription(
        key="load_frequency",
        name="Load Frequency",
        endpoint="load",
        data_key="loadFac",
        native_unit_of_measurement=UnitOfFrequency.HERTZ,
        device_class=SensorDeviceClass.FREQUENCY,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    SunsynkSensorEntityDescription(
        key="load_smart_load_status",
        name="Smart Load Status",
        endpoint="load",
        data_key="smartLoadStatus",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    SunsynkSensorEntityDescription(
        key="load_ups_power_l1",
        name="Load UPS Power L1",
        endpoint="load",
        data_key="upsPowerL1",
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    SunsynkSensorEntityDescription(
        key="load_ups_power_l2",
        name="Load UPS Power L2",
        endpoint="load",
        data_key="upsPowerL2",
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    SunsynkSensorEntityDescription(
        key="load_ups_power_l3",
        name="Load UPS Power L3",
        endpoint="load",
        data_key="upsPowerL3",
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    SunsynkSensorEntityDescription(
        key="load_ups_total_power",
        name="Load UPS Total Power",
        endpoint="load",
        data_key="upsPowerTotal",
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
    ),
)

OUTPUT_SENSORS: tuple[SunsynkSensorEntityDescription, ...] = (
    SunsynkSensorEntityDescription(
        key="inverter_in_power",
        name="Inverter Input Power",
        endpoint="output",
        data_key="pInv",
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    SunsynkSensorEntityDescription(
        key="inverter_output_power",
        name="Inverter Output Power",
        endpoint="output",
        data_key="pac",
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    SunsynkSensorEntityDescription(
        key="inverter_output_frequency",
        name="Inverter Output Frequency",
        endpoint="output",
        data_key="fac",
        native_unit_of_measurement=UnitOfFrequency.HERTZ,
        device_class=SensorDeviceClass.FREQUENCY,
        state_class=SensorStateClass.MEASUREMENT,
    ),
)

TEMP_SENSORS: tuple[SunsynkSensorEntityDescription, ...] = (
    SunsynkSensorEntityDescription(
        key="inverter_dc_temp",
        name="Inverter DC Temperature",
        endpoint="temp",
        data_key="dc_temp",
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    SunsynkSensorEntityDescription(
        key="inverter_ac_temp",
        name="Inverter AC (IGBT) Temperature",
        endpoint="temp",
        data_key="igbt_temp",
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
    ),
)

ALL_STATIC_SENSORS = (
    INVERTER_SENSORS
    + PV_SENSORS
    + BATTERY_SENSORS
    + GRID_SENSORS
    + LOAD_SENSORS
    + OUTPUT_SENSORS
    + TEMP_SENSORS
)

BATTERY_SETTING_KEYS = frozenset([
    "absorptionVolt", "battMode", "batteryCap", "batteryEfficiency",
    "batteryEmptyV", "batteryImpedance", "batteryLowCap", "batteryLowVolt",
    "batteryMaxCurrentCharge", "batteryMaxCurrentDischarge", "batteryOn",
    "batteryRestartCap", "batteryRestartVolt", "batteryShutdownCap",
    "batteryShutdownVolt", "bmsErrStop", "disableFloatCharge", "equChargeCycle",
    "equChargeTime", "equVoltCharge", "floatVolt", "genChargeOn", "genSignal",
    "generatorBatteryCurrent", "generatorForcedStart", "generatorStartCap",
    "generatorStartVolt", "gridSignal", "lithiumMode", "lowNoiseMode",
    "lowPowerMode", "safetyType", "sdBatteryCurrent", "sdChargeOn", "sdStartCap",
    "sdStartVolt", "signalIslandModeEnable", "sn", "tempco", "chargeCurrent",
    "dischargeCurrent", "chargeVolt", "dischargeVolt",
])

SYSTEM_MODE_SETTING_KEYS = frozenset([
    "sn", "safetyType", "battMode", "solarSell", "pvMaxLimit", "energyMode",
    "peakAndVallery", "sysWorkMode",
    "sellTime1", "sellTime2", "sellTime3", "sellTime4", "sellTime5", "sellTime6",
    "sellTime1En", "sellTime2En", "sellTime3En", "sellTime4En", "sellTime5En", "sellTime6En",
    "sellTime1Pac", "sellTime2Pac",
    "sellTime3Pac", "sellTime4Pac", "sellTime5Pac", "sellTime6Pac",
    "cap1", "cap2", "cap3", "cap4", "cap5", "cap6",
    "sellTime1Volt", "sellTime2Volt", "sellTime3Volt", "sellTime4Volt",
    "sellTime5Volt", "sellTime6Volt", "zeroExportPower", "solarMaxSellPower",
    "mondayOn", "tuesdayOn", "wednesdayOn", "thursdayOn", "fridayOn",
    "saturdayOn", "sundayOn", "time1on", "time2on", "time3on", "time4on",
    "time5on", "time6on", "genTime1on", "genTime2on", "genTime3on",
    "genTime4on", "genTime5on", "genTime6on",
])

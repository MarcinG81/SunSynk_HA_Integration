"""Sensor platform for Sunsynk integration."""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    PERCENTAGE,
    UnitOfElectricCurrent,
    UnitOfElectricPotential,
    UnitOfEnergy,
    UnitOfIrradiance,
    UnitOfPower,
    UnitOfTemperature,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from .const import (
    ALL_STATIC_SENSORS,
    DOMAIN,
    SunsynkSensorEntityDescription,
)
from .coordinator import SolarForecastCoordinator, SunsynkCoordinator
from .helpers import build_device_info

if TYPE_CHECKING:
    from .tariff import TariffChargingManager
    from .virtual_slots import VirtualSlotScheduler


@dataclass(frozen=True)
class ForecastSensorDescription(SensorEntityDescription):
    forecast_key: str = ""


FORECAST_SENSOR_DESCRIPTIONS: tuple[ForecastSensorDescription, ...] = (
    ForecastSensorDescription(
        key="forecast_today_kwh",
        name="Today",
        forecast_key="today_kwh",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        state_class=None,
        suggested_display_precision=2,
    ),
    ForecastSensorDescription(
        key="forecast_tomorrow_kwh",
        name="Tomorrow",
        forecast_key="tomorrow_kwh",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        state_class=None,
        suggested_display_precision=2,
    ),
    ForecastSensorDescription(
        key="forecast_cloud_cover",
        name="Cloud Cover",
        forecast_key="cloud_cover",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
    ),
    ForecastSensorDescription(
        key="forecast_precipitation",
        name="Precipitation",
        forecast_key="precipitation",
        native_unit_of_measurement="mm",
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
    ),
    ForecastSensorDescription(
        key="forecast_ghi",
        name="GHI",
        forecast_key="ghi",
        native_unit_of_measurement=UnitOfIrradiance.WATTS_PER_SQUARE_METER,
        device_class=SensorDeviceClass.IRRADIANCE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
    ),
    ForecastSensorDescription(
        key="forecast_dni",
        name="DNI",
        forecast_key="dni",
        native_unit_of_measurement=UnitOfIrradiance.WATTS_PER_SQUARE_METER,
        device_class=SensorDeviceClass.IRRADIANCE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
    ),
    ForecastSensorDescription(
        key="forecast_performance_ratio",
        name="Performance Ratio (Calibrated)",
        forecast_key="performance_ratio",
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: SunsynkCoordinator = hass.data[DOMAIN][entry.entry_id]

    registered_uids: set[str] = set()
    entities: list[SensorEntity] = []

    for serial in coordinator.serials:
        device_info = build_device_info(coordinator, serial)
        for description in ALL_STATIC_SENSORS:
            uid = f"{serial}_{description.key}"
            registered_uids.add(uid)
            entities.append(SunsynkSensor(coordinator, serial, description, device_info))
        entities.append(InverterInternalPowerSensor(coordinator, serial, device_info))
        entities.append(PlantEnergyPriceSensor(coordinator, serial, device_info))

    async_add_entities(entities)

    @callback
    def _add_dynamic_sensors() -> None:
        """Called on each coordinator update to register newly-discovered dynamic sensors."""
        new_entities: list[SunsynkSensor] = []
        for serial in coordinator.serials:
            device_info = build_device_info(coordinator, serial)
            for desc in _build_dynamic_descriptions(coordinator, serial):
                uid = f"{serial}_{desc.key}"
                if uid not in registered_uids:
                    registered_uids.add(uid)
                    new_entities.append(SunsynkSensor(coordinator, serial, desc, device_info))
        if new_entities:
            async_add_entities(new_entities)

    entry.async_on_unload(coordinator.async_add_listener(_add_dynamic_sensors))
    _add_dynamic_sensors()

    forecast_coordinator: SolarForecastCoordinator | None = hass.data[DOMAIN].get(
        f"{entry.entry_id}_forecast"
    )
    if forecast_coordinator is not None:
        forecast_device = DeviceInfo(
            identifiers={(DOMAIN, f"{entry.entry_id}_forecast")},
            name="Solar Forecast",
            manufacturer="Open-Meteo",
            model="Weather Forecast",
        )
        async_add_entities([
            SolarForecastSensor(forecast_coordinator, entry.entry_id, desc, forecast_device)
            for desc in FORECAST_SENSOR_DESCRIPTIONS
        ])

    tariff_manager: TariffChargingManager | None = hass.data[DOMAIN].get(
        f"{entry.entry_id}_tariff"
    )
    if tariff_manager is not None:
        first_serial = coordinator.serials[0]
        device_info = build_device_info(coordinator, first_serial)
        async_add_entities([
            TariffStateSensor(entry.entry_id, tariff_manager, device_info),
            TariffPriceQualitySensor(entry.entry_id, tariff_manager, device_info),
        ])

    vslot_scheduler: VirtualSlotScheduler | None = hass.data[DOMAIN].get(
        f"{entry.entry_id}_vslots"
    )
    if vslot_scheduler is not None:
        first_serial = coordinator.serials[0]
        device_info = build_device_info(coordinator, first_serial)
        async_add_entities([
            VirtualSlotStateSensor(entry.entry_id, vslot_scheduler, device_info),
        ])



def _build_dynamic_descriptions(
    coordinator: SunsynkCoordinator, serial: str
) -> list[SunsynkSensorEntityDescription]:
    """Build sensor descriptions for MPPT strings and grid/load/output phases."""
    descriptions: list[SunsynkSensorEntityDescription] = []
    serial_data = (coordinator.data or {}).get(serial, {})

    mppts = serial_data.get("pv", {}).get("pvIV", [])
    for idx in range(len(mppts)):
        for field_key, name_suffix, unit, dev_class in [
            ("ppv", "Power", UnitOfPower.WATT, SensorDeviceClass.POWER),
            ("vpv", "Voltage", UnitOfElectricPotential.VOLT, SensorDeviceClass.VOLTAGE),
            ("ipv", "Current", UnitOfElectricCurrent.AMPERE, SensorDeviceClass.CURRENT),
        ]:
            descriptions.append(SunsynkSensorEntityDescription(
                key=f"pv_mppt{idx}_{field_key}",
                name=f"PV MPPT {idx + 1} {name_suffix}",
                endpoint="pv",
                data_key=f"pvIV.{idx}.{field_key}",
                native_unit_of_measurement=unit,
                device_class=dev_class,
                state_class=SensorStateClass.MEASUREMENT,
            ))

    for endpoint, prefix, label in [
        ("grid", "grid_phase", "Grid Phase"),
        ("load", "load_phase", "Load Phase"),
        ("output", "inverter_phase", "Inverter Phase"),
    ]:
        phases = serial_data.get(endpoint, {}).get("vip", [])
        for idx in range(len(phases)):
            for field_key, name_suffix, unit, dev_class in [
                ("volt", "Voltage", UnitOfElectricPotential.VOLT, SensorDeviceClass.VOLTAGE),
                ("current", "Current", UnitOfElectricCurrent.AMPERE, SensorDeviceClass.CURRENT),
                ("power", "Power", UnitOfPower.WATT, SensorDeviceClass.POWER),
            ]:
                descriptions.append(SunsynkSensorEntityDescription(
                    key=f"{prefix}{idx}_{field_key}",
                    name=f"{label} {idx + 1} {name_suffix}",
                    endpoint=endpoint,
                    data_key=f"vip.{idx}.{field_key}",
                    native_unit_of_measurement=unit,
                    device_class=dev_class,
                    state_class=SensorStateClass.MEASUREMENT,
                ))

    # Individual battery pack sensors — only added when the slot reports a non-zero voltage,
    # meaning a physical battery is present and communicating at that slot.
    battery_data = serial_data.get("battery", {})
    for slot in (1, 2):
        volt_key = f"batteryVolt{slot}"
        if (battery_data.get(volt_key) or 0) > 0:
            for field_key, name_suffix, api_key, unit, dev_class in [
                ("soc", "SOC", f"batterySoc{slot}", PERCENTAGE, SensorDeviceClass.BATTERY),
                ("voltage", "Voltage", f"batteryVolt{slot}", UnitOfElectricPotential.VOLT, SensorDeviceClass.VOLTAGE),
                ("current", "Current", f"batteryCurrent{slot}", UnitOfElectricCurrent.AMPERE, SensorDeviceClass.CURRENT),
                ("power", "Power", f"batteryPower{slot}", UnitOfPower.WATT, SensorDeviceClass.POWER),
                ("temp", "Temperature", f"batteryTemp{slot}", UnitOfTemperature.CELSIUS, SensorDeviceClass.TEMPERATURE),
            ]:
                descriptions.append(SunsynkSensorEntityDescription(
                    key=f"battery_slot{slot}_{field_key}",
                    name=f"Battery {slot} {name_suffix}",
                    endpoint="battery",
                    data_key=api_key,
                    native_unit_of_measurement=unit,
                    device_class=dev_class,
                    state_class=SensorStateClass.MEASUREMENT,
                ))

    return descriptions


def _resolve_value(data: dict[str, Any], path: str) -> Any:
    """Resolve a dot-notation path like 'pvIV.0.ppv' from a data dict."""
    parts = path.split(".")
    current: Any = data
    for part in parts:
        if current is None:
            return None
        if isinstance(current, list):
            try:
                current = current[int(part)]
            except (IndexError, ValueError):
                return None
        elif isinstance(current, dict):
            current = current.get(part)
        else:
            return None
    return current


class SunsynkSensor(CoordinatorEntity[SunsynkCoordinator], SensorEntity):
    """A single Sunsynk sensor entity."""

    entity_description: SunsynkSensorEntityDescription
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: SunsynkCoordinator,
        serial: str,
        description: SunsynkSensorEntityDescription,
        device_info: DeviceInfo,
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._serial = serial
        self._attr_unique_id = f"{serial}_{description.key}"
        self._attr_device_info = device_info

    @property
    def native_value(self) -> Any:
        serial_data = (self.coordinator.data or {}).get(self._serial, {})
        endpoint_data = serial_data.get(self.entity_description.endpoint, {})
        if not endpoint_data:
            return None

        if self.entity_description.value_fn is not None:
            return self.entity_description.value_fn(endpoint_data)

        value = _resolve_value(endpoint_data, self.entity_description.data_key)
        if (value is None or value == "") and self.entity_description.fallback_data_key:
            value = _resolve_value(endpoint_data, self.entity_description.fallback_data_key)
        if value is None:
            return None

        try:
            return float(value)
        except (TypeError, ValueError):
            # Numeric sensors (state_class set) must report None rather than a raw
            # string like "--" — the API returns that placeholder for fields that
            # don't apply to a given inverter, and HA rejects non-numeric values
            # for measurement/total sensors.
            if self.entity_description.state_class is not None:
                return None
            return str(value) if value != "" else None


class InverterInternalPowerSensor(CoordinatorEntity[SunsynkCoordinator], SensorEntity):
    """Computed: PV + grid + battery power minus load power.

    Approximates conversion/internal loss inside the inverter — the
    portion of power that isn't accounted for by any of the four
    measured flows. Not a value the API reports directly.
    """

    _attr_has_entity_name = True
    _attr_name = "Internal Power"
    _attr_native_unit_of_measurement = UnitOfPower.WATT
    _attr_device_class = SensorDeviceClass.POWER
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(
        self, coordinator: SunsynkCoordinator, serial: str, device_info: DeviceInfo
    ) -> None:
        super().__init__(coordinator)
        self._serial = serial
        self._attr_unique_id = f"{serial}_inverter_internal_power"
        self._attr_device_info = device_info

    @property
    def native_value(self) -> float | None:
        serial_data = (self.coordinator.data or {}).get(self._serial, {})
        try:
            pv = float(serial_data.get("pv", {}).get("pac"))
            grid = float(serial_data.get("grid", {}).get("pac"))
            battery = float(serial_data.get("battery", {}).get("power"))
            load = float(serial_data.get("load", {}).get("totalPower"))
        except (TypeError, ValueError):
            return None
        return round(pv + grid + battery - load, 3)


_CHARGE_TYPE_LABELS = {1: "constant", 2: "time_of_use", 3: "live_price"}


def _active_plant_charge(charges: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Pick the currently-active pricing charge from a plant's charges list.

    Constant Price (type 1) plants have a single entry that's always
    active. Time of Use (type 2) plants have up to 6 slots matched by
    time-of-day, HH:MM strings compared lexically (safe for zero-padded
    24h format). Live Price (type 3) has no fixed price to report here.
    """
    candidates = [c for c in (charges or []) if c.get("type") != 3]
    if not candidates:
        return None
    if len(candidates) == 1:
        return candidates[0]

    now_hhmm = dt_util.now().strftime("%H:%M")
    for charge in candidates:
        start = charge.get("startRange") or "00:00"
        end = charge.get("endRange") or "24:00"
        if start <= now_hhmm < end:
            return charge
    return candidates[0]


class PlantEnergyPriceSensor(CoordinatorEntity[SunsynkCoordinator], SensorEntity):
    """Currently-active electricity price from the plant's pricing config.

    Plant-level data (fetched via a different API endpoint than the rest
    of this integration) — unavailable if the inverter's plant ID
    couldn't be determined or the plant has no pricing configured.
    """

    _attr_has_entity_name = True
    _attr_name = "Current Energy Price"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_suggested_display_precision = 4

    def __init__(
        self, coordinator: SunsynkCoordinator, serial: str, device_info: DeviceInfo
    ) -> None:
        super().__init__(coordinator)
        self._serial = serial
        self._attr_unique_id = f"{serial}_plant_energy_price"
        self._attr_device_info = device_info

    def _charge(self) -> dict[str, Any] | None:
        plant = (self.coordinator.data or {}).get(self._serial, {}).get("plant", {})
        return _active_plant_charge(plant.get("charges") or [])

    @property
    def native_value(self) -> float | None:
        charge = self._charge()
        if charge is None:
            return None
        try:
            return float(charge.get("price"))
        except (TypeError, ValueError):
            return None

    @property
    def native_unit_of_measurement(self) -> str | None:
        plant = (self.coordinator.data or {}).get(self._serial, {}).get("plant", {})
        currency = (plant.get("currency") or {}).get("code")
        return f"{currency}/kWh" if currency else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        plant = (self.coordinator.data or {}).get(self._serial, {}).get("plant", {})
        charge = self._charge()
        attrs: dict[str, Any] = {"plant_id": plant.get("id")}
        if charge is not None:
            attrs["type"] = _CHARGE_TYPE_LABELS.get(charge.get("type"), charge.get("type"))
            attrs["start_range"] = charge.get("startRange")
            attrs["end_range"] = charge.get("endRange")
        return attrs


class SolarForecastSensor(CoordinatorEntity[SolarForecastCoordinator], SensorEntity):
    """A single solar forecast sensor backed by Open-Meteo data."""

    entity_description: ForecastSensorDescription
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: SolarForecastCoordinator,
        entry_id: str,
        description: ForecastSensorDescription,
        device_info: DeviceInfo,
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{entry_id}_forecast_{description.key}"
        self._attr_device_info = device_info

    @property
    def native_value(self) -> float | None:
        if self.coordinator.data is None:
            return None
        return self.coordinator.data.get(self.entity_description.forecast_key)


class TariffStateSensor(SensorEntity):
    """Sensor reporting the current operating mode of the tariff manager."""

    _attr_has_entity_name = True
    _attr_name = "Tariff Mode"
    _attr_icon = "mdi:lightning-bolt"
    _attr_should_poll = False

    def __init__(
        self,
        entry_id: str,
        manager: TariffChargingManager,
        device_info: DeviceInfo,
    ) -> None:
        self._manager = manager
        self._attr_unique_id = f"{entry_id}_tariff_mode"
        self._attr_device_info = device_info
        self._unsub: Any = None

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
    def native_value(self) -> str:
        return self._manager.mode

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        attrs: dict[str, Any] = {"price_entity": self._manager.price_entity}
        if self._manager.cheap_threshold is not None:
            attrs["cheap_threshold"] = self._manager.cheap_threshold
        if self._manager.expensive_threshold is not None:
            attrs["expensive_threshold"] = self._manager.expensive_threshold
        if self._manager.start_hour is not None:
            attrs["active_hours"] = f"{self._manager.start_hour:02d}:00–{self._manager.end_hour:02d}:00"
        return attrs


class TariffPriceQualitySensor(SensorEntity):
    """Diagnostic sensor reporting price data quality for the tariff manager.

    States: ok | unavailable | not_found | invalid | stale
    """

    _attr_has_entity_name = True
    _attr_name = "Tariff Price Quality"
    _attr_icon = "mdi:check-circle"
    _attr_should_poll = False
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(
        self,
        entry_id: str,
        manager: TariffChargingManager,
        device_info: DeviceInfo,
    ) -> None:
        self._manager = manager
        self._attr_unique_id = f"{entry_id}_tariff_price_quality"
        self._attr_device_info = device_info
        self._unsub: Any = None

    async def async_added_to_hass(self) -> None:
        self._unsub = self._manager.async_add_listener(self._handle_update)

    async def async_will_remove_from_hass(self) -> None:
        if self._unsub:
            self._unsub()
            self._unsub = None

    @callback
    def _handle_update(self) -> None:
        quality = self._manager.price_quality
        self._attr_icon = "mdi:check-circle" if quality == "ok" else "mdi:alert-circle"
        self.async_write_ha_state()

    @property
    def native_value(self) -> str:
        return self._manager.price_quality

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        attrs: dict[str, Any] = {"price_entity": self._manager.price_entity}
        if self._manager.price_max_age_minutes is not None:
            attrs["max_age_minutes"] = self._manager.price_max_age_minutes
        # Attach live sensor details when available
        state = self.hass.states.get(self._manager.price_entity) if self.hass else None
        if state is not None:
            attrs["last_updated"] = state.last_updated.isoformat()
            attrs["current_state"] = state.state
        return attrs


class VirtualSlotStateSensor(SensorEntity):
    """Reports what the virtual slot scheduler is currently doing and why.

    State is the resolution source: none | price_override | virtual_slot:<id>.
    """

    _attr_has_entity_name = True
    _attr_name = "Virtual Slot Scheduler"
    _attr_icon = "mdi:calendar-clock"
    _attr_should_poll = False
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(
        self,
        entry_id: str,
        scheduler: VirtualSlotScheduler,
        device_info: DeviceInfo,
    ) -> None:
        self._scheduler = scheduler
        self._attr_unique_id = f"{entry_id}_vslots_state"
        self._attr_device_info = device_info
        self._unsub: Any = None

    async def async_added_to_hass(self) -> None:
        self._unsub = self._scheduler.async_add_listener(self._handle_update)

    async def async_will_remove_from_hass(self) -> None:
        if self._unsub:
            self._unsub()
            self._unsub = None

    @callback
    def _handle_update(self) -> None:
        self.async_write_ha_state()

    @property
    def native_value(self) -> str:
        if not self._scheduler.is_enabled:
            return "disabled"
        return self._scheduler.active_source

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        next_boundary = self._scheduler.next_boundary
        return {
            "current_physical_slot": self._scheduler.current_physical_slot,
            "next_boundary": next_boundary.isoformat() if next_boundary else None,
            "virtual_slots": self._scheduler.list_slots(),
        }

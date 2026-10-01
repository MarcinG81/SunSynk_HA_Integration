"""Exercise HA platform setup and entity adapters end to end."""
from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.sunsynk.const import DOMAIN, SunsynkSensorEntityDescription
from custom_components.sunsynk.number import (
    PlantEnergyPriceNumberEntity,
    SunsynkNumberEntity,
    TariffNumberEntity,
    async_setup_entry as setup_numbers,
)
from custom_components.sunsynk.sensor import (
    PlantEnergyPriceSensor,
    SolarForecastSensor,
    SunsynkSensor,
    TariffPriceQualitySensor,
    TariffStateSensor,
    VirtualSlotStateSensor,
    _active_plant_charge,
    _resolve_value,
    async_setup_entry as setup_sensors,
)
from custom_components.sunsynk.switch import (
    SunsynkSwitchEntity,
    TariffManagerSwitch,
    VirtualSlotSchedulerSwitch,
    async_setup_entry as setup_switches,
)
from custom_components.sunsynk.text import SunsynkTextEntity, async_setup_entry as setup_texts


@pytest.fixture
def platform_context():
    coordinator = MagicMock()
    coordinator.serials = ["INV1", "INV2"]
    coordinator.data = {
        "INV1": {
            "inverter": {"sn": "INV1", "alias": "One"},
            "settings": {"chargeCurrent": "42", "solarSell": "true", "sellTime1": "06:30"},
            "pv": {"pac": "123", "pvIV": [{"ppv": 100, "vpv": 200, "ipv": 0.5}]},
            "grid": {"vip": [{"volt": 230, "current": 2, "power": 460}]},
            "load": {"vip": [{"volt": 230, "current": 1, "power": 230}]},
            "output": {"vip": [{"volt": 230, "current": 1, "power": 230}]},
            "battery": {"batteryVolt1": 52, "soc": 50},
            "flow": {"existsGen": True, "genPower": 10, "existsMin": True, "minPower": 20},
            "plant": {"charges": [{"price": 0.2, "type": 1}]},
        },
        "INV2": {"inverter": {"sn": "INV2"}, "settings": {}},
    }
    coordinator.async_write_setting = AsyncMock()
    coordinator.async_write_plant_price = AsyncMock()
    coordinator.async_add_listener.return_value = MagicMock()

    forecast = MagicMock(data={"today_kwh": 5.0})
    tariff = MagicMock(
        is_enabled=True,
        mode="idle",
        per_inverter_modes={"INV1": "idle", "INV2": "idle"},
        price_entity="sensor.buy",
        export_price_entity="sensor.sell",
        cheap_threshold=0.1,
        expensive_threshold=0.3,
        start_hour=22,
        end_hour=6,
        price_quality="ok",
        export_price_quality="stale",
        price_max_age_minutes=90,
        soc_quality_by_inverter={"INV1": "ok"},
        _cheap_threshold=0.1,
    )
    tariff.async_add_listener.return_value = MagicMock()
    scheduler = MagicMock(
        is_enabled=True,
        active_source="virtual_slot:1",
        current_physical_slot=1,
        next_boundary=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    scheduler.async_add_listener.return_value = MagicMock()
    scheduler.list_slots.return_value = [{"slot_id": 1}]
    hass = SimpleNamespace(
        data={
            DOMAIN: {
                "entry": coordinator,
                "entry_forecast": forecast,
                "entry_tariff": tariff,
                "entry_vslots": {"INV1": scheduler},
            }
        }
    )
    entry = MagicMock(entry_id="entry")
    return hass, entry, coordinator, forecast, tariff, scheduler


@pytest.mark.asyncio
async def test_all_platforms_setup_for_two_inverters(platform_context):
    hass, entry, coordinator, _forecast, _tariff, _scheduler = platform_context
    added: list[object] = []

    def add(entities: list[object]) -> None:
        added.extend(entities)

    await setup_numbers(hass, entry, add)
    await setup_switches(hass, entry, add)
    await setup_texts(hass, entry, add)
    await setup_sensors(hass, entry, add)

    assert any(isinstance(entity, SunsynkNumberEntity) for entity in added)
    assert any(isinstance(entity, SunsynkSwitchEntity) for entity in added)
    assert any(isinstance(entity, SunsynkTextEntity) for entity in added)
    assert any(isinstance(entity, SunsynkSensor) for entity in added)
    assert any(isinstance(entity, SolarForecastSensor) for entity in added)
    assert any(isinstance(entity, TariffStateSensor) for entity in added)
    assert any(isinstance(entity, VirtualSlotStateSensor) for entity in added)
    entry.async_on_unload.assert_called()
    coordinator.async_add_listener.call_args.args[0]()


@pytest.mark.asyncio
async def test_number_switch_and_text_entity_adapters(platform_context):
    _hass, _entry, coordinator, _forecast, tariff, scheduler = platform_context
    device = MagicMock()

    number = SunsynkNumberEntity(
        coordinator,
        "INV1",
        next(d for d in __import__("custom_components.sunsynk.number", fromlist=["WRITABLE_NUMBERS"]).WRITABLE_NUMBERS if d.setting_key == "chargeCurrent"),
        device,
    )
    assert number.native_value == 42.0
    await number.async_set_native_value(27.9)
    coordinator.async_write_setting.assert_awaited_with("INV1", "chargeCurrent", 27)
    coordinator.data["INV1"]["settings"]["chargeCurrent"] = "bad"
    assert number.native_value is None
    coordinator.data["INV1"]["settings"].pop("chargeCurrent")
    assert number.native_value is None

    tariff_number = TariffNumberEntity(
        "entry",
        tariff,
        __import__("custom_components.sunsynk.number", fromlist=["TARIFF_NUMBERS"]).TARIFF_NUMBERS[0],
        device,
    )
    await tariff_number.async_added_to_hass()
    tariff_number.async_write_ha_state = MagicMock()
    tariff_number._handle_update()
    assert tariff_number.native_value == 0.1
    await tariff_number.async_set_native_value(0.2)
    tariff.set_cheap_threshold.assert_called_once_with(0.2)
    await tariff_number.async_will_remove_from_hass()
    tariff_number._manager._cheap_threshold = None
    assert tariff_number.native_value is None
    tariff_number._manager._cheap_threshold = "bad"
    assert tariff_number.native_value is None

    switch = SunsynkSwitchEntity(
        coordinator,
        "INV1",
        __import__("custom_components.sunsynk.switch", fromlist=["WRITABLE_SWITCHES"]).WRITABLE_SWITCHES[0],
        device,
    )
    assert switch.is_on is True
    await switch.async_turn_on()
    await switch.async_turn_off()
    coordinator.data["INV1"]["settings"]["solarSell"] = 0
    assert switch.is_on is False
    coordinator.data["INV1"]["settings"]["solarSell"] = None
    assert switch.is_on is None
    coordinator.data["INV1"]["settings"]["solarSell"] = True
    assert switch.is_on is True

    tariff_switch = TariffManagerSwitch("entry", tariff, device)
    await tariff_switch.async_added_to_hass()
    tariff_switch.async_write_ha_state = MagicMock()
    tariff_switch._handle_update()
    assert tariff_switch.is_on is True
    await tariff_switch.async_turn_on()
    await tariff_switch.async_turn_off()
    await tariff_switch.async_will_remove_from_hass()

    vslot_switch = VirtualSlotSchedulerSwitch("INV1", scheduler, device)
    await vslot_switch.async_added_to_hass()
    vslot_switch.async_write_ha_state = MagicMock()
    vslot_switch._handle_update()
    assert vslot_switch.is_on is True
    await vslot_switch.async_turn_on()
    await vslot_switch.async_turn_off()
    await vslot_switch.async_will_remove_from_hass()

    text = SunsynkTextEntity(
        coordinator,
        "INV1",
        __import__("custom_components.sunsynk.text", fromlist=["WRITABLE_TEXTS"]).WRITABLE_TEXTS[0],
        device,
    )
    assert text.native_value == "06:30"
    await text.async_set_value("bad")
    await text.async_set_value("07:15")
    coordinator.data["INV1"]["settings"].pop("sellTime1")
    assert text.native_value is None

    plant = PlantEnergyPriceNumberEntity(coordinator, "INV1", device)
    await plant.async_set_native_value(0.3)
    coordinator.data["INV1"]["plant"]["charges"][0]["price"] = "bad"
    assert plant.native_value is None


def test_sensor_helpers_and_listener_entities(platform_context):
    _hass, _entry, coordinator, forecast, tariff, scheduler = platform_context
    device = MagicMock()
    assert _resolve_value({"items": [{"v": 1}]}, "items.0.v") == 1
    assert _resolve_value({"items": []}, "items.2.v") is None
    assert _resolve_value({"items": []}, "items.bad.v") is None
    assert _resolve_value({"x": 1}, "x.y") is None
    assert _resolve_value({"x": None}, "x.y") is None

    desc = SunsynkSensorEntityDescription(
        key="test", endpoint="pv", data_key="missing", fallback_data_key="pac"
    )
    sensor = SunsynkSensor(coordinator, "INV1", desc, device)
    assert sensor.native_value == 123.0
    desc_text = SunsynkSensorEntityDescription(
        key="text", endpoint="inverter", data_key="alias"
    )
    assert SunsynkSensor(coordinator, "INV1", desc_text, device).native_value == "One"
    desc_fn = SunsynkSensorEntityDescription(
        key="fn", endpoint="pv", value_fn=lambda data: data["pac"]
    )
    assert SunsynkSensor(coordinator, "INV1", desc_fn, device).native_value == "123"
    assert SunsynkSensor(coordinator, "INV2", desc, device).native_value is None
    missing = SunsynkSensorEntityDescription(
        key="missing", endpoint="pv", data_key="missing"
    )
    assert SunsynkSensor(coordinator, "INV1", missing, device).native_value is None
    numeric_bad = SunsynkSensorEntityDescription(
        key="bad", endpoint="inverter", data_key="alias", state_class="measurement"
    )
    assert SunsynkSensor(coordinator, "INV1", numeric_bad, device).native_value is None

    assert _active_plant_charge([
        {"type": 1, "price": 0.1},
        {"type": 1, "price": 0.2},
    ]) == {"type": 1, "price": 0.1}
    with patch(
        "custom_components.sunsynk.sensor.dt_util.now",
        return_value=datetime(2026, 1, 1, tzinfo=timezone.utc),
    ):
        assert _active_plant_charge([
            {"type": 1, "price": 0.1, "startRange": "00:00", "endRange": "00:01"},
            {"type": 1, "price": 0.2, "startRange": "00:01", "endRange": "00:02"},
        ]) == {"type": 1, "price": 0.1, "startRange": "00:00", "endRange": "00:01"}
        assert _active_plant_charge([
            {"type": 1, "price": 0.1, "startRange": "01:00", "endRange": "02:00"},
            {"type": 1, "price": 0.2, "startRange": "02:00", "endRange": "03:00"},
        ]) == {"type": 1, "price": 0.1, "startRange": "01:00", "endRange": "02:00"}
    plant_sensor = PlantEnergyPriceSensor(coordinator, "INV1", device)
    coordinator.data["INV1"]["plant"]["charges"][0]["price"] = "bad"
    assert plant_sensor.native_value is None

    state = TariffStateSensor("entry", tariff, device)
    assert state.native_value == "idle"
    assert state.extra_state_attributes["active_hours"] == "22:00–06:00"

    quality = TariffPriceQualitySensor("entry", tariff, device)
    quality.hass = SimpleNamespace(
        states=SimpleNamespace(
            get=lambda entity: SimpleNamespace(
                state="0.2", last_updated=datetime(2026, 1, 1, tzinfo=timezone.utc)
            )
        )
    )
    assert quality.native_value == "stale"
    attrs = quality.extra_state_attributes
    assert attrs["current_state"] == "0.2"
    assert attrs["export_current_state"] == "0.2"

    vslot = VirtualSlotStateSensor("INV1", scheduler, device)
    assert vslot.native_value == "virtual_slot:1"
    assert vslot.extra_state_attributes["current_physical_slot"] == 1
    scheduler.is_enabled = False
    assert vslot.native_value == "disabled"

    forecast_sensor = SolarForecastSensor(
        forecast,
        "entry",
        __import__("custom_components.sunsynk.sensor", fromlist=["FORECAST_SENSOR_DESCRIPTIONS"]).FORECAST_SENSOR_DESCRIPTIONS[0],
        device,
    )
    assert forecast_sensor.native_value == 5.0
    forecast.data = None
    assert forecast_sensor.native_value is None


@pytest.mark.asyncio
async def test_sensor_listener_lifecycles(platform_context):
    _hass, _entry, _coordinator, _forecast, tariff, scheduler = platform_context
    device = MagicMock()
    for entity in (
        TariffStateSensor("entry", tariff, device),
        TariffPriceQualitySensor("entry", tariff, device),
        VirtualSlotStateSensor("INV1", scheduler, device),
    ):
        entity.async_write_ha_state = MagicMock()
        await entity.async_added_to_hass()
        entity._handle_update()
        entity.async_write_ha_state.assert_called_once()
        unsubscribe = entity._unsub
        await entity.async_will_remove_from_hass()
        assert unsubscribe.called
        assert entity._unsub is None

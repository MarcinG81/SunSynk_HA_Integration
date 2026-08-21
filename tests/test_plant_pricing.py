"""Tests for the plant-level pricing sensors/number entity and internal power."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.sunsynk.number import PlantEnergyPriceNumberEntity
from custom_components.sunsynk.sensor import (
    InverterInternalPowerSensor,
    PlantEnergyPriceSensor,
    _active_plant_charge,
)

_DEVICE_INFO = MagicMock()


# ── _active_plant_charge ────────────────────────────────────────────────────


def test_active_plant_charge_empty():
    assert _active_plant_charge([]) is None


def test_active_plant_charge_constant_price_single_entry():
    charges = [{"price": 0.25, "type": 1, "startRange": "", "endRange": ""}]
    assert _active_plant_charge(charges) == charges[0]


def test_active_plant_charge_time_of_use_matches_window():
    charges = [
        {"price": 0.10, "type": 2, "startRange": "00:00", "endRange": "07:00"},
        {"price": 0.30, "type": 2, "startRange": "07:00", "endRange": "16:00"},
        {"price": 0.50, "type": 2, "startRange": "16:00", "endRange": "19:00"},
        {"price": 0.20, "type": 2, "startRange": "19:00", "endRange": "24:00"},
    ]
    with patch(
        "custom_components.sunsynk.sensor.dt_util.now"
    ) as mock_now:
        mock_now.return_value.strftime.return_value = "17:30"
        result = _active_plant_charge(charges)
    assert result["price"] == 0.50


def test_active_plant_charge_ignores_live_price_entries():
    charges = [{"price": 0, "type": 3, "startRange": "", "endRange": ""}]
    # Live Price entries are filtered out — nothing else to fall back to.
    with patch("custom_components.sunsynk.sensor.dt_util.now") as mock_now:
        mock_now.return_value.strftime.return_value = "12:00"
        assert _active_plant_charge(charges) is None


# ── InverterInternalPowerSensor ─────────────────────────────────────────────


def test_internal_power_matches_real_world_sample(mock_hass):
    coordinator = MagicMock()
    coordinator.data = {
        "TEST123": {
            "pv": {"pac": 3},
            "grid": {"pac": -2760},
            "battery": {"power": 3487},
            "load": {"totalPower": 407},
        }
    }
    sensor = InverterInternalPowerSensor(coordinator, "TEST123", _DEVICE_INFO)
    assert sensor.native_value == 323.0


def test_internal_power_none_when_data_missing():
    coordinator = MagicMock()
    coordinator.data = {"TEST123": {}}
    sensor = InverterInternalPowerSensor(coordinator, "TEST123", _DEVICE_INFO)
    assert sensor.native_value is None


# ── PlantEnergyPriceSensor ───────────────────────────────────────────────────


def test_plant_energy_price_sensor_reads_active_charge():
    coordinator = MagicMock()
    coordinator.data = {
        "TEST123": {
            "plant": {
                "id": 555,
                "currency": {"code": "GBP"},
                "charges": [{"price": 0.28, "type": 1, "startRange": "", "endRange": ""}],
            }
        }
    }
    sensor = PlantEnergyPriceSensor(coordinator, "TEST123", _DEVICE_INFO)
    assert sensor.native_value == 0.28
    assert sensor.native_unit_of_measurement == "GBP/kWh"
    assert sensor.extra_state_attributes["plant_id"] == 555
    assert sensor.extra_state_attributes["type"] == "constant"


def test_plant_energy_price_sensor_unavailable_without_plant():
    coordinator = MagicMock()
    coordinator.data = {"TEST123": {"plant": {}}}
    sensor = PlantEnergyPriceSensor(coordinator, "TEST123", _DEVICE_INFO)
    assert sensor.native_value is None
    assert sensor.native_unit_of_measurement is None


# ── PlantEnergyPriceNumberEntity ────────────────────────────────────────────


def test_plant_price_number_reads_when_constant_price():
    coordinator = MagicMock()
    coordinator.data = {
        "TEST123": {"plant": {"charges": [{"price": 0.28, "type": 1}]}}
    }
    entity = PlantEnergyPriceNumberEntity(coordinator, "TEST123", _DEVICE_INFO)
    assert entity.native_value == 0.28


def test_plant_price_number_unknown_when_multiple_slots():
    coordinator = MagicMock()
    coordinator.data = {
        "TEST123": {
            "plant": {
                "charges": [
                    {"price": 0.10, "type": 2},
                    {"price": 0.30, "type": 2},
                ]
            }
        }
    }
    entity = PlantEnergyPriceNumberEntity(coordinator, "TEST123", _DEVICE_INFO)
    assert entity.native_value is None


@pytest.mark.asyncio
async def test_plant_price_number_set_calls_coordinator():
    coordinator = MagicMock()
    coordinator.data = {"TEST123": {"plant": {}}}
    coordinator.async_write_plant_price = AsyncMock()
    entity = PlantEnergyPriceNumberEntity(coordinator, "TEST123", _DEVICE_INFO)

    await entity.async_set_native_value(0.32)

    coordinator.async_write_plant_price.assert_awaited_once_with("TEST123", 0.32)

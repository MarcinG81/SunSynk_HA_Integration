"""Battery figures worked out from what the inverter reports."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from custom_components.sunsynk.battery_helpers import (
    energy_kwh,
    hours_to_full,
    hours_to_reserve,
    max_power_w,
)
from custom_components.sunsynk.sensor import (
    BATTERY_HELPERS,
    BatteryHelperSensor,
    async_setup_entry,
)


def _coordinator(battery=None, settings=None, serials=("INV1",), target=None):
    coordinator = SimpleNamespace(
        serials=list(serials),
        data={
            "INV1": {
                "battery": {
                    "capacity": 200,
                    "voltage": 52,
                    "soc": 50,
                    "power": 0,
                    **(battery or {}),
                },
                "settings": {
                    "batteryLowCap": 20,
                    "batteryMaxCurrentCharge": 100,
                    "batteryMaxCurrentDischarge": 120,
                    **(settings or {}),
                },
            }
        },
    )
    coordinator.resolve_write_target = lambda serial: target or serial
    return coordinator


def test_energy_in_the_battery():
    # 200 Ah × 52 V = 10.4 kWh, half full.
    assert energy_kwh(_coordinator(), "INV1") == 5.2


@pytest.mark.parametrize(
    "battery",
    [{"capacity": None}, {"voltage": 0}, {"soc": 120}, {"capacity": "x"}],
)
def test_no_energy_without_trustworthy_readings(battery):
    assert energy_kwh(_coordinator(battery), "INV1") is None


def test_max_power_from_the_lower_of_inverter_and_bms_limits():
    coordinator = _coordinator({"chargeCurrentLimit": 80})

    assert max_power_w(coordinator, "INV1", "charge") == 80 * 52
    assert max_power_w(coordinator, "INV1", "discharge") == 120 * 52


def test_max_power_in_a_parallel_group_is_the_inverters_share():
    """Two inverters on one bank whose BMS allows 160 A: 80 A each."""
    coordinator = _coordinator(
        {"chargeCurrentLimit": 160}, serials=("INV1", "SLAVE"), target="INV1"
    )

    assert max_power_w(coordinator, "INV1", "charge") == 80 * 52


def test_no_max_power_without_a_voltage_or_any_limit():
    assert max_power_w(_coordinator({"voltage": None}), "INV1", "charge") is None
    no_limits = _coordinator(
        settings={"batteryMaxCurrentCharge": None, "batteryMaxCurrentDischarge": None}
    )
    assert max_power_w(no_limits, "INV1", "charge") is None


def test_time_to_full_while_charging():
    # 5.2 kWh to go at 2,600 W: 2 hours.
    assert hours_to_full(_coordinator({"power": -2600}), "INV1") == 2.0
    assert hours_to_full(_coordinator({"power": 500}), "INV1") is None
    assert hours_to_full(_coordinator({"capacity": None, "power": -100}), "INV1") is None


def test_time_to_reserve_while_discharging():
    # 50 % down to 20 %: 3.12 kWh at 1,040 W = 3 hours.
    assert hours_to_reserve(_coordinator({"power": 1040}), "INV1") == 3.0
    assert hours_to_reserve(_coordinator({"power": -100}), "INV1") is None
    # Already below the reserve: none left.
    assert hours_to_reserve(_coordinator({"power": 500, "soc": 10}), "INV1") == 0
    no_reserve = _coordinator({"power": 1040}, settings={"batteryLowCap": None})
    assert hours_to_reserve(no_reserve, "INV1") is None


def test_the_sensor_shows_the_figure():
    coordinator = _coordinator()
    sensor = BatteryHelperSensor(coordinator, "INV1", BATTERY_HELPERS[0], MagicMock())

    assert sensor.native_value == 5.2
    assert sensor.unique_id == "INV1_battery_energy"


@pytest.mark.asyncio
@pytest.mark.parametrize("backend", [None, "deyecloud"])
async def test_every_inverter_gets_the_figures(backend):
    coordinator = MagicMock()
    coordinator.serials = ["INV1"]
    coordinator.write_target_serials = ["INV1"]
    coordinator.resolve_write_target.side_effect = lambda serial: serial
    # No current limits reported, as on a single-phase Deye Cloud inverter.
    coordinator.data = {
        "INV1": {
            "inverter": {"sn": "INV1"},
            "battery": {"capacity": 200, "voltage": 52, "soc": 50, "power": 0},
            "settings": {"batteryLowCap": 20},
        }
    }
    entry = MagicMock(entry_id="entry", data={"backend": backend} if backend else {})
    hass = SimpleNamespace(data={"sunsynk": {"entry": coordinator}})
    added: list = []

    await async_setup_entry(hass, entry, added.extend)

    keys = {
        e.entity_description.key for e in added if isinstance(e, BatteryHelperSensor)
    }
    always = {"battery_energy", "battery_time_to_full", "battery_time_to_reserve"}
    power = {"battery_max_charge_power", "battery_max_discharge_power"}
    assert keys == (always if backend else always | power)

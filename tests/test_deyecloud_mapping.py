"""Deye Cloud data as the Sunsynk-shaped sections the integration reads.

Uses real probe output from a single-phase 8 kW and a three-phase 15 kW
hybrid (#37), serials removed.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from custom_components.sunsynk.deyecloud.mapping import PROVIDED, sections, settings

_DATA = json.loads(
    (Path(__file__).parent / "fixtures" / "deyecloud_37.json").read_text("utf-8")
)
SINGLE = _DATA["single_phase_8kw"]["latest"]
THREE = _DATA["three_phase_15kw"]["latest"]
THREE_CONFIG = _DATA["three_phase_15kw"]["config"]


def test_single_phase_live_data():
    data = sections("SN1", SINGLE, {})

    assert data["inverter"]["sn"] == "SN1"
    assert data["inverter"]["ratePower"] == 8000
    assert data["inverter"]["version"] == {
        "masterVer": "6025-1724",
        "hmiVer": "0000-C36E",
        "bmsVer": "V2.0.0.8",
    }
    assert data["battery"] == {
        "soc": 100,
        "power": 8,
        "voltage": 56.18,
        "current": 0.16,
        "temp": 17,
        "etodayChg": 0.7,
        "etodayDischg": 0.7,
        "etotalChg": 1382.7,
        "etotalDischg": 1226.2,
        "capacity": 200,
    }
    # Importing is positive, as on Sunsynk.
    assert data["grid"]["pac"] == 666
    assert data["grid"]["etodayFrom"] == 10.6
    assert data["grid"]["etodayTo"] == 0
    assert data["grid"]["etotalFrom"] == 2268.6
    assert data["grid"]["vip"] == [{"volt": 228.4, "current": 3.02, "power": 666}]
    assert data["load"]["totalPower"] == 589
    assert data["load"]["totalUsed"] == 2874.8
    assert data["output"]["pac"] == -77
    assert data["temp"] == {"dc_temp": 47.9, "igbt_temp": 40.3}
    assert data["settings"] == {}


def test_single_phase_solar_power_is_the_sum_of_the_strings():
    """Single-phase models report no TotalSolarPower."""
    points = {**SINGLE, "DCPowerPV1": "1200", "DCPowerPV2": "800"}

    pv = sections("SN1", points, {})["pv"]

    assert pv["pac"] == 2000
    assert [string["ppv"] for string in pv["pvIV"]] == [1200, 800, 0]
    assert pv["pvIV"][0] == {"vpv": 47.3, "ipv": 0, "ppv": 1200}
    assert pv["etoday"] == 1.4


def test_three_phase_live_data():
    data = sections("SN3", THREE, THREE_CONFIG)

    assert data["inverter"]["ratePower"] == 15000
    assert data["pv"]["pac"] == 0
    assert len(data["pv"]["pvIV"]) == 4
    assert data["battery"]["soc"] == 31
    assert data["battery"]["power"] == 182
    # No plain BatteryCurrent on three-phase: the total is used.
    assert data["battery"]["current"] == 3.52
    assert data["grid"]["etotalFrom"] == 3613
    assert data["grid"]["etotalTo"] == 9466.4
    assert data["grid"]["vip"] == [
        {"volt": 233.4, "current": 0.55, "power": 4},
        {"volt": 231.5, "current": 0.39, "power": 2},
        {"volt": 229.9, "current": 0.47, "power": 0},
    ]
    assert [phase["power"] for phase in data["load"]["vip"]] == [58, 38, 21]
    assert data["load"]["totalUsed"] == 10263.2
    assert data["output"]["pac"] == 111
    assert [phase["volt"] for phase in data["output"]["vip"]] == [234.2, 231.4, 228.9]


def test_three_phase_settings():
    result = settings(THREE_CONFIG)

    assert result["sysWorkMode"] == 1  # ZERO_EXPORT_TO_LOAD
    assert result["energyMode"] == 0  # BATTERY_FIRST
    assert result["solarMaxSellPower"] == 30000
    assert result["zeroExportPower"] == 20
    assert result["batteryMaxCurrentCharge"] == 80
    assert result["batteryMaxCurrentDischarge"] == 170
    assert result["batteryLowCap"] == 25
    assert result["batteryShutdownCap"] == 20
    assert result["batteryCap"] == 300
    assert result["peakAndVallery"] == "1"
    assert [result[f"sellTime{n}"] for n in range(1, 7)] == [
        "01:00", "06:50", "07:05", "10:00", "18:00", "22:00",
    ]
    assert result["time2on"] == "true"
    assert result["time1on"] == "false"
    assert result["cap2"] == 95
    assert result["sellTime3Pac"] == 3000
    assert result["genTime1on"] == "false"


@pytest.mark.parametrize(
    ("tou", "expected"),
    [
        ({"touAction": "off", "timeUseSettingItems": []}, {"peakAndVallery": "0"}),
        # A time Deye didn't send as HHMM, and a flag that isn't a bool.
        (
            {"timeUseSettingItems": [{"time": "6:5", "enableGridCharge": "yes"}]},
            {},
        ),
    ],
)
def test_odd_time_of_use_values_are_left_out(tou, expected):
    assert settings({"tou": tou}) == expected


def test_unknown_modes_and_bad_numbers_are_left_out():
    result = settings(
        {
            "system": {"systemWorkMode": "SOMETHING_NEW", "maxSellPower": "nan"},
            "battery": {"maxChargeCurrent": "x"},
        }
    )

    assert result == {}


def test_nothing_reported_gives_empty_sections():
    data = sections("SN1", {}, {})

    assert data["pv"] == {"pvIV": []}
    assert data["battery"] == {}
    assert data["grid"] == {"vip": []}
    assert data["inverter"] == {"sn": "SN1", "brand": "Deye"}


def _leaf_keys(section: dict, prefix: str = "") -> set[str]:
    keys = set()
    for key, value in section.items():
        if isinstance(value, dict):
            keys |= _leaf_keys(value, f"{prefix}{key}.")
        elif not isinstance(value, list):
            keys.add(f"{prefix}{key}")
    return keys


@pytest.mark.parametrize("points", [SINGLE, THREE], ids=["single", "three"])
def test_provided_lists_exactly_what_the_mapping_fills(points):
    """Sensors are created from PROVIDED: anything missing from it would
    never get a sensor, anything extra would always be "Unknown"."""
    data = sections("SN", points, {})

    for name, provided in PROVIDED.items():
        assert _leaf_keys(data[name]) <= provided, name
    filled = {name: _leaf_keys(data[name]) for name in PROVIDED}
    # Every listed field is filled by at least one of the two models.
    other = sections("SN", THREE if points is SINGLE else SINGLE, {})
    for name, provided in PROVIDED.items():
        assert provided <= filled[name] | _leaf_keys(other[name]), name

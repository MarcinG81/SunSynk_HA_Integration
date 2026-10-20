"""Deye Cloud data in the shape the rest of the integration reads.

Sensors, the dashboard, Tariff Manager and the Virtual Slot Scheduler all
read a Sunsynk-shaped dict per inverter: sections "inverter", "pv", "grid",
"battery", "load", "output", "temp" and "settings", with the Sunsynk API's
field names. Deye Cloud reports flat measure points instead (`SOC`,
`TotalGridPower`, ...) and its settings in three config groups, so they are
translated here.

Key names and signs come from real data (#37): a single-phase 8 kW and a
three-phase 15 kW hybrid. They differ: single-phase reports `…L1L2` keys and
no `TotalSolarPower`; three-phase reports per-phase `…L1/L2/L3` keys. Signs
match Sunsynk's: battery power is positive while discharging, grid power
positive while importing.
"""

from __future__ import annotations

import math
from typing import Any

from .client import ENERGY_PATTERNS, TOU_SLOTS, WORK_MODES

# The fields `sections` can fill, per section. Sensors for anything else
# (BMS details, status codes, plant info, ...) would only ever show
# "Unknown" on a Deye Cloud inverter, so they aren't created (#37).
PROVIDED: dict[str, frozenset[str]] = {
    "inverter": frozenset(
        {
            "sn",
            "brand",
            "ratePower",
            "pac",
            "etoday",
            "etotal",
            "version.masterVer",
            "version.hmiVer",
            "version.bmsVer",
        }
    ),
    "pv": frozenset({"pac", "etoday", "etotal"}),
    "grid": frozenset(
        {"pac", "fac", "etodayFrom", "etodayTo", "etotalFrom", "etotalTo"}
    ),
    "battery": frozenset(
        {
            "soc",
            "power",
            "voltage",
            "current",
            "temp",
            "etodayChg",
            "etodayDischg",
            "etotalChg",
            "etotalDischg",
            "capacity",
        }
    ),
    "load": frozenset(
        {"totalPower", "dailyUsed", "totalUsed", "loadFac", "upsPowerTotal"}
    ),
    "output": frozenset({"pac", "fac"}),
    "temp": frozenset({"dc_temp", "igbt_temp"}),
}

_WORK_MODE_VALUES = {name: value for value, name in WORK_MODES.items()}
_ENERGY_PATTERN_VALUES = {name: value for value, name in ENERGY_PATTERNS.items()}


def _number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _first(points: dict[str, Any], *keys: str) -> float | None:
    """The first of `keys` reported as a number (models name things apart)."""
    for key in keys:
        number = _number(points.get(key))
        if number is not None:
            return number
    return None


def _drop_missing(section: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in section.items() if value is not None}


def _three_phase(points: dict[str, Any]) -> bool:
    return "GridVoltageL1" in points or "LoadVoltageL1" in points


def _pv(points: dict[str, Any]) -> dict[str, Any]:
    strings = []
    n = 1
    while f"DCVoltagePV{n}" in points or f"DCPowerPV{n}" in points:
        strings.append(
            _drop_missing(
                {
                    "vpv": _number(points.get(f"DCVoltagePV{n}")),
                    "ipv": _number(points.get(f"DCCurrentPV{n}")),
                    "ppv": _number(points.get(f"DCPowerPV{n}")),
                }
            )
        )
        n += 1
    power = _first(points, "TotalSolarPower")
    if power is None and strings:
        # Single-phase models don't report a total.
        power = sum(string.get("ppv") or 0 for string in strings)
    return _drop_missing(
        {
            "pac": power,
            "etoday": _first(
                points, "PVDailyPowerGenerationActive", "DailyActiveProduction"
            ),
            "etotal": _first(
                points, "PVCumulativePowerGenerationActive", "TotalActiveProduction"
            ),
            "pvIV": strings,
        }
    )


def _phases(points: dict[str, Any], fields: dict[str, tuple[str, ...]]) -> list:
    """Per-phase entries, e.g. {"volt": ..., "current": ..., "power": ...}."""
    phases = []
    for keys in zip(*fields.values(), strict=True):
        phase = _drop_missing(
            {name: _first(points, key) for name, key in zip(fields, keys, strict=True)}
        )
        if phase:
            phases.append(phase)
    return phases


def _grid(points: dict[str, Any], three_phase: bool) -> dict[str, Any]:
    if three_phase:
        vip = _phases(
            points,
            {
                "volt": ("GridVoltageL1", "GridVoltageL2", "GridVoltageL3"),
                "current": ("GridCurrentL1", "GridCurrentL2", "GridCurrentL3"),
                "power": ("GridPowerL1", "GridPowerL2", "GridPowerL3"),
            },
        )
    else:
        vip = _phases(
            points,
            {
                "volt": ("GridVoltageL1L2",),
                "current": ("GridCurrentL1L2",),
                "power": ("TotalGridPower",),
            },
        )
    return _drop_missing(
        {
            "pac": _first(points, "TotalGridPower"),
            "fac": _first(points, "GridFrequency"),
            "etodayFrom": _first(points, "DailyEnergyPurchased"),
            "etodayTo": _first(points, "DailyGridFeedIn"),
            "etotalFrom": _first(points, "CumulativeEnergyPurchased", "TotalEnergyBuy"),
            "etotalTo": _first(points, "CumulativeGridFeedIn", "TotalEnergySell"),
            "vip": vip,
        }
    )


def _load(points: dict[str, Any], three_phase: bool) -> dict[str, Any]:
    if three_phase:
        vip = _phases(
            points,
            {
                "volt": ("LoadVoltageL1", "LoadVoltageL2", "LoadVoltageL3"),
                "power": ("LoadPowerL1", "LoadPowerL2", "LoadPowerL3"),
            },
        )
    else:
        vip = _phases(
            points,
            {"volt": ("LoadVoltageL1L2",), "power": ("TotalConsumptionPower",)},
        )
    return _drop_missing(
        {
            "totalPower": _first(points, "TotalConsumptionPower"),
            "dailyUsed": _first(points, "DailyConsumption"),
            "totalUsed": _first(points, "CumulativeConsumption", "TotalConsumption"),
            "loadFac": _first(points, "LoadFrequency"),
            "upsPowerTotal": _first(points, "UPSLoadPower"),
            "vip": vip,
        }
    )


def _output(points: dict[str, Any], three_phase: bool) -> dict[str, Any]:
    if three_phase:
        vip = _phases(
            points,
            {
                "volt": ("ACVoltageRUA", "ACVoltageSVB", "ACVoltageTWC"),
                "current": ("ACCurrentRUA", "ACCurrentSVB", "ACCurrentTWC"),
                "power": (
                    "InverterOutputPowerL1",
                    "InverterOutputPowerL2",
                    "InverterOutputPowerL3",
                ),
            },
        )
    else:
        vip = _phases(
            points,
            {
                "volt": ("ACVoltageRUA",),
                "current": ("ACCurrentRUA",),
                "power": ("InverterOutputPowerL1L2",),
            },
        )
    return _drop_missing(
        {
            "pac": _first(
                points, "TotalInverterOutputPower", "InverterOutputPowerL1L2"
            ),
            "fac": _first(points, "ACOutputFrequencyR"),
            "vip": vip,
        }
    )


def _battery(points: dict[str, Any]) -> dict[str, Any]:
    return _drop_missing(
        {
            "soc": _first(points, "SOC"),
            "power": _first(points, "BatteryPower"),
            "voltage": _first(points, "BatteryVoltage"),
            "current": _first(points, "BatteryCurrent", "BatteryTotalCurrent"),
            "temp": _first(points, "Temperature- Battery"),
            "etodayChg": _first(points, "DailyChargingEnergy"),
            "etodayDischg": _first(points, "DailyDischargingEnergy"),
            "etotalChg": _first(points, "TotalChargeEnergy"),
            "etotalDischg": _first(points, "TotalDischargeEnergy"),
            "capacity": _first(points, "BatteryRatedCapacity"),
        }
    )


def _inverter(serial: str, points: dict[str, Any], pv: dict[str, Any]) -> dict:
    versions = _drop_missing(
        {
            "masterVer": points.get("MAIN"),
            "hmiVer": points.get("HMI"),
            "bmsVer": points.get("LithiumBatteryVersionNumber"),
        }
    )
    return _drop_missing(
        {
            "sn": serial,
            "brand": "Deye",
            "ratePower": _first(points, "RatedPower"),
            "pac": _first(
                points, "TotalInverterOutputPower", "InverterOutputPowerL1L2"
            ),
            "etoday": pv.get("etoday"),
            "etotal": _first(points, "TotalActiveProduction"),
            "version": versions or None,
        }
    )


def _hhmm(value: Any) -> str | None:
    """Deye's "0650" as Sunsynk's "06:50"."""
    text = str(value or "").strip()
    if len(text) == 4 and text.isdigit():
        return f"{text[:2]}:{text[2:]}"
    return None


def _flag(value: Any) -> str | None:
    """A Deye boolean as the "true"/"false" Sunsynk reports for its flags."""
    if isinstance(value, bool):
        return "true" if value else "false"
    return None


def settings(config: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Settings under the Sunsynk API's names, from the config groups read.

    A group the inverter doesn't offer (or that wasn't read) adds nothing.
    """
    system = config.get("system") or {}
    battery = config.get("battery") or {}
    tou = config.get("tou") or {}
    result: dict[str, Any] = {
        "sysWorkMode": _WORK_MODE_VALUES.get(system.get("systemWorkMode")),
        "energyMode": _ENERGY_PATTERN_VALUES.get(system.get("energyPattern")),
        "solarMaxSellPower": _number(system.get("maxSellPower")),
        "zeroExportPower": _number(system.get("zeroExportPower")),
        "batteryMaxCurrentCharge": _number(battery.get("maxChargeCurrent")),
        "batteryMaxCurrentDischarge": _number(battery.get("maxDischargeCurrent")),
        "batteryLowCap": _number(battery.get("battLowCapacity")),
        "batteryShutdownCap": _number(battery.get("battShutDownCapacity")),
        "batteryCap": _number(battery.get("battCapacity")),
    }
    if "touAction" in tou:
        result["peakAndVallery"] = "1" if tou["touAction"] == "on" else "0"
    for n, slot in enumerate((tou.get("timeUseSettingItems") or [])[:TOU_SLOTS], 1):
        result.update(
            {
                f"sellTime{n}": _hhmm(slot.get("time")),
                f"sellTime{n}Pac": _number(slot.get("power")),
                f"cap{n}": _number(slot.get("soc")),
                f"time{n}on": _flag(slot.get("enableGridCharge")),
                f"genTime{n}on": _flag(slot.get("enableGeneration")),
            }
        )
    return _drop_missing(result)


def sections(
    serial: str, points: dict[str, Any], config: dict[str, dict[str, Any]]
) -> dict[str, Any]:
    """One inverter's data, shaped like the Sunsynk client's fetch."""
    three_phase = _three_phase(points)
    pv = _pv(points)
    return {
        "inverter": _inverter(serial, points, pv),
        "pv": pv,
        "grid": _grid(points, three_phase),
        "battery": _battery(points),
        "load": _load(points, three_phase),
        "output": _output(points, three_phase),
        "temp": _drop_missing(
            {
                "dc_temp": _first(points, "DC Temperature"),
                "igbt_temp": _first(points, "AC Temperature"),
            }
        ),
        "settings": settings(config),
    }

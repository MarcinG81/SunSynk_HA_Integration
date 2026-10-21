"""Battery figures worked out from what the inverter reports.

Energy in the battery, the most it can take or give right now, and how long
until it is full or down to its reserve: handy on a dashboard, and for tools
such as Predbat that plan with power rather than current.

All from the battery's capacity (Ah), voltage, SOC and power, its charge and
discharge current limits (the inverter's own and the BMS's, shared in a
parallel group), and the inverter's Battery Low Capacity. A figure that can't
be worked out (missing data, or not charging / discharging) is None.
"""

from __future__ import annotations

import math
from typing import Any

from .current_limits import current_limit, group_sizes


def _number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _battery(coordinator: Any, serial: str) -> dict[str, Any]:
    return (coordinator.data or {}).get(serial, {}).get("battery", {}) or {}


def _capacity_wh(battery: dict[str, Any]) -> float | None:
    """The battery's capacity in Wh at its voltage now."""
    ah = _number(battery.get("capacity"))
    volts = _number(battery.get("voltage"))
    if not ah or not volts or ah <= 0 or volts <= 0:
        return None
    return ah * volts


def _soc(battery: dict[str, Any]) -> float | None:
    soc = _number(battery.get("soc"))
    return soc if soc is not None and 0 <= soc <= 100 else None


def energy_kwh(coordinator: Any, serial: str) -> float | None:
    """Energy in the battery now, in kWh."""
    battery = _battery(coordinator, serial)
    capacity, soc = _capacity_wh(battery), _soc(battery)
    if capacity is None or soc is None:
        return None
    return round(capacity * soc / 100 / 1000, 2)


def max_power_w(coordinator: Any, serial: str, direction: str) -> int | None:
    """The most the battery can take ("charge") or give ("discharge") now."""
    volts = _number(_battery(coordinator, serial).get("voltage"))
    if not volts or volts <= 0:
        return None
    limit = current_limit(
        coordinator.data, [serial], direction, group_sizes(coordinator), live=True
    ).maximum
    return None if limit is None else round(limit * volts)


def hours_to_full(coordinator: Any, serial: str) -> float | None:
    """At the charging power now; None unless charging."""
    battery = _battery(coordinator, serial)
    capacity, soc = _capacity_wh(battery), _soc(battery)
    power = _number(battery.get("power"))
    if capacity is None or soc is None or power is None or power >= 0:
        return None
    return round(capacity * (100 - soc) / 100 / -power, 2)


def hours_to_reserve(coordinator: Any, serial: str) -> float | None:
    """Down to Battery Low Capacity at the discharging power now; None
    unless discharging."""
    battery = _battery(coordinator, serial)
    capacity, soc = _capacity_wh(battery), _soc(battery)
    power = _number(battery.get("power"))
    settings = (coordinator.data or {}).get(serial, {}).get("settings", {}) or {}
    reserve = _number(settings.get("batteryLowCap"))
    if (
        capacity is None
        or soc is None
        or power is None
        or power <= 0
        or reserve is None
    ):
        return None
    return round(max(0.0, capacity * (soc - reserve) / 100 / power), 2)

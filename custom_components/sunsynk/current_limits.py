"""Highest battery charge/discharge current an inverter will actually use.

A charge or discharge current above what the inverter or the battery allows
is accepted by the Sunsynk API but has no effect: the inverter is capped by
its own "Max A" battery setting, and with a lithium BMS also by the limit
the BMS reports. Configure checks the Tariff Manager currents against the
lower of the two, so nobody sets 200 A expecting more than the hardware
gives.

Only plausible readings count. A parallel master was seen reporting a
discharge current of 1040 A (#21), so a value outside 1..MAX_CURRENT_A is
treated as unknown rather than as a limit.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from .write_validation import MAX_CURRENT_A

# (inverter setting, BMS reading) for each direction.
_SOURCES = {
    "charge": ("batteryMaxCurrentCharge", "maxChargeCurrentLimit"),
    "discharge": ("batteryMaxCurrentDischarge", "maxDischargeCurrentLimit"),
}


@dataclass(frozen=True)
class CurrentLimit:
    """The lowest known limit for one direction, and where it came from."""

    inverter: int | None
    battery: int | None

    @property
    def maximum(self) -> int | None:
        known = [v for v in (self.inverter, self.battery) if v is not None]
        return min(known) if known else None


def _plausible_amps(value: Any) -> int | None:
    try:
        amps = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(amps) or not 1 <= amps <= MAX_CURRENT_A:
        return None
    return int(amps)


def _lowest(values: list[int | None]) -> int | None:
    known = [v for v in values if v is not None]
    return min(known) if known else None


def current_limit(
    data: dict[str, Any] | None, serials: list[str], direction: str
) -> CurrentLimit:
    """Lowest plausible limit across `serials` for "charge" or "discharge".

    The Tariff Manager writes the same current to every write target, so the
    most restrictive one sets what all of them will actually use.
    """
    setting_key, bms_key = _SOURCES[direction]
    inverter: list[int | None] = []
    battery: list[int | None] = []
    for serial in serials:
        serial_data = (data or {}).get(serial, {})
        inverter.append(
            _plausible_amps(serial_data.get("settings", {}).get(setting_key))
        )
        battery.append(_plausible_amps(serial_data.get("battery", {}).get(bms_key)))
    return CurrentLimit(inverter=_lowest(inverter), battery=_lowest(battery))


def describe_limit(limit: CurrentLimit) -> str:
    """Plain-English source of a limit, e.g. "inverter 120 A, battery 100 A"."""
    parts = []
    if limit.inverter is not None:
        parts.append(f"inverter {limit.inverter} A")
    if limit.battery is not None:
        parts.append(f"battery BMS {limit.battery} A")
    return ", ".join(parts)

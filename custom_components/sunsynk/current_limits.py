"""Highest battery charge/discharge current an inverter will actually use.

A charge or discharge current above what the inverter or the battery allows
is accepted by the Sunsynk API but has no effect: the inverter is capped by
its own "Max A" battery setting, and with a lithium BMS also by the limit
the BMS reports. Configure checks the Tariff Manager currents against the
lower of the two, so nobody sets 200 A expecting more than the hardware
gives.

In a parallel group every inverter applies the same per-inverter current
to one shared battery bank, so the bank sees that current times the number
of inverters. The BMS limit is for the whole bank: a parallel pair of 12 kW
inverters at 240 A each draws 480 A (#21, a 1256 Ah bank whose BMS allows
1040 A). Each inverter's share of the BMS limit is therefore the limit
divided by the number of inverters.

Only plausible readings count: an inverter's own limit outside
1..MAX_CURRENT_A, or a BMS limit outside 1.._MAX_BMS_A, is treated as unknown
rather than as a limit.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import Any

from .write_validation import MAX_CURRENT_A

_LOGGER = logging.getLogger(__name__)

# A whole battery bank can allow far more than one inverter: 1040 A was seen.
_MAX_BMS_A = 5000

# (inverter setting, BMS rated limit, BMS live limit) for each direction. The
# rated limit is fixed; the live one is what the BMS allows right now, and
# drops e.g. near full charge. Many BMSs report only the live one (rated 0).
_SOURCES = {
    "charge": (
        "batteryMaxCurrentCharge",
        "maxChargeCurrentLimit",
        "chargeCurrentLimit",
    ),
    "discharge": (
        "batteryMaxCurrentDischarge",
        "maxDischargeCurrentLimit",
        "dischargeCurrentLimit",
    ),
}


@dataclass(frozen=True)
class CurrentLimit:
    """The lowest known limit for one direction, and where it came from."""

    inverter: int | None
    battery: int | None
    # The whole bank's limit and the inverters sharing it, when shared.
    battery_total: int | None = None
    shared_by: int = 1

    @property
    def maximum(self) -> int | None:
        known = [v for v in (self.inverter, self.battery) if v is not None]
        return min(known) if known else None


def _plausible_amps(value: Any, upper: int = MAX_CURRENT_A) -> int | None:
    try:
        amps = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(amps) or not 1 <= amps <= upper:
        return None
    return int(amps)


def _lowest(values: list[int | None]) -> int | None:
    known = [v for v in values if v is not None]
    return min(known) if known else None


def current_limit(
    data: dict[str, Any] | None,
    serials: list[str],
    direction: str,
    group_sizes: dict[str, int] | None = None,
    *,
    live: bool = False,
) -> CurrentLimit:
    """Lowest plausible per-inverter limit across `serials`.

    `group_sizes` gives the number of inverters in each serial's parallel
    group (1 when independent); the BMS limit is shared between them. With
    `live`, the lower of the BMS's rated and current limits is used;
    otherwise only fixed limits count, so a check doesn't depend on the SOC
    at the moment it runs.

    The Tariff Manager writes the same current to every write target, so the
    most restrictive one sets what all of them will actually use.
    """
    setting_key, rated_key, live_key = _SOURCES[direction]
    best: CurrentLimit | None = None
    inverter: list[int | None] = []
    for serial in serials:
        serial_data = (data or {}).get(serial, {})
        inverter.append(
            _plausible_amps(serial_data.get("settings", {}).get(setting_key))
        )
        battery_data = serial_data.get("battery", {})
        total = _plausible_amps(battery_data.get(rated_key), _MAX_BMS_A)
        if live:
            # What the BMS allows right now can be lower than its rating
            # (e.g. near full charge): the lower of the two applies, not the
            # rated one just because it is reported (2.0 audit A5).
            total = _lowest(
                [total, _plausible_amps(battery_data.get(live_key), _MAX_BMS_A)]
            )
        if total is None:
            continue
        shared_by = max(1, int((group_sizes or {}).get(serial, 1)))
        share = CurrentLimit(None, total // shared_by, total, shared_by)
        if best is None or share.battery < best.battery:
            best = share
    if best is None:
        return CurrentLimit(inverter=_lowest(inverter), battery=None)
    return CurrentLimit(
        inverter=_lowest(inverter),
        battery=best.battery,
        battery_total=best.battery_total,
        shared_by=best.shared_by,
    )


def group_sizes(coordinator: Any) -> dict[str, int]:
    """Inverters in each write target's parallel group, from the coordinator."""
    serials = list(getattr(coordinator, "serials", None) or [])
    sizes: dict[str, int] = {}
    for serial in serials:
        target = coordinator.resolve_write_target(serial)
        if isinstance(target, str):
            sizes[target] = sizes.get(target, 0) + 1
    return sizes


def battery_safe_current(
    coordinator: Any, serial: str, direction: str, amps: int
) -> int:
    """`amps`, lowered if the parallel group together would exceed the BMS.

    Uses what the BMS allows right now. The inverter's own limit is left to
    the inverter: writing above it is harmless, and Configure refuses it.
    """
    if not amps:
        return amps
    limit = current_limit(
        coordinator.data,
        [serial],
        direction,
        group_sizes(coordinator),
        live=True,
    )
    if limit.battery is None or amps <= limit.battery:
        return amps
    _LOGGER.warning(
        "Lowering %s current for %s from %s A to %s A: %s",
        direction,
        serial,
        amps,
        limit.battery,
        describe_limit(
            CurrentLimit(None, limit.battery, limit.battery_total, limit.shared_by)
        ),
    )
    return limit.battery


def describe_limit(limit: CurrentLimit) -> str:
    """Plain-English source of a limit, e.g. "inverter 120 A, battery 100 A"."""
    parts = []
    if limit.inverter is not None:
        parts.append(f"inverter {limit.inverter} A")
    if limit.battery is not None:
        if limit.shared_by > 1:
            parts.append(
                f"battery BMS {limit.battery_total} A shared by "
                f"{limit.shared_by} inverters = {limit.battery} A each"
            )
        else:
            parts.append(f"battery BMS {limit.battery} A")
    return ", ".join(parts)

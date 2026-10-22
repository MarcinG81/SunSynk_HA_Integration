"""Plan when to buy and sell from the prices ahead (#40).

Price integrations publish the coming day's prices per slot (Octopus Agile:
half-hourly, from about 16:00). Given how much to buy and to sell a day, the
cheapest slots to buy in and the dearest to sell in are picked per day, so
Tariff Manager charges or discharges in exactly those.

Rather than a list of suppliers, the formats the price integrations use are
read, so any supplier they cover works:
- Octopus Energy (BottlecapDave): `rates`, items with `start`, `end`,
  `value_inc_vat` (current day and next day rates entities);
- Nord Pool and the integrations that copy it: `raw_today` / `raw_tomorrow`,
  items with `start`, `end`, `value`.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from homeassistant.util import dt as dt_util

# Attribute holding the slots, and the key of each slot's price.
_FORMATS: tuple[tuple[tuple[str, ...], str], ...] = (
    (("rates",), "value_inc_vat"),
    (("raw_today", "raw_tomorrow"), "value"),
)


@dataclass(frozen=True)
class Rate:
    start: datetime
    end: datetime
    price: float

    @property
    def hours(self) -> float:
        return (self.end - self.start).total_seconds() / 3600


def _when(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        when = value
    elif isinstance(value, str):
        when = dt_util.parse_datetime(value)
        if when is None:
            return None
    else:
        return None
    return when if when.tzinfo else when.replace(tzinfo=dt_util.DEFAULT_TIME_ZONE)


def _price(value: Any) -> float | None:
    try:
        price = float(value)
    except (TypeError, ValueError):
        return None
    return price if math.isfinite(price) else None


def parse_rates(attributes: Mapping[str, Any]) -> list[Rate]:
    """The slots in one entity's attributes, in any format above."""
    rates: list[Rate] = []
    for keys, price_key in _FORMATS:
        for key in keys:
            for item in attributes.get(key) or ():
                if not isinstance(item, Mapping):
                    continue
                start, end = _when(item.get("start")), _when(item.get("end"))
                price = _price(item.get(price_key))
                if start and end and end > start and price is not None:
                    rates.append(Rate(start, end, price))
    return rates


def merge_rates(*groups: Iterable[Rate]) -> list[Rate]:
    """All slots from several entities (e.g. today's and tomorrow's), one
    per start time, in time order."""
    by_start = {rate.start: rate for group in groups for rate in group}
    return [by_start[start] for start in sorted(by_start)]


def plan_day(
    rates: Iterable[Rate],
    after: datetime,
    kwh: float,
    power_kw: float,
    *,
    dearest: bool,
    limit: float | None = None,
) -> list[Rate]:
    """The cheapest (or `dearest`) slots ending after `after` that move
    `kwh` at `power_kw`, within the price `limit`; in time order."""
    if kwh <= 0 or power_kw <= 0:
        return []
    candidates = [
        rate
        for rate in rates
        if rate.end > after
        and (limit is None or (rate.price >= limit if dearest else rate.price <= limit))
    ]
    # Ties keep the earlier slot, so the same prices always give the same plan.
    candidates.sort(
        key=lambda rate: (-rate.price if dearest else rate.price, rate.start)
    )
    chosen: list[Rate] = []
    energy = 0.0
    for rate in candidates:
        if energy >= kwh:
            break
        chosen.append(rate)
        energy += power_kw * rate.hours
    return sorted(chosen, key=lambda rate: rate.start)


def by_day(rates: Iterable[Rate]) -> dict[date, list[Rate]]:
    """Slots grouped by the local day they start on."""
    days: dict[date, list[Rate]] = {}
    for rate in rates:
        days.setdefault(dt_util.as_local(rate.start).date(), []).append(rate)
    return days


def in_slot(plan: Iterable[Rate], now: datetime) -> Rate | None:
    return next((rate for rate in plan if rate.start <= now < rate.end), None)

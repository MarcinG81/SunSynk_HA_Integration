"""Learns a per-month solar performance ratio from actual vs. irradiance-modeled production.

The forecast coordinator estimates energy as `ghi/1000 * panel_kwp * performance_ratio`.
`performance_ratio` bundles panel losses (temperature, soiling, inverter, wiring, shading,
mismatch) that vary with the season. Instead of one fixed user-configured value, this
calibrator compares each finished day's actual `pv.etoday` reading against what the
irradiance model would have predicted with a ratio of 1, and folds the observed ratio
into an exponential moving average per calendar month.
"""
from __future__ import annotations

from datetime import date

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

_STORAGE_VERSION = 1
_EMA_ALPHA = 0.2
_MIN_RAW_KWH = 0.5  # below this, a day's irradiance total is too small for a stable ratio
_RATIO_MIN = 0.3
_RATIO_MAX = 1.1


class PerformanceRatioCalibrator:
    """Tracks and persists a learned performance ratio for each calendar month."""

    def __init__(self, hass: HomeAssistant, entry_id: str, default_ratio: float) -> None:
        self._store: Store = Store(hass, _STORAGE_VERSION, f"sunsynk_performance_{entry_id}")
        self._default_ratio = default_ratio
        self._monthly: dict[str, dict[str, float]] = {}
        self._tracked_date: date | None = None
        self._last_raw_kwh = 0.0
        self._last_actual_kwh = 0.0

    async def async_load(self) -> None:
        """Load persisted monthly ratios, if any."""
        data = await self._store.async_load()
        if data:
            self._monthly = data.get("monthly", {})

    def get_ratio(self, month: int) -> float:
        """Return the learned ratio for `month`, or the configured default if no data yet."""
        entry = self._monthly.get(str(month))
        if entry and entry.get("samples", 0) > 0:
            return entry["ratio"]
        return self._default_ratio

    async def async_update(
        self, today: date, raw_kwh_today: float, actual_kwh_today: float | None
    ) -> None:
        """Feed the latest raw (ratio=1) and actual kWh readings observed for `today`.

        Call this on every forecast refresh. When the date rolls over, the last
        readings seen for the previous day are used to calibrate that month.
        """
        if actual_kwh_today is None:
            return

        if self._tracked_date is None:
            self._tracked_date = today
        elif today != self._tracked_date:
            await self._async_calibrate_day(
                self._tracked_date.month, self._last_raw_kwh, self._last_actual_kwh
            )
            self._tracked_date = today

        self._last_raw_kwh = raw_kwh_today
        self._last_actual_kwh = actual_kwh_today

    async def _async_calibrate_day(self, month: int, raw_kwh: float, actual_kwh: float) -> None:
        if raw_kwh < _MIN_RAW_KWH:
            return

        observed = max(_RATIO_MIN, min(_RATIO_MAX, actual_kwh / raw_kwh))
        key = str(month)
        entry: dict[str, float] = self._monthly.get(key, {"ratio": observed, "samples": 0})
        if entry["samples"] > 0:
            entry["ratio"] = _EMA_ALPHA * observed + (1 - _EMA_ALPHA) * entry["ratio"]
        entry["samples"] = entry.get("samples", 0) + 1
        self._monthly[key] = entry
        await self._store.async_save({"monthly": self._monthly})

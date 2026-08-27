"""Forecast Export Guard — hold back grid export when tomorrow looks weak.

Idea: if tomorrow's solar forecast is only enough (or not enough) to
refill the battery from its current SOC, today's surplus is worth more
kept in the battery than sold — so disable Solar Sell (`solarSell`) for
the day. If the battery is already full, or tomorrow's forecast comfortably
exceeds what's needed to refill it, there's nothing to conserve for, so
export is left/turned on. The 100%-SOC case falls out of the same
comparison for free: energy_needed is 0, so any positive forecast clears
the sell threshold.

Evaluated once per calendar day, in the window just before sunrise (a
configurable number of minutes) — late enough that the weather forecast
feeding it is as fresh as possible, but before the day's own solar
production has started. Also evaluated once immediately whenever the
guard is enabled, so turning it on mid-day doesn't wait until the next
sunrise window to do anything.
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any

from homeassistant.const import SUN_EVENT_SUNRISE
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.sun import get_astral_event_next
from homeassistant.util import dt as dt_util

from .coordinator import SolarForecastCoordinator, SunsynkCoordinator

_LOGGER = logging.getLogger(__name__)

_NOTIFICATION_ID = "sunsynk_forecast_guard"

MODE_SELLING = "selling"
MODE_HOLDING_BACK = "holding_back"
MODE_NO_FORECAST = "no_forecast"
MODE_DISABLED = "disabled"

DEFAULT_MARGIN_PERCENT = 100
DEFAULT_SUNRISE_OFFSET_MINUTES = 60


@dataclass
class GuardDecision:
    """Result of one evaluation for a single inverter."""

    sell: bool
    mode: str
    forecast_tomorrow_kwh: float | None
    capacity_kwh: float | None
    soc: float | None
    energy_needed_kwh: float | None
    threshold_kwh: float | None


def decide(
    *,
    forecast_tomorrow_kwh: float | None,
    battery_capacity_ah: float | None,
    battery_voltage: float | None,
    battery_soc: float | None,
    margin_percent: float,
) -> GuardDecision:
    """Pure decision function — no I/O, easy to test in isolation.

    sell threshold = energy needed to refill the battery to 100%, scaled
    by margin_percent (100 = no adjustment). Sell if forecast clears the
    threshold; hold back (disable Solar Sell) otherwise.
    """
    if forecast_tomorrow_kwh is None:
        return GuardDecision(
            sell=True,  # fail open — don't hold back on missing data
            mode=MODE_NO_FORECAST,
            forecast_tomorrow_kwh=None,
            capacity_kwh=None,
            soc=battery_soc,
            energy_needed_kwh=None,
            threshold_kwh=None,
        )

    if battery_capacity_ah is None or battery_voltage is None or battery_soc is None:
        return GuardDecision(
            sell=True,
            mode=MODE_NO_FORECAST,
            forecast_tomorrow_kwh=forecast_tomorrow_kwh,
            capacity_kwh=None,
            soc=battery_soc,
            energy_needed_kwh=None,
            threshold_kwh=None,
        )

    capacity_kwh = battery_capacity_ah * battery_voltage / 1000
    soc_clamped = max(0.0, min(100.0, battery_soc))
    energy_needed_kwh = capacity_kwh * (100 - soc_clamped) / 100
    threshold_kwh = energy_needed_kwh * margin_percent / 100

    sell = forecast_tomorrow_kwh > threshold_kwh
    return GuardDecision(
        sell=sell,
        mode=MODE_SELLING if sell else MODE_HOLDING_BACK,
        forecast_tomorrow_kwh=forecast_tomorrow_kwh,
        capacity_kwh=round(capacity_kwh, 2),
        soc=soc_clamped,
        energy_needed_kwh=round(energy_needed_kwh, 2),
        threshold_kwh=round(threshold_kwh, 2),
    )


class ForecastExportGuard:
    """Owns the `solarSell` register based on tomorrow's forecast vs battery need."""

    def __init__(
        self,
        hass: HomeAssistant,
        coordinator: SunsynkCoordinator,
        forecast_coordinator: SolarForecastCoordinator,
        entry_id: str,
        margin_percent: float = DEFAULT_MARGIN_PERCENT,
        sunrise_offset_minutes: int = DEFAULT_SUNRISE_OFFSET_MINUTES,
    ) -> None:
        self._hass = hass
        self._coordinator = coordinator
        self._forecast_coordinator = forecast_coordinator
        self._entry_id = entry_id
        self._margin_percent = margin_percent
        self._sunrise_offset_minutes = sunrise_offset_minutes

        self._enabled = False
        self._last_evaluated_date: date | None = None
        self._last_decision: GuardDecision | None = None

        self._listeners: list[Callable[[], None]] = []
        self._unsub_coordinator: Any = None

    # ── Listener registry ───────────────────────────────────────────────

    def async_add_listener(self, cb: Callable[[], None]) -> Callable[[], None]:
        self._listeners.append(cb)

        def _remove() -> None:
            self._listeners.remove(cb)

        return _remove

    def _notify_listeners(self) -> None:
        for cb in self._listeners:
            cb()

    # ── Lifecycle ────────────────────────────────────────────────────────

    def start(self) -> None:
        self._unsub_coordinator = self._coordinator.async_add_listener(self._on_tick)

    def stop(self) -> None:
        if self._unsub_coordinator:
            self._unsub_coordinator()
            self._unsub_coordinator = None

    @callback
    def _on_tick(self) -> None:
        self._hass.async_create_task(self._async_maybe_evaluate())

    # ── Enable / disable ─────────────────────────────────────────────────

    def set_enabled(self, enabled: bool) -> None:
        self._enabled = enabled
        if enabled:
            # Re-evaluate immediately so enabling mid-day still does
            # something, rather than waiting for tomorrow's sunrise window.
            self._hass.async_create_task(self._async_evaluate())
        self._notify_listeners()

    # ── Config setters (Number entities) ────────────────────────────────

    def set_margin_percent(self, value: float) -> None:
        self._margin_percent = value
        self._notify_listeners()

    def set_sunrise_offset_minutes(self, value: int) -> None:
        self._sunrise_offset_minutes = int(value)
        self._notify_listeners()

    # ── Evaluation window ────────────────────────────────────────────────

    def _now(self) -> datetime:
        return dt_util.now()

    def _evaluation_window_open(self, now: datetime) -> bool:
        """True once per calendar day, from (next sunrise - offset) onward."""
        if self._last_evaluated_date == now.date():
            return False
        next_sunrise = get_astral_event_next(
            self._hass,
            SUN_EVENT_SUNRISE,
            utc_point_in_time=now,
            offset=-timedelta(minutes=self._sunrise_offset_minutes),
        )
        return now >= next_sunrise

    async def _async_maybe_evaluate(self) -> None:
        if not self._enabled:
            return
        now = self._now()
        if self._evaluation_window_open(now):
            await self._async_evaluate()

    async def _async_evaluate(self) -> None:
        if not self._enabled:
            return

        now = self._now()
        forecast_tomorrow = (self._forecast_coordinator.data or {}).get("tomorrow_kwh")

        changed = False
        for serial in self._coordinator.serials:
            battery = (self._coordinator.data or {}).get(serial, {}).get("battery", {})
            decision = decide(
                forecast_tomorrow_kwh=_as_float(forecast_tomorrow),
                battery_capacity_ah=_as_float(battery.get("capacity")),
                battery_voltage=_as_float(battery.get("voltage")),
                battery_soc=_as_float(battery.get("soc")),
                margin_percent=self._margin_percent,
            )

            if decision.mode != MODE_NO_FORECAST:
                await self._coordinator.async_write_setting(
                    serial, "solarSell", 1 if decision.sell else 0
                )
                if decision.mode != (self._last_decision.mode if self._last_decision else None):
                    self._send_notification(decision)

            self._last_decision = decision
            changed = True

        self._last_evaluated_date = now.date()
        if changed:
            self._notify_listeners()

    def _send_notification(self, decision: GuardDecision) -> None:
        if decision.mode == MODE_HOLDING_BACK:
            title = "Forecast Export Guard: holding back export"
            message = (
                f"Tomorrow's forecast ({decision.forecast_tomorrow_kwh:.1f} kWh) is at or "
                f"below what's needed to refill the battery "
                f"({decision.energy_needed_kwh:.1f} kWh) — Solar Sell disabled for today."
            )
        else:
            title = "Forecast Export Guard: export enabled"
            message = (
                f"Tomorrow's forecast ({decision.forecast_tomorrow_kwh:.1f} kWh) covers what's "
                f"needed to refill the battery ({decision.energy_needed_kwh:.1f} kWh) — "
                f"Solar Sell enabled."
            )
        self._hass.async_create_task(
            self._hass.services.async_call(
                "persistent_notification",
                "create",
                {"title": title, "message": message, "notification_id": _NOTIFICATION_ID},
            )
        )

    # ── Properties ───────────────────────────────────────────────────────

    @property
    def is_enabled(self) -> bool:
        return self._enabled

    @property
    def mode(self) -> str:
        if not self._enabled:
            return MODE_DISABLED
        if self._last_decision is None:
            return MODE_NO_FORECAST
        return self._last_decision.mode

    @property
    def last_decision(self) -> GuardDecision | None:
        return self._last_decision

    @property
    def margin_percent(self) -> float:
        return self._margin_percent

    @property
    def sunrise_offset_minutes(self) -> int:
        return self._sunrise_offset_minutes


def _as_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None

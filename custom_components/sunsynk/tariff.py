"""Tariff-aware battery charging/discharging manager."""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Callable

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.util import dt as dt_util

from .coordinator import SunsynkCoordinator

_LOGGER = logging.getLogger(__name__)

_NOTIFICATION_ID = "sunsynk_tariff"

# Human-readable quality state values
QUALITY_OK = "ok"
QUALITY_NOT_FOUND = "not_found"
QUALITY_UNAVAILABLE = "unavailable"
QUALITY_INVALID = "invalid"
QUALITY_STALE = "stale"


class TariffChargingManager:
    """Monitors a price sensor and adjusts battery charge/discharge current.

    Cheap mode (price ≤ cheap_threshold, SOC < target_soc):
      → charge at cheap_current.
    Expensive mode (price ≥ expensive_threshold, SOC > discharge_min_soc):
      → discharge at peak_discharge_current (sell to grid).
    Otherwise:
      → restore normal_charge_current / normal_discharge_current.

    The manager starts DISABLED by default — enable it via the
    "Tariff Manager" switch entity in HA.

    An optional schedule (start_hour / end_hour) limits activity to
    specific hours of the day.  If start_hour > end_hour the range
    wraps midnight (e.g., 22–06).

    price_max_age_minutes: if the price sensor has not updated within
    this many minutes the data is considered stale and any active mode
    is cancelled to avoid acting on outdated prices.  Pass None to
    skip the staleness check.
    """

    def __init__(
        self,
        hass: HomeAssistant,
        coordinator: SunsynkCoordinator,
        price_entity: str,
        # cheap-rate charging
        cheap_threshold: float | None,
        cheap_current: int | None,
        normal_charge_current: int | None,
        target_soc: int,
        # expensive-rate discharging
        expensive_threshold: float | None,
        peak_discharge_current: int | None,
        normal_discharge_current: int | None,
        discharge_min_soc: int,
        # schedule (both None = always active)
        start_hour: int | None = None,
        end_hour: int | None = None,
        # price data quality
        price_max_age_minutes: int | None = 90,
    ) -> None:
        self._hass = hass
        self._coordinator = coordinator
        self._price_entity = price_entity

        self._cheap_threshold = cheap_threshold
        self._cheap_current = cheap_current
        self._normal_charge_current = normal_charge_current
        self._target_soc = target_soc

        self._expensive_threshold = expensive_threshold
        self._peak_discharge_current = peak_discharge_current
        self._normal_discharge_current = normal_discharge_current
        self._discharge_min_soc = discharge_min_soc

        self._start_hour = start_hour
        self._end_hour = end_hour
        self._price_max_age_minutes = price_max_age_minutes

        self._enabled = False  # user must explicitly enable via switch
        self._charging_active = False
        self._discharging_active = False
        self._price_quality: str = QUALITY_NOT_FOUND
        self._listeners: list[Callable[[], None]] = []
        self._unsub_price: Any = None
        self._unsub_coordinator: Any = None

    # ── Listener registry (for switch + sensor entities) ──────────────────

    def async_add_listener(self, cb: Callable[[], None]) -> Callable[[], None]:
        """Register a callback fired on every state change. Returns unsub."""
        self._listeners.append(cb)
        def _remove() -> None:
            self._listeners.remove(cb)
        return _remove

    def _notify_listeners(self) -> None:
        for cb in self._listeners:
            cb()

    # ── Lifecycle ──────────────────────────────────────────────────────────

    def start(self) -> None:
        """Register price-sensor and coordinator listeners."""
        self._unsub_price = async_track_state_change_event(
            self._hass, [self._price_entity], self._on_price_changed
        )
        self._unsub_coordinator = self._coordinator.async_add_listener(
            self._on_coordinator_update
        )
        # Assess initial quality and log result
        quality, _ = self._compute_price_quality()
        self._price_quality = quality
        if quality != QUALITY_OK:
            _LOGGER.warning(
                "Tariff: price entity '%s' quality at startup: %s",
                self._price_entity, quality,
            )

    def stop(self) -> None:
        """Unregister all listeners."""
        if self._unsub_price:
            self._unsub_price()
            self._unsub_price = None
        if self._unsub_coordinator:
            self._unsub_coordinator()
            self._unsub_coordinator = None

    # ── Enable / disable ───────────────────────────────────────────────────

    def set_enabled(self, enabled: bool) -> None:
        """Enable or disable without losing configuration."""
        self._enabled = enabled
        if not enabled:
            for serial in self._coordinator.serials:
                if self._charging_active and self._normal_charge_current is not None:
                    self._hass.async_create_task(
                        self._coordinator.async_write_setting(
                            serial, "chargeCurrent", self._normal_charge_current
                        )
                    )
                if self._discharging_active and self._normal_discharge_current is not None:
                    self._hass.async_create_task(
                        self._coordinator.async_write_setting(
                            serial, "dischargeCurrent", self._normal_discharge_current
                        )
                    )
            self._charging_active = False
            self._discharging_active = False
            _LOGGER.info("Tariff manager disabled — normal currents restored")
            self._send_notification("Tariff Manager disabled", "Normal charge/discharge currents restored.")
        else:
            _LOGGER.info("Tariff manager enabled — re-evaluating current price")
            state = self._hass.states.get(self._price_entity)
            if state and state.state not in ("unknown", "unavailable"):
                self._hass.async_create_task(self._evaluate(state.state))
        self._notify_listeners()

    # ── Price quality ──────────────────────────────────────────────────────

    def _compute_price_quality(self) -> tuple[str, str]:
        """Return (quality_state, detail_message) without side effects."""
        state = self._hass.states.get(self._price_entity)
        if state is None:
            return QUALITY_NOT_FOUND, f"Entity '{self._price_entity}' not found"
        if state.state in ("unknown", "unavailable"):
            return QUALITY_UNAVAILABLE, f"State is '{state.state}'"
        try:
            float(state.state)
        except (ValueError, TypeError):
            return QUALITY_INVALID, f"Non-numeric state '{state.state}'"
        if self._price_max_age_minutes is not None:
            age_seconds = (dt_util.utcnow() - state.last_updated).total_seconds()
            age_minutes = age_seconds / 60
            if age_minutes > self._price_max_age_minutes:
                return (
                    QUALITY_STALE,
                    f"Last updated {age_minutes:.0f} min ago (max {self._price_max_age_minutes} min)",
                )
        return QUALITY_OK, "ok"

    def _refresh_quality(self) -> bool:
        """Recompute quality; if changed notify listeners; return True if quality is OK."""
        quality, detail = self._compute_price_quality()
        if quality != self._price_quality:
            prev = self._price_quality
            self._price_quality = quality
            _LOGGER.info(
                "Tariff: price quality changed %s → %s (%s)",
                prev, quality, detail,
            )
            if quality != QUALITY_OK:
                self._hass.async_create_task(self._handle_bad_quality(detail))
            self._notify_listeners()
        return quality == QUALITY_OK

    async def _handle_bad_quality(self, detail: str) -> None:
        """Stop any active mode when price data quality drops."""
        stopped_something = False
        for serial in self._coordinator.serials:
            if self._charging_active and self._normal_charge_current is not None:
                _LOGGER.warning(
                    "Tariff: stopping charging [%s] due to bad price quality — %s", serial, detail
                )
                await self._coordinator.async_write_setting(
                    serial, "chargeCurrent", self._normal_charge_current
                )
                self._charging_active = False
                stopped_something = True
            if self._discharging_active and self._normal_discharge_current is not None:
                _LOGGER.warning(
                    "Tariff: stopping discharging [%s] due to bad price quality — %s", serial, detail
                )
                await self._coordinator.async_write_setting(
                    serial, "dischargeCurrent", self._normal_discharge_current
                )
                self._discharging_active = False
                stopped_something = True
        if stopped_something:
            self._send_notification(
                "Tariff: stopped — price data quality issue",
                f"Normal currents restored. Reason: {detail}",
            )
            self._notify_listeners()

    # ── Scheduler ─────────────────────────────────────────────────────────

    def _is_in_schedule(self) -> bool:
        """Return True if current hour is within the configured active window."""
        if self._start_hour is None or self._end_hour is None:
            return True
        hour = datetime.now().hour
        if self._start_hour <= self._end_hour:
            return self._start_hour <= hour < self._end_hour
        # wraps midnight (e.g. 22–06)
        return hour >= self._start_hour or hour < self._end_hour

    # ── Listeners ──────────────────────────────────────────────────────────

    @callback
    def _on_price_changed(self, event: Any) -> None:
        new_state = event.data.get("new_state")
        if new_state and new_state.state not in ("unknown", "unavailable"):
            self._hass.async_create_task(self._evaluate(new_state.state))
        else:
            # State went unavailable — refresh quality immediately
            self._refresh_quality()

    @callback
    def _on_coordinator_update(self) -> None:
        # Periodic quality check (catches staleness between price updates)
        quality_ok = self._refresh_quality()
        if quality_ok:
            state = self._hass.states.get(self._price_entity)
            if state and state.state not in ("unknown", "unavailable"):
                self._hass.async_create_task(self._evaluate(state.state))

    # ── Core evaluation ────────────────────────────────────────────────────

    async def _evaluate(self, price_state: str) -> None:
        if not self._enabled:
            return

        # Validate quality before acting
        quality, detail = self._compute_price_quality()
        if quality != self._price_quality:
            self._price_quality = quality
            self._notify_listeners()
        if quality != QUALITY_OK:
            await self._handle_bad_quality(detail)
            return

        try:
            price = float(price_state)
        except (ValueError, TypeError):
            _LOGGER.debug("Tariff: cannot parse price '%s'", price_state)
            return

        in_schedule = self._is_in_schedule()

        for serial in self._coordinator.serials:
            battery = (self._coordinator.data or {}).get(serial, {}).get("battery", {})
            try:
                soc = float(battery.get("soc", 0))
            except (ValueError, TypeError):
                soc = 0.0

            await self._evaluate_charging(serial, price, soc, in_schedule)
            await self._evaluate_discharging(serial, price, soc, in_schedule)

    async def _evaluate_charging(
        self, serial: str, price: float, soc: float, in_schedule: bool
    ) -> None:
        if self._cheap_threshold is None or self._cheap_current is None:
            return

        should_charge = in_schedule and price <= self._cheap_threshold and soc < self._target_soc

        if should_charge and not self._charging_active:
            _LOGGER.info(
                "Tariff charging ON [%s] — price %.4f ≤ %.4f, SOC %.0f%% < %d%%",
                serial, price, self._cheap_threshold, soc, self._target_soc,
            )
            await self._coordinator.async_write_setting(
                serial, "chargeCurrent", self._cheap_current
            )
            self._charging_active = True
            self._send_notification(
                "Tariff: Charging started",
                f"Price {price:.4f} ≤ threshold {self._cheap_threshold}. "
                f"Charging at {self._cheap_current} A until {self._target_soc}% SOC.",
            )
            self._notify_listeners()

        elif not should_charge and self._charging_active:
            if not in_schedule:
                reason = "outside active schedule"
            elif price > self._cheap_threshold:
                reason = f"price {price:.4f} > threshold {self._cheap_threshold}"
            else:
                reason = f"SOC {soc:.0f}% reached target {self._target_soc}%"
            _LOGGER.info("Tariff charging OFF [%s] — %s", serial, reason)
            await self._coordinator.async_write_setting(
                serial, "chargeCurrent", self._normal_charge_current
            )
            self._charging_active = False
            self._send_notification(
                "Tariff: Charging stopped",
                f"{reason.capitalize()}. Restored {self._normal_charge_current} A.",
            )
            self._notify_listeners()

    async def _evaluate_discharging(
        self, serial: str, price: float, soc: float, in_schedule: bool
    ) -> None:
        if self._expensive_threshold is None or self._peak_discharge_current is None:
            return

        should_discharge = (
            in_schedule and price >= self._expensive_threshold and soc > self._discharge_min_soc
        )

        if should_discharge and not self._discharging_active:
            _LOGGER.info(
                "Tariff discharging ON [%s] — price %.4f ≥ %.4f, SOC %.0f%% > %d%%",
                serial, price, self._expensive_threshold, soc, self._discharge_min_soc,
            )
            await self._coordinator.async_write_setting(
                serial, "dischargeCurrent", self._peak_discharge_current
            )
            self._discharging_active = True
            self._send_notification(
                "Tariff: Discharging started",
                f"Price {price:.4f} ≥ threshold {self._expensive_threshold}. "
                f"Discharging at {self._peak_discharge_current} A "
                f"(min SOC {self._discharge_min_soc}%).",
            )
            self._notify_listeners()

        elif not should_discharge and self._discharging_active:
            if not in_schedule:
                reason = "outside active schedule"
            elif price < self._expensive_threshold:
                reason = f"price {price:.4f} < threshold {self._expensive_threshold}"
            else:
                reason = f"SOC {soc:.0f}% reached minimum {self._discharge_min_soc}%"
            _LOGGER.info("Tariff discharging OFF [%s] — %s", serial, reason)
            await self._coordinator.async_write_setting(
                serial, "dischargeCurrent", self._normal_discharge_current
            )
            self._discharging_active = False
            self._send_notification(
                "Tariff: Discharging stopped",
                f"{reason.capitalize()}. Restored {self._normal_discharge_current} A.",
            )
            self._notify_listeners()

    # ── Notifications ──────────────────────────────────────────────────────

    def _send_notification(self, title: str, message: str) -> None:
        self._hass.async_create_task(
            self._hass.services.async_call(
                "persistent_notification",
                "create",
                {
                    "title": title,
                    "message": message,
                    "notification_id": _NOTIFICATION_ID,
                },
            )
        )

    # ── Properties ─────────────────────────────────────────────────────────

    @property
    def is_enabled(self) -> bool:
        return self._enabled

    @property
    def is_charging_active(self) -> bool:
        return self._charging_active

    @property
    def is_discharging_active(self) -> bool:
        return self._discharging_active

    @property
    def price_quality(self) -> str:
        return self._price_quality

    @property
    def mode(self) -> str:
        """Return human-readable current mode."""
        if not self._enabled:
            return "disabled"
        if self._charging_active:
            return "charging"
        if self._discharging_active:
            return "discharging"
        return "idle"

    @property
    def price_entity(self) -> str:
        return self._price_entity

    @property
    def cheap_threshold(self) -> float | None:
        return self._cheap_threshold

    @property
    def expensive_threshold(self) -> float | None:
        return self._expensive_threshold

    @property
    def start_hour(self) -> int | None:
        return self._start_hour

    @property
    def end_hour(self) -> int | None:
        return self._end_hour

    @property
    def price_max_age_minutes(self) -> int | None:
        return self._price_max_age_minutes

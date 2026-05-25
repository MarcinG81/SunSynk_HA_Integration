"""Tariff-aware battery charging/discharging manager."""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Callable

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.event import async_track_state_change_event

from .coordinator import SunsynkCoordinator

_LOGGER = logging.getLogger(__name__)

_NOTIFICATION_ID = "sunsynk_tariff"


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

        self._enabled = False  # user must explicitly enable via switch
        self._charging_active = False
        self._discharging_active = False
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
        state = self._hass.states.get(self._price_entity)
        if state is None:
            _LOGGER.warning("Tariff: price entity '%s' not found", self._price_entity)
        elif state.state in ("unknown", "unavailable"):
            _LOGGER.warning(
                "Tariff: price entity '%s' is currently %s", self._price_entity, state.state
            )
        else:
            try:
                float(state.state)
            except (ValueError, TypeError):
                _LOGGER.warning(
                    "Tariff: price entity '%s' has non-numeric state '%s'",
                    self._price_entity, state.state,
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

    @callback
    def _on_coordinator_update(self) -> None:
        state = self._hass.states.get(self._price_entity)
        if state and state.state not in ("unknown", "unavailable"):
            self._hass.async_create_task(self._evaluate(state.state))

    # ── Core evaluation ────────────────────────────────────────────────────

    async def _evaluate(self, price_state: str) -> None:
        if not self._enabled:
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

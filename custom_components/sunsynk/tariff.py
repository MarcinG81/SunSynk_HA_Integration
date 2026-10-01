"""Tariff-aware battery charging/discharging manager."""

from __future__ import annotations

import logging
import math
from asyncio import Lock
from collections.abc import Callable
from typing import Any

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

    export_price_entity: optional second price sensor. When set, it
    drives discharging decisions while price_entity continues to drive
    charging — for tariffs like Octopus where the import and export
    rates aren't linked (#16). Leave unset (or equal to price_entity)
    to keep the original single-price behaviour: both sides read the
    same sensor. Quality (staleness/availability) is tracked separately
    per entity, so a problem on one side only pauses that side.
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
        # optional separate export/sell price (defaults to price_entity)
        export_price_entity: str | None = None,
    ) -> None:
        self._hass = hass
        self._coordinator = coordinator
        self._price_entity = price_entity
        self._export_price_entity = export_price_entity or price_entity

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
        # Runtime decisions are per physical write target.  A config entry may
        # contain several independent inverters (with parallel slaves already
        # collapsed to their master by ``write_target_serials``), so a single
        # boolean would let the first inverter suppress decisions for all
        # remaining ones.
        self._charging_active_serials: set[str] = set()
        self._discharging_active_serials: set[str] = set()
        self._price_quality: str = QUALITY_NOT_FOUND
        self._export_price_quality: str = QUALITY_NOT_FOUND
        self._soc_quality_by_serial: dict[str, str] = {}
        self._listeners: list[Callable[[], None]] = []
        self._evaluate_lock = Lock()
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
        tracked_entities = {self._price_entity, self._export_price_entity}
        self._unsub_price = async_track_state_change_event(
            self._hass, list(tracked_entities), self._on_price_changed
        )
        self._unsub_coordinator = self._coordinator.async_add_listener(
            self._on_coordinator_update
        )
        # Assess initial quality and log result
        quality, _ = self._compute_price_quality(self._price_entity)
        self._price_quality = quality
        if quality != QUALITY_OK:
            _LOGGER.warning(
                "Tariff: price entity '%s' quality at startup: %s",
                self._price_entity,
                quality,
            )
        export_quality, _ = self._compute_price_quality(self._export_price_entity)
        self._export_price_quality = export_quality
        if (
            self._export_price_entity != self._price_entity
            and export_quality != QUALITY_OK
        ):
            _LOGGER.warning(
                "Tariff: export price entity '%s' quality at startup: %s",
                self._export_price_entity,
                export_quality,
            )

    def stop(self) -> None:
        """Unregister all listeners without changing inverter settings."""
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
            if self._charging_active_serials or self._discharging_active_serials:
                self._hass.async_create_task(self._async_restore_active_currents())
            _LOGGER.info("Tariff manager disabled — restoring normal currents")
            self._send_notification(
                "Tariff Manager disabled",
                "Normal charge/discharge current restoration started.",
            )
        else:
            _LOGGER.info("Tariff manager enabled — re-evaluating current price")
            self._hass.async_create_task(self._evaluate())
        self._notify_listeners()

    async def _async_restore_active_currents(self) -> bool:
        """Await restoration of all currents currently owned by the manager."""
        success = True
        async with self._evaluate_lock:
            charge_serials = set(self._charging_active_serials)
            discharge_serials = set(self._discharging_active_serials)

            for serial in self._coordinator.write_target_serials:
                restores: dict[str, int] = {}
                if serial in charge_serials and self._normal_charge_current is not None:
                    restores["chargeCurrent"] = self._normal_charge_current
                if (
                    serial in discharge_serials
                    and self._normal_discharge_current is not None
                ):
                    restores["dischargeCurrent"] = self._normal_discharge_current
                if restores:
                    try:
                        await self._coordinator.async_write_settings(serial, restores)
                    except Exception as err:  # noqa: BLE001
                        success = False
                        _LOGGER.error(
                            "Tariff shutdown: could not restore currents for %s: %s",
                            serial,
                            err,
                        )

            self._charging_active_serials.clear()
            self._discharging_active_serials.clear()

        self._notify_listeners()
        return success

    async def async_shutdown(self) -> bool:
        """Stop callbacks and restore owned currents before unload/reload."""
        self.stop()
        self._enabled = False
        return await self._async_restore_active_currents()

    # ── Price quality ──────────────────────────────────────────────────────

    def _compute_price_quality(self, entity_id: str | None = None) -> tuple[str, str]:
        """Return (quality_state, detail_message) for one price entity, without side effects.

        Defaults to the import price entity when called with no argument —
        kept for backward compatibility with callers that only care about
        the (charging-side) price, and with existing tests.
        """
        entity_id = entity_id or self._price_entity
        state = self._hass.states.get(entity_id)
        if state is None:
            return QUALITY_NOT_FOUND, f"Entity '{entity_id}' not found"
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

    def _read_price(self, entity_id: str) -> float | None:
        """Return the current numeric state of a price entity, or None."""
        state = self._hass.states.get(entity_id)
        if state is None or state.state in ("unknown", "unavailable"):
            return None
        try:
            return float(state.state)
        except (ValueError, TypeError):
            return None

    async def _stop_charging(self, serial: str, reason: str) -> None:
        """Stop charging (no-op if already stopped)."""
        if serial not in self._charging_active_serials:
            return
        _LOGGER.warning("Tariff: stopping charging [%s] — %s", serial, reason)
        if self._normal_charge_current is not None:
            await self._coordinator.async_write_setting(
                serial, "chargeCurrent", self._normal_charge_current
            )
        self._charging_active_serials.discard(serial)
        self._send_notification(
            "Tariff: Charging stopped", f"Inverter {serial}: {reason.capitalize()}."
        )
        self._notify_listeners()

    async def _stop_discharging(self, serial: str, reason: str) -> None:
        """Stop discharging (no-op if already stopped)."""
        if serial not in self._discharging_active_serials:
            return
        _LOGGER.warning("Tariff: stopping discharging [%s] — %s", serial, reason)
        if self._normal_discharge_current is not None:
            await self._coordinator.async_write_setting(
                serial, "dischargeCurrent", self._normal_discharge_current
            )
        self._discharging_active_serials.discard(serial)
        self._send_notification(
            "Tariff: Discharging stopped", f"Inverter {serial}: {reason.capitalize()}."
        )
        self._notify_listeners()

    # ── Scheduler ─────────────────────────────────────────────────────────

    def _is_in_schedule(self) -> bool:
        """Return True if current hour is within the configured active window."""
        if self._start_hour is None or self._end_hour is None:
            return True
        hour = dt_util.now().hour
        if self._start_hour <= self._end_hour:
            return self._start_hour <= hour < self._end_hour
        # wraps midnight (e.g. 22–06)
        return hour >= self._start_hour or hour < self._end_hour

    # ── Listeners ──────────────────────────────────────────────────────────

    @callback
    def _on_price_changed(self, event: Any) -> None:
        self._hass.async_create_task(self._evaluate())

    @callback
    def _on_coordinator_update(self) -> None:
        self._hass.async_create_task(self._evaluate())

    # ── Core evaluation ────────────────────────────────────────────────────

    async def _evaluate(self) -> None:
        """Serialize evaluations triggered by price and coordinator updates."""
        async with self._evaluate_lock:
            await self._evaluate_locked()

    async def _evaluate_locked(self) -> None:
        """Re-check both price entities and act on each side independently.

        Import price quality only gates charging; export price quality only
        gates discharging — so a stale/missing export sensor (e.g. someone
        hasn't got around to setting one, and it defaults to price_entity)
        never blocks charging, and vice versa. Always re-reads current state
        rather than trusting a triggering event's payload, since a single
        entity's state-change event doesn't carry the *other* entity's
        current value.
        """
        if not self._enabled:
            return

        import_quality, import_detail = self._compute_price_quality(self._price_entity)
        if import_quality != self._price_quality:
            _LOGGER.info(
                "Tariff: import price quality changed %s → %s (%s)",
                self._price_quality,
                import_quality,
                import_detail,
            )
            self._price_quality = import_quality
            self._notify_listeners()

        export_quality, export_detail = self._compute_price_quality(
            self._export_price_entity
        )
        if export_quality != self._export_price_quality:
            _LOGGER.info(
                "Tariff: export price quality changed %s → %s (%s)",
                self._export_price_quality,
                export_quality,
                export_detail,
            )
            self._export_price_quality = export_quality
            self._notify_listeners()

        in_schedule = self._is_in_schedule()

        for serial in self._coordinator.write_target_serials:
            battery = (self._coordinator.data or {}).get(serial, {}).get("battery", {})
            soc, soc_quality = self._read_soc(battery)
            previous_soc_quality = self._soc_quality_by_serial.get(serial)
            if soc_quality != previous_soc_quality:
                log = _LOGGER.info if soc_quality == QUALITY_OK else _LOGGER.warning
                log(
                    "Tariff: battery SOC quality changed [%s] %s → %s",
                    serial,
                    previous_soc_quality or "unknown",
                    soc_quality,
                )
                self._soc_quality_by_serial[serial] = soc_quality
                self._notify_listeners()

            if soc is None:
                # Missing, non-finite or out-of-range SOC is never equivalent
                # to 0%.  Stop anything this manager owns and do not make a
                # new charge/discharge decision until trustworthy SOC returns.
                reason = f"battery SOC quality: {soc_quality}"
                await self._stop_charging(serial, reason)
                await self._stop_discharging(serial, reason)
                continue

            if import_quality == QUALITY_OK:
                price = self._read_price(self._price_entity)
                if price is not None:
                    await self._evaluate_charging(serial, price, soc, in_schedule)
            else:
                await self._stop_charging(
                    serial, f"import price quality: {import_detail}"
                )

            if export_quality == QUALITY_OK:
                price = self._read_price(self._export_price_entity)
                if price is not None:
                    await self._evaluate_discharging(serial, price, soc, in_schedule)
            else:
                await self._stop_discharging(
                    serial, f"export price quality: {export_detail}"
                )

    @staticmethod
    def _read_soc(battery: dict[str, Any]) -> tuple[float | None, str]:
        """Return a validated SOC; unsafe values fail closed."""
        if "soc" not in battery or battery["soc"] is None:
            return None, "missing"
        try:
            soc = float(battery["soc"])
        except (TypeError, ValueError):
            return None, QUALITY_INVALID
        if not math.isfinite(soc):
            return None, QUALITY_INVALID
        if not 0 <= soc <= 100:
            return None, "out_of_range"
        return soc, QUALITY_OK

    async def _evaluate_charging(
        self, serial: str, price: float, soc: float, in_schedule: bool
    ) -> None:
        if self._cheap_threshold is None or self._cheap_current is None:
            return

        should_charge = (
            in_schedule and price <= self._cheap_threshold and soc < self._target_soc
        )

        if should_charge and serial not in self._charging_active_serials:
            # Charging wins if thresholds overlap.  A physical inverter must
            # never be commanded to charge and discharge at the same time.
            await self._stop_discharging(serial, "charging took priority")
            _LOGGER.info(
                "Tariff charging ON [%s] — price %.4f ≤ %.4f, SOC %.0f%% < %d%%",
                serial,
                price,
                self._cheap_threshold,
                soc,
                self._target_soc,
            )
            await self._coordinator.async_write_setting(
                serial, "chargeCurrent", self._cheap_current
            )
            self._charging_active_serials.add(serial)
            self._send_notification(
                "Tariff: Charging started",
                f"Inverter {serial}: price {price:.4f} ≤ threshold {self._cheap_threshold}. "
                f"Charging at {self._cheap_current} A until {self._target_soc}% SOC.",
            )
            self._notify_listeners()

        elif not should_charge and serial in self._charging_active_serials:
            if not in_schedule:
                reason = "outside active schedule"
            elif price > self._cheap_threshold:
                reason = f"price {price:.4f} > threshold {self._cheap_threshold}"
            else:
                reason = f"SOC {soc:.0f}% reached target {self._target_soc}%"
            await self._stop_charging(serial, reason)

    async def _evaluate_discharging(
        self, serial: str, price: float, soc: float, in_schedule: bool
    ) -> None:
        if self._expensive_threshold is None or self._peak_discharge_current is None:
            return

        should_discharge = (
            in_schedule
            and price >= self._expensive_threshold
            and soc > self._discharge_min_soc
        )

        if (
            should_discharge
            and serial not in self._discharging_active_serials
            and serial not in self._charging_active_serials
        ):
            _LOGGER.info(
                "Tariff discharging ON [%s] — price %.4f ≥ %.4f, SOC %.0f%% > %d%%",
                serial,
                price,
                self._expensive_threshold,
                soc,
                self._discharge_min_soc,
            )
            await self._coordinator.async_write_setting(
                serial, "dischargeCurrent", self._peak_discharge_current
            )
            self._discharging_active_serials.add(serial)
            self._send_notification(
                "Tariff: Discharging started",
                f"Inverter {serial}: price {price:.4f} ≥ threshold {self._expensive_threshold}. "
                f"Discharging at {self._peak_discharge_current} A "
                f"(min SOC {self._discharge_min_soc}%).",
            )
            self._notify_listeners()

        elif not should_discharge and serial in self._discharging_active_serials:
            if not in_schedule:
                reason = "outside active schedule"
            elif price < self._expensive_threshold:
                reason = f"price {price:.4f} < threshold {self._expensive_threshold}"
            else:
                reason = f"SOC {soc:.0f}% reached minimum {self._discharge_min_soc}%"
            await self._stop_discharging(serial, reason)

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

    # ── Runtime setters (called by TariffNumberEntity) ────────────────────

    def _re_evaluate(self) -> None:
        """Trigger a fresh evaluation if the manager is active."""
        if not self._enabled:
            return
        self._hass.async_create_task(self._evaluate())

    def set_cheap_threshold(self, value: float | None) -> None:
        self._cheap_threshold = value
        self._re_evaluate()
        self._notify_listeners()

    def set_cheap_current(self, value: int | None) -> None:
        self._cheap_current = None if value is None else int(value)
        self._re_evaluate()
        self._notify_listeners()

    def set_normal_charge_current(self, value: int | None) -> None:
        self._normal_charge_current = None if value is None else int(value)
        self._notify_listeners()

    def set_target_soc(self, value: int) -> None:
        self._target_soc = int(value)
        self._re_evaluate()
        self._notify_listeners()

    def set_expensive_threshold(self, value: float | None) -> None:
        self._expensive_threshold = value
        self._re_evaluate()
        self._notify_listeners()

    def set_peak_discharge_current(self, value: int | None) -> None:
        self._peak_discharge_current = None if value is None else int(value)
        self._re_evaluate()
        self._notify_listeners()

    def set_normal_discharge_current(self, value: int | None) -> None:
        self._normal_discharge_current = None if value is None else int(value)
        self._notify_listeners()

    def set_discharge_min_soc(self, value: int) -> None:
        self._discharge_min_soc = int(value)
        self._re_evaluate()
        self._notify_listeners()

    # ── Properties ─────────────────────────────────────────────────────────

    @property
    def is_enabled(self) -> bool:
        return self._enabled

    @property
    def is_charging_active(self) -> bool:
        """Return whether any inverter is tariff-charging."""
        return bool(self._charging_active_serials)

    @property
    def is_discharging_active(self) -> bool:
        """Return whether any inverter is tariff-discharging."""
        return bool(self._discharging_active_serials)

    def is_charging_active_for(self, serial: str) -> bool:
        """Return whether one physical write target is tariff-charging."""
        return serial in self._charging_active_serials

    def is_discharging_active_for(self, serial: str) -> bool:
        """Return whether one physical write target is tariff-discharging."""
        return serial in self._discharging_active_serials

    def mode_for(self, serial: str) -> str:
        """Return the tariff mode for one physical write target."""
        if not self._enabled:
            return "disabled"
        if self.is_charging_active_for(serial):
            return "charging"
        if self.is_discharging_active_for(serial):
            return "discharging"
        return "idle"

    @property
    def per_inverter_modes(self) -> dict[str, str]:
        """Return a stable snapshot of modes keyed by physical target."""
        return {
            serial: self.mode_for(serial)
            for serial in self._coordinator.write_target_serials
        }

    @property
    def soc_quality_by_inverter(self) -> dict[str, str]:
        """Return SOC quality for each physical write target."""
        return dict(self._soc_quality_by_serial)

    @property
    def price_quality(self) -> str:
        return self._price_quality

    @property
    def mode(self) -> str:
        """Return aggregate mode; ``mixed`` means targets differ."""
        if not self._enabled:
            return "disabled"
        modes = set(self.per_inverter_modes.values())
        if not modes:
            return "idle"
        if len(modes) == 1:
            return modes.pop()
        return "mixed"

    @property
    def price_entity(self) -> str:
        return self._price_entity

    @property
    def export_price_entity(self) -> str:
        return self._export_price_entity

    @property
    def export_price_quality(self) -> str:
        return self._export_price_quality

    @property
    def cheap_threshold(self) -> float | None:
        return self._cheap_threshold

    @property
    def expensive_threshold(self) -> float | None:
        return self._expensive_threshold

    @property
    def target_soc(self) -> int:
        return self._target_soc

    @property
    def normal_charge_current(self) -> int | None:
        return self._normal_charge_current

    @property
    def normal_discharge_current(self) -> int | None:
        return self._normal_discharge_current

    @property
    def discharge_min_soc(self) -> int:
        return self._discharge_min_soc

    @property
    def start_hour(self) -> int | None:
        return self._start_hour

    @property
    def end_hour(self) -> int | None:
        return self._end_hour

    @property
    def price_max_age_minutes(self) -> int | None:
        return self._price_max_age_minutes

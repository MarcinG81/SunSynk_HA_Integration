"""Up-to-10 HA-side virtual charge/discharge slots, resolved onto the
inverter's six System Mode timer slots.

Background
----------
Per Sunsynk's own documentation ("Avoiding Conflicts in the System Mode
Timer"), the 6 System Mode Timer slots MUST be set chronologically —
each slot's start time must be later than the previous one — and *only*
Timer 6 is allowed to roll across midnight into Timer 1. No other pair of
slots can wrap, and setting a lower-numbered slot to a later time-of-day
than a higher-numbered slot is exactly the "conflict" that article warns
about: the inverter will not reliably act on the out-of-order slot.

This module drives all six slots. Slot 1 holds the earlier time-of-day
boundary and slots 2-6 the later one, all starting at slot 6's start: slot 6
is the only slot the firmware lets wrap past midnight, and slots 2-5 carry
the same settings, so none of the user's own start times is left in the
middle of the day to take over (#36; before, slots 3-5 kept theirs, and a
price override could land in one of them and export nothing). This is
recomputed fresh on every tick. Which one is "currently active" flips
between them as the day progresses (e.g. slot 1 governs the daytime arc,
slot 6 governs the overnight arc that wraps into the next day), but their
relative ordering by time-of-day never does.

Price-driven decisions from TariffChargingManager do not need a
breakpoint of their own — they always take priority and are applied by
live-patching whichever of slot 1 / slot 6 is currently active (cap /
sell power / on), without touching its start time or the other slot.

Ownership is taken only when there is something to drive (an enabled
window or a price decision), and only once the user's own timer has been
captured, so it can always be put back. With nothing to do, the scheduler
stands by: the user's timer is in place and the capture follows it.

Every current and sell power written is also kept within what the hardware
and the installation allow: the battery's BMS limit and the maximum export
power, both shared between the inverters of a parallel group (see
current_limits).
"""

from __future__ import annotations

import asyncio
import logging
from asyncio import Lock
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum
from hashlib import sha256
from typing import TYPE_CHECKING, Any

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.event import async_track_point_in_time
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import DOMAIN
from .coordinator import SunsynkCoordinator
from .current_limits import battery_safe_current, group_sizes

if TYPE_CHECKING:
    from .tariff import TariffChargingManager

_LOGGER = logging.getLogger(__name__)

STORAGE_VERSION = 1

MAX_VIRTUAL_SLOTS = 10
# A boundary is armed on the daily timers only once it is less than a day
# away, and an idle slot starts at least this long after the previous day's
# occurrence of the upcoming boundary.
_ARM_MARGIN = timedelta(minutes=1)

# The only pair of physical Sunsynk timer slots that can wrap past
# midnight (Timer 6 -> Timer 1) — see module docstring.
PHYSICAL_SLOTS = (1, 6)
MODE_CHARGE = "charge"
MODE_DISCHARGE = "discharge"
MODE_IDLE = "idle"


class _CurrentState(Enum):
    """Named non-write states tracked by current-setting deduplication."""

    IDLE = "idle"


WEEKDAY_NAMES = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
ALL_WEEKDAYS: frozenset[int] = frozenset(range(7))

# setting_key names for each physical timer slot. The scheduler drives all
# six: slot 1 holds the earlier boundary and slots 2-6 the later one, so no
# start time of the user's own schedule is left in the middle of the day to
# take over from it (#36). `on` is the slot's Grid Charge tick.
_PHYSICAL_KEYS: dict[int, dict[str, str]] = {
    n: {
        "on": f"time{n}on",
        "cap": f"cap{n}",
        "pac": f"sellTime{n}Pac",
        "start": f"sellTime{n}",
        "en": f"sellTime{n}on",
        "en_alt": f"sellTime{n}En",
    }
    for n in range(1, 7)
}
_SELL_FLAG_KEYS = frozenset(
    {key for keys in _PHYSICAL_KEYS.values() for key in (keys["en"], keys["en_alt"])}
)
_GRID_CHARGE_KEYS = tuple(keys["on"] for keys in _PHYSICAL_KEYS.values())
# Rewrites of a window the inverter doesn't report as written, before giving
# up until the window changes: an inverter that refuses a value must not be
# written to on every refresh.
_MAX_REWRITES = 3

# Restore slot data fields before the Grid Charge ticks so the inverter never
# briefly charges in a partially restored slot. Currents return to configured
# normal values separately; snapshotting a live tariff override would be
# unsafe.
_MANAGED_SETTING_KEYS = (
    tuple(
        key
        for keys in _PHYSICAL_KEYS.values()
        for key in (keys["cap"], keys["pac"], keys["start"], keys["en"], keys["en_alt"])
    )
    + _GRID_CHARGE_KEYS
)


def _parse_hhmm(value: str) -> tuple[int, int]:
    hh, mm = value.split(":", 1)
    return int(hh), int(mm)


def _duration_minutes(start: str, end: str) -> int:
    sh, sm = _parse_hhmm(start)
    eh, em = _parse_hhmm(end)
    minutes = (eh * 60 + em) - (sh * 60 + sm)
    if minutes <= 0:
        minutes += 24 * 60
    return minutes


def _most_recent_occurrence(
    ref: datetime, weekday: int, hour: int, minute: int
) -> datetime:
    """Latest datetime <= ref matching the given weekday (0=Mon) and time."""
    candidate = ref.replace(hour=hour, minute=minute, second=0, microsecond=0)
    days_back = (ref.weekday() - weekday) % 7
    candidate -= timedelta(days=days_back)
    if candidate > ref:
        candidate -= timedelta(days=7)
    return candidate


def _next_occurrence(after: datetime, weekday: int, hour: int, minute: int) -> datetime:
    """Earliest datetime > after matching the given weekday (0=Mon) and time."""
    candidate = after.replace(hour=hour, minute=minute, second=0, microsecond=0)
    days_ahead = (weekday - after.weekday()) % 7
    candidate += timedelta(days=days_ahead)
    if candidate <= after:
        candidate += timedelta(days=7)
    return candidate


@dataclass(frozen=True)
class VirtualSlot:
    """One HA-side, user-defined charge/discharge/idle window."""

    slot_id: int
    start: str  # "HH:MM"
    end: str  # "HH:MM"
    mode: str  # charge | discharge | idle
    weekdays: frozenset[int] = ALL_WEEKDAYS
    current: int | None = None  # A — chargeCurrent / dischargeCurrent
    target_soc: int | None = None  # % — cap{n}
    sell_power: int = 0  # W — sellTime{n}Pac, only meaningful for discharge
    priority: int = 0
    enabled: bool = True

    @property
    def duration_minutes(self) -> int:
        return _duration_minutes(self.start, self.end)

    def window_containing(self, now: datetime) -> tuple[datetime, datetime] | None:
        """Return (window_start, window_end) if this slot is active at `now`."""
        if not self.enabled or not self.weekdays:
            return None
        hh, mm = _parse_hhmm(self.start)
        starts = [_most_recent_occurrence(now, wd, hh, mm) for wd in self.weekdays]
        window_start = max(starts)
        window_end = window_start + timedelta(minutes=self.duration_minutes)
        if window_start <= now < window_end:
            return window_start, window_end
        return None

    def next_start_after(self, after: datetime) -> datetime:
        hh, mm = _parse_hhmm(self.start)
        return min(_next_occurrence(after, wd, hh, mm) for wd in self.weekdays)

    def to_dict(self) -> dict[str, Any]:
        return {
            "slot_id": self.slot_id,
            "start": self.start,
            "end": self.end,
            "mode": self.mode,
            "weekdays": sorted(self.weekdays),
            "current": self.current,
            "target_soc": self.target_soc,
            "sell_power": self.sell_power,
            "priority": self.priority,
            "enabled": self.enabled,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> VirtualSlot:
        return cls(
            slot_id=int(data["slot_id"]),
            start=data["start"],
            end=data["end"],
            mode=data["mode"],
            weekdays=frozenset(data.get("weekdays", sorted(ALL_WEEKDAYS))),
            current=data.get("current"),
            target_soc=data.get("target_soc"),
            sell_power=int(data.get("sell_power", 0)),
            priority=int(data.get("priority", 0)),
            enabled=bool(data.get("enabled", True)),
        )


@dataclass
class Resolution:
    """What should be in force at a given physical slot."""

    mode: str
    current: int | None
    target_soc: int | None
    sell_power: int
    source: str  # "price_override" | "virtual_slot:<id>" | "none"


_IDLE = Resolution(
    mode=MODE_IDLE, current=None, target_soc=None, sell_power=0, source="none"
)


@dataclass
class _TickPlan:
    slot1: Resolution
    slot1_start: str
    slot6: Resolution
    slot6_start: str
    active_resolution: Resolution
    active_physical: int
    next_boundary: datetime | None


class VirtualSlotScheduler:
    """Drives the six timer slots; resolves up to 10 virtual slots plus a
    live price-driven override from TariffChargingManager onto them.
    """

    def __init__(
        self,
        hass: HomeAssistant,
        coordinator: SunsynkCoordinator,
        entry_id: str,
        serial: str,
        tariff_manager: TariffChargingManager | None = None,
        normal_charge_current: int | None = None,
        normal_discharge_current: int | None = None,
        max_export_power: int | None = None,
    ) -> None:
        self._hass = hass
        self._coordinator = coordinator
        self._entry_id = entry_id
        self._serial = serial
        self._tariff_manager = tariff_manager
        self._normal_charge_current = normal_charge_current
        self._normal_discharge_current = normal_discharge_current
        self._max_export_power = max_export_power

        serial_key = sha256(serial.encode("utf-8")).hexdigest()[:16]
        self._store: Store = Store(
            hass,
            STORAGE_VERSION,
            f"{DOMAIN}_virtual_slots_{entry_id}_{serial_key}",
        )
        self._slots: dict[int, VirtualSlot] = {}

        self._enabled = False
        self._current_physical_slot = 1
        self._current_source = "none"
        self._next_boundary: datetime | None = None
        self._last_written: dict[int, tuple] = {}
        # Rewrites of a window the inverter doesn't report as written, per
        # timer slot, for the window last written there.
        self._rewrites: dict[int, int] = {}
        self._last_current_key: tuple[str, int] | _CurrentState | None = None
        self._original_settings: dict[str, Any] | None = None
        # Normal currents a shutdown could not write back yet; retried until
        # the inverter accepts them, also across restarts.
        self._pending_current_restore: dict[str, Any] | None = None
        # On but with nothing to do (no enabled window, no price override):
        # the user's own timer is in place, and slots are only taken over
        # when there is something to drive (#21).
        self._standing_by = False
        # The user's on/off choice, kept across restarts. Unload/reload stops
        # the scheduler without changing it.
        self._wanted_enabled = False
        self._operation_lock = Lock()

        self._listeners: list[Callable[[], None]] = []
        self._unsub_coordinator: Any = None
        self._unsub_tariff: Any = None
        self._unsub_boundary: Callable[[], None] | None = None

    # ── Listener registry ───────────────────────────────────────────────

    def async_add_listener(self, cb: Callable[[], None]) -> Callable[[], None]:
        self._listeners.append(cb)

        def _remove() -> None:
            self._listeners.remove(cb)

        return _remove

    def _notify_listeners(self) -> None:
        # One failing entity must not stop the others updating: a switch
        # whose state never gets written is flipped back by the frontend
        # after two seconds, which looks like it turning itself off (#21).
        for cb in list(self._listeners):
            try:
                cb()
            except Exception:
                _LOGGER.exception("Virtual slots: a listener failed to update")

    # ── Persistence ──────────────────────────────────────────────────────

    async def async_load(
        self, legacy_slots: list[dict[str, Any]] | None = None
    ) -> None:
        """Load this inverter's slots, optionally seeding old shared slots.

        Before schedules became per-inverter, one storage record applied to
        every inverter in the config entry.  On first load of the new key we
        copy that record so existing behaviour is preserved independently for
        every physical target.
        """
        data = await self._store.async_load()
        migrated = data is None and legacy_slots is not None
        if data is None:
            data = {"slots": legacy_slots or []}
        self._slots = {
            int(s["slot_id"]): VirtualSlot.from_dict(s) for s in data.get("slots", [])
        }
        self._wanted_enabled = bool(data.get("enabled", False))
        # Settings captured before taking ownership survive a restart, so a
        # later disable restores the user's timer, not the scheduler's own.
        self._original_settings = data.get("original_settings") or None
        self._pending_current_restore = data.get("pending_current_restore") or None
        if migrated:
            await self._async_persist()

    async def _async_persist(self) -> None:
        await self._store.async_save(
            {
                "slots": [s.to_dict() for s in self._slots.values()],
                "enabled": self._wanted_enabled,
                "original_settings": self._original_settings,
                "pending_current_restore": self._pending_current_restore,
            }
        )

    @property
    def wanted_enabled(self) -> bool:
        """Whether the user left the scheduler switched on."""
        return self._wanted_enabled

    # ── Lifecycle ────────────────────────────────────────────────────────

    def start(self) -> None:
        self._unsub_coordinator = self._coordinator.async_add_listener(self._on_tick)
        if self._tariff_manager is not None:
            self._unsub_tariff = self._tariff_manager.async_add_listener(self._on_tick)

    def stop(self) -> None:
        if self._unsub_coordinator:
            self._unsub_coordinator()
            self._unsub_coordinator = None
        if self._unsub_tariff:
            self._unsub_tariff()
            self._unsub_tariff = None
        self._cancel_boundary_timer()

    @callback
    def _on_tick(self) -> None:
        self._hass.async_create_task(self._async_tick())

    @callback
    def _on_boundary(self, _now: datetime) -> None:
        """Re-evaluate exactly when a virtual window starts or ends."""
        self._unsub_boundary = None
        self._on_tick()

    def _cancel_boundary_timer(self) -> None:
        if self._unsub_boundary is not None:
            self._unsub_boundary()
            self._unsub_boundary = None

    def _schedule_boundary_timer(self, boundary: datetime | None) -> None:
        """Keep exactly one callback for the scheduler's next transition."""
        self._cancel_boundary_timer()
        if boundary is not None and self._enabled:
            self._unsub_boundary = async_track_point_in_time(
                self._hass, self._on_boundary, boundary
            )

    # ── Enable / disable ─────────────────────────────────────────────────

    def set_enabled(self, enabled: bool) -> None:
        was_enabled = self._enabled
        self._enabled = enabled
        self._wanted_enabled = enabled
        self._hass.async_create_task(self._async_persist())
        if enabled:
            self._hass.async_create_task(self._async_bootstrap())
        else:
            self._hass.async_create_task(
                self._async_shutdown(force_disable=was_enabled)
            )
        self._notify_listeners()

    async def _async_bootstrap(self) -> None:
        """Start after the switch is turned on: capture the user's timer, then
        tick, which takes the slots over only if there is something to do."""
        async with self._operation_lock:
            # The enable task may still be queued when an unload/disable wins
            # the race. Never take ownership after shutdown has begun.
            if not self._enabled or not self._drives_inverter():
                return
            if self._original_settings is None:
                # Nothing has been written yet: the user's timer is in place.
                # If it can't be captured yet (no settings polled), the tick
                # keeps trying and drives nothing until it can.
                self._standing_by = True
                await self._async_capture_user_timer()
            else:
                # A snapshot from before a restart: the inverter may still
                # hold this scheduler's layout, so put the user's back first.
                self._standing_by = False

            self._last_written = {}
            self._last_current_key = None
            await self._async_tick_locked()

    async def _async_capture_user_timer(self) -> bool:
        """Capture the user's timer so it can always be put back.

        Never from a failed refresh, and never empty: without a capture the
        scheduler has no way back to the user's schedule, so it doesn't take
        the slots over at all (2.0 audit N3).
        """
        if getattr(self._coordinator, "last_update_success", True) is False:
            return False
        captured = self._current_managed_settings()
        if not captured:
            _LOGGER.warning(
                "Virtual slots [%s]: waiting for the inverter's timer settings "
                "before taking over",
                self._serial,
            )
            return False
        # Each model has only one of the two sell-flag names.
        missing = set(_MANAGED_SETTING_KEYS) - captured.keys() - _SELL_FLAG_KEYS
        if missing:
            _LOGGER.warning(
                "Virtual slots [%s]: cannot capture missing settings, they "
                "won't be put back: %s",
                self._serial,
                ", ".join(sorted(missing)),
            )
        self._original_settings = captured
        await self._async_persist()
        return True

    def _current_managed_settings(self) -> dict[str, Any]:
        settings = (
            (self._coordinator.data or {}).get(self._serial, {}).get("settings", {})
        )
        return {key: settings[key] for key in _MANAGED_SETTING_KEYS if key in settings}

    def _has_work(self) -> bool:
        """Whether there is an enabled window or a live price override."""
        return (
            any(slot.enabled and slot.weekdays for slot in self._slots.values())
            or self._resolve_override() is not None
        )

    async def _async_stand_by(self) -> bool:
        """Leave the user's own timer in place while there is nothing to do.

        Puts the settings captured before taking ownership back once, e.g.
        when a price override ends with no window defined, so Tariff Manager
        can be used without leaving the user's schedule changed. While
        standing by, the captured settings follow the inverter, so a change
        made in the Sunsynk app is what comes back after the next override.
        """
        if self._standing_by:
            if getattr(self._coordinator, "last_update_success", True) is not False:
                current = self._current_managed_settings()
                if current and current != self._original_settings:
                    self._original_settings = current
                    await self._async_persist()
            return False
        try:
            if self._original_settings:
                await self._coordinator.async_write_settings(
                    self._serial, self._original_settings
                )
            if isinstance(self._last_current_key, tuple):
                # A window's own current is still on the inverter (the window
                # was removed or disabled): put the normal currents back too.
                await self._apply_current_if_changed(_IDLE)
        except Exception as err:  # noqa: BLE001
            _LOGGER.error(
                "Virtual slots [%s]: could not put your own timer back, "
                "will retry on the next update: %s",
                self._serial,
                err,
            )
            return False
        self._standing_by = True
        self._last_written = {}
        self._last_current_key = None
        _LOGGER.info(
            "Virtual slots [%s]: nothing to do, your own timer is back in place",
            self._serial,
        )
        return True

    async def _async_take_over(self) -> None:
        """Start driving the timer slots."""
        _LOGGER.info(
            "Virtual slots [%s]: taking over the timer slots (%s)",
            self._serial,
            "price override" if self._resolve_override() else "virtual slot",
        )
        # A capture from before 2.0.0-beta.16 only covered slots 1, 2 and 6.
        # Add the rest from the inverter before they are first written, while
        # they still hold the user's own values (#36).
        current = self._current_managed_settings()
        missing = {
            key: value
            for key, value in current.items()
            if key not in (self._original_settings or {})
        }
        if missing and self._original_settings is not None:
            self._original_settings = {**self._original_settings, **missing}
            await self._async_persist()
        self._standing_by = False
        self._last_written = {}
        self._last_current_key = None

    async def _async_shutdown(self, force_disable: bool = True) -> bool:
        """Restore the exact settings captured before taking ownership."""
        success = True
        async with self._operation_lock:
            # Standing by, the user's own timer and the normal currents are
            # already in place: nothing to put back (2.0 audit N4).
            owns = not self._standing_by
            had_ownership = (
                (self._original_settings is not None and owns)
                or self._pending_current_restore is not None
                or (force_disable and owns)
            )
            pending: dict[str, Any] = {}
            pending_currents: dict[str, Any] = {}
            if self._original_settings and owns:
                try:
                    await self._coordinator.async_write_settings(
                        self._serial, self._original_settings
                    )
                except Exception as err:  # noqa: BLE001
                    success = False
                    pending.update(self._original_settings)
                    _LOGGER.error(
                        "Virtual slots shutdown [%s]: could not restore slot settings: %s",
                        self._serial,
                        err,
                    )
            elif force_disable and owns and self._drives_inverter():
                try:
                    await self._coordinator.async_write_settings(
                        self._serial,
                        {key: 0 for key in _GRID_CHARGE_KEYS},
                    )
                except Exception as err:  # noqa: BLE001
                    success = False
                    _LOGGER.error(
                        "Virtual slots shutdown [%s]: could not disable owned slots: %s",
                        self._serial,
                        err,
                    )

            if had_ownership:
                normal_charge_current = self._normal_charge_current
                normal_discharge_current = self._normal_discharge_current
                if self._tariff_manager is not None:
                    normal_charge_current = self._tariff_manager.normal_charge_current
                    normal_discharge_current = (
                        self._tariff_manager.normal_discharge_current
                    )
                current_restores = dict(self._pending_current_restore or {})
                current_restores |= {
                    key: value
                    for key, value in (
                        ("chargeCurrent", normal_charge_current),
                        ("dischargeCurrent", normal_discharge_current),
                    )
                    if value is not None
                }
                if current_restores:
                    try:
                        await self._coordinator.async_write_settings(
                            self._serial,
                            {
                                key: self._safe(key, value)
                                for key, value in current_restores.items()
                            },
                        )
                    except Exception as err:  # noqa: BLE001
                        success = False
                        pending_currents = current_restores
                        _LOGGER.error(
                            "Virtual slots shutdown [%s]: could not restore currents: %s",
                            self._serial,
                            err,
                        )

            self._original_settings = pending or None
            self._pending_current_restore = pending_currents or None
            self._standing_by = False
            self._last_written = {}
            self._last_current_key = None
            await self._async_persist()

        self._notify_listeners()
        return success

    async def async_shutdown(self) -> bool:
        """Stop callbacks and restore settings before unload/reload."""
        self.stop()
        was_enabled = self._enabled
        self._enabled = False
        return await self._async_shutdown(force_disable=was_enabled)

    # ── Slot management (called by services) ───────────────────────────

    async def async_set_slot(self, slot: VirtualSlot) -> None:
        if not (1 <= slot.slot_id <= MAX_VIRTUAL_SLOTS):
            raise ValueError(f"slot_id must be 1-{MAX_VIRTUAL_SLOTS}")
        self._slots[slot.slot_id] = slot
        await self._async_persist()
        self._notify_listeners()
        if self._enabled:
            await self._async_tick()

    async def async_clear_slot(self, slot_id: int) -> None:
        self._slots.pop(slot_id, None)
        await self._async_persist()
        self._notify_listeners()
        if self._enabled:
            await self._async_tick()

    def list_slots(self) -> list[dict[str, Any]]:
        return [
            s.to_dict() for s in sorted(self._slots.values(), key=lambda s: s.slot_id)
        ]

    # ── Resolution ───────────────────────────────────────────────────────

    def _resolve_virtual(
        self, at: datetime
    ) -> tuple[Resolution, datetime | None, datetime | None]:
        """Resolve the virtual (time-based) schedule at `at`. Ignores price override.

        Returns (resolution, window_start, next_boundary):
        - window_start: start of the currently active window, None if idle.
        - next_boundary: earliest datetime > `at` at which the resolution
          could change, or None if nothing is scheduled at all.
        """
        candidates: list[tuple[VirtualSlot, datetime, datetime]] = []
        for slot in self._slots.values():
            window = slot.window_containing(at)
            if window is not None:
                candidates.append((slot, window[0], window[1]))

        if not candidates:
            chosen = None
        else:
            candidates.sort(
                key=lambda c: (-c[0].priority, c[0].duration_minutes, c[0].slot_id)
            )
            chosen = candidates[0]

        boundaries: list[datetime] = []
        for slot in self._slots.values():
            # A slot with no weekdays never starts (2.0 audit N5).
            if not slot.enabled or not slot.weekdays:
                continue
            if chosen is not None and slot.slot_id == chosen[0].slot_id:
                boundaries.append(chosen[2])  # this slot's own window end
            else:
                boundaries.append(slot.next_start_after(at))
        next_boundary = min(boundaries) if boundaries else None

        if chosen is None:
            return _IDLE, None, next_boundary

        slot = chosen[0]
        resolution = Resolution(
            mode=slot.mode,
            current=slot.current,
            target_soc=slot.target_soc,
            sell_power=slot.sell_power,
            source=f"virtual_slot:{slot.slot_id}",
        )
        return resolution, chosen[1], next_boundary

    def _last_window_end(self, at: datetime) -> datetime | None:
        """End of the latest window that ended at or before `at`."""
        ends = []
        for slot in self._slots.values():
            if not slot.enabled or not slot.weekdays:
                continue
            hh, mm = _parse_hhmm(slot.start)
            start = max(_most_recent_occurrence(at, wd, hh, mm) for wd in slot.weekdays)
            end = start + timedelta(minutes=slot.duration_minutes)
            if end <= at:
                ends.append(end)
        return max(ends, default=None)

    def _resolve_override(self) -> Resolution | None:
        """Live price-driven decision from the Tariff Manager, if any."""
        tm = self._tariff_manager
        # In test mode Tariff Manager only shows what it would do.
        if tm is None or getattr(tm, "test_mode", False) is True:
            return None
        if tm.is_charging_active_for(self._serial):
            return Resolution(
                mode=MODE_CHARGE,
                current=None,  # tariff manager owns chargeCurrent
                target_soc=tm.target_soc,
                sell_power=0,
                source="price_override",
            )
        if tm.is_discharging_active_for(self._serial):
            return Resolution(
                mode=MODE_DISCHARGE,
                current=None,  # tariff manager owns dischargeCurrent
                target_soc=tm.discharge_min_soc,
                sell_power=self._export_share() or self._uncapped_sell_power(),
                source="price_override",
            )
        return None

    _DEFAULT_UNCAPPED_SELL_POWER = 30000

    def _uncapped_sell_power(self) -> int:
        """Best-effort "no artificial cap" sell power for a price-override
        discharge.

        Used when no maximum export power is configured (see
        `_export_share`). Tariff Manager has no sell power of its own —
        unlike a virtual slot's `sell_power` field, which the user
        sets explicitly. Previously this resolved to a hardcoded 0, which
        wrote `sellTime{n}Pac` = 0 W onto the active physical slot — a
        price-driven discharge would correctly raise `dischargeCurrent`
        and still export nothing, since the slot's own power cap silently
        overrode it (#21). Uses the inverter's own rated power
        (`ratePower`, already polled every refresh) so the only thing
        actually limiting export during a price override is Tariff
        Manager's own `dischargeCurrent`, not a forgotten cap. Falls back
        to a generous default if rated power isn't known yet (e.g. right
        after startup, before the first successful poll).
        """
        return self._rated_power() or self._DEFAULT_UNCAPPED_SELL_POWER

    def _rated_power(self) -> int | None:
        """The inverter's rated power in W, None until it is known."""
        inverter_info = (
            (self._coordinator.data or {}).get(self._serial, {}).get("inverter", {})
        )
        try:
            rate = int(inverter_info.get("ratePower") or 0)
        except (TypeError, ValueError):
            return None
        return rate if rate > 0 else None

    def _export_share(self) -> int | None:
        """This inverter's share of the configured maximum export, in W.

        Each inverter in a parallel group applies its slot's sell power on its
        own, so the system exports that times the number of inverters: an
        11 kW limit is 5500 W each on a pair (#21). None if no limit is set.
        """
        if not self._max_export_power:
            return None
        inverters = max(1, group_sizes(self._coordinator).get(self._serial, 1))
        return max(1, int(self._max_export_power) // inverters)

    def sell_power_limit(self) -> tuple[int | None, str]:
        """Most a discharge window may sell on this inverter, in W, and why.

        The lower of the inverter's rated power and its share of the maximum
        export power. (None, "") while neither is known.
        """
        rated = self._rated_power()
        share = self._export_share()
        parts = []
        if rated is not None:
            parts.append(f"inverter rated {rated} W")
        if share is not None:
            inverters = max(1, group_sizes(self._coordinator).get(self._serial, 1))
            parts.append(
                f"maximum export {self._max_export_power} W"
                + (
                    f" shared by {inverters} inverters = {share} W each"
                    if inverters > 1
                    else ""
                )
            )
        known = [value for value in (rated, share) if value is not None]
        return (min(known) if known else None), ", ".join(parts)

    def export_summary(self) -> dict[str, Any]:
        """The export limit and the inverter's own totals for this group.

        Per-inverter values times the number of inverters, so a parallel
        system shows what it can put on the grid as a whole: e.g. a Max Sell
        Power of 12000 W on each of two inverters is 24000 W (#21).
        """
        inverters = max(1, group_sizes(self._coordinator).get(self._serial, 1))
        settings = (
            (self._coordinator.data or {}).get(self._serial, {}).get("settings", {})
        )
        try:
            max_sell = int(settings.get("solarMaxSellPower") or 0) or None
        except (TypeError, ValueError):
            max_sell = None
        rated = self._rated_power()
        limit = int(self._max_export_power) if self._max_export_power else None
        max_sell_total = max_sell * inverters if max_sell else None
        return {
            "limit_total": limit,
            "inverters": inverters,
            "limit_per_inverter": self._export_share(),
            "rated_power_per_inverter": rated,
            "rated_power_total": rated * inverters if rated else None,
            "max_sell_power_per_inverter": max_sell,
            "max_sell_power_total": max_sell_total,
            # Max Sell Power also governs solar export, which this limit
            # doesn't touch: flag an inverter setting that allows more.
            "max_sell_power_above_limit": (
                limit is not None
                and max_sell_total is not None
                and max_sell_total > limit
            ),
        }

    _DEFAULT_SOC_FLOOR = 20

    def _soc_floor(self) -> int:
        """Lowest `cap{n}` this scheduler may write: the inverter's own
        Battery Low Capacity.

        A window without a target SOC used to be written as `cap{n}` = 0
        (#21). The inverter should still stop at its own low capacity, but a
        slot value must not rely on that. Falls back to a conservative
        default until settings have been polled.
        """
        settings = (
            (self._coordinator.data or {}).get(self._serial, {}).get("settings", {})
        )
        try:
            return max(0, min(100, int(settings["batteryLowCap"])))
        except (KeyError, TypeError, ValueError):
            return self._DEFAULT_SOC_FLOOR

    def _plan(self, now: datetime) -> _TickPlan:
        """Decide what belongs on physical slot 1 vs slot 6.

        Sunsynk requires slot 1's start time-of-day to be <= slot 6's, and
        only slot 6 may wrap past midnight into slot 1 — so whichever of
        (currently-active, next-scheduled) has the earlier time-of-day
        always goes on slot 1, regardless of which one is active right now.
        """
        virt_resolution, window_start, next_boundary = self._resolve_virtual(now)
        override = self._resolve_override()

        active_resolution = override if override is not None else virt_resolution

        # The timers repeat daily, so a boundary more than a day away can't be
        # armed yet: its time of day comes round before it does, and would
        # start that window early. Until it is within a day, both slots hold
        # whatever is in force now.
        armable = (
            next_boundary is not None
            and next_boundary - now <= timedelta(days=1) - _ARM_MARGIN
        )
        if window_start is not None:
            active_start = window_start
        elif armable:
            # Idle since the last window ended. The physical slot runs from
            # its start to the upcoming one's, so that start must fall within
            # the day before the upcoming boundary, or "now" lands in the
            # upcoming slot's range instead (#21 audit R1).
            active_start = max(
                self._last_window_end(now) or datetime.min.replace(tzinfo=now.tzinfo),
                next_boundary - timedelta(days=1) + _ARM_MARGIN,
            )
        else:
            active_start = None
        active_hhmm = (
            active_start.strftime("%H:%M") if active_start is not None else "00:00"
        )

        if armable:
            upcoming_resolution, _, _ = self._resolve_virtual(next_boundary)
            upcoming_hhmm = next_boundary.strftime("%H:%M")
        else:
            upcoming_resolution = active_resolution
            upcoming_hhmm = "00:00"

        if active_hhmm <= upcoming_hhmm:
            slot1, slot1_start = active_resolution, active_hhmm
            slot6, slot6_start = upcoming_resolution, upcoming_hhmm
            active_physical = 1
        else:
            slot1, slot1_start = upcoming_resolution, upcoming_hhmm
            slot6, slot6_start = active_resolution, active_hhmm
            active_physical = 6

        return _TickPlan(
            slot1=slot1,
            slot1_start=slot1_start,
            slot6=slot6,
            slot6_start=slot6_start,
            active_resolution=active_resolution,
            active_physical=active_physical,
            next_boundary=next_boundary,
        )

    def _now(self) -> datetime:
        return dt_util.now()

    # ── Tick ─────────────────────────────────────────────────────────────

    async def _async_tick(self) -> None:
        if (
            not self._enabled
            and not self._wanted_enabled
            and (self._original_settings or self._pending_current_restore)
        ):
            # Switched off, but a restore failed: keep retrying it on every
            # update until the inverter has the user's settings back.
            await self._async_shutdown(force_disable=False)
            return
        async with self._operation_lock:
            await self._async_tick_locked()

    def _drives_inverter(self) -> bool:
        """Whether this scheduler owns its inverter's writes right now.

        A scheduler created for a parallel slave before the group was
        recognised must not drive the master's timer slots alongside the
        master's own scheduler (#21).
        """
        target = self._coordinator.resolve_write_target(self._serial)
        if target == self._serial:
            return True
        _LOGGER.warning(
            "Virtual slots [%s]: not driving the inverter, %s owns its writes",
            self._serial,
            target,
        )
        return False

    async def _async_tick_locked(self) -> None:
        if not self._enabled:
            return
        if not self._drives_inverter():
            return
        if self._tariff_manager is not None and self._tariff_manager.is_busy:
            # Tariff Manager is part-way through a change, e.g. idle between
            # stopping a charge and starting a discharge in the same second.
            # Acting on that put the user's timer back and took the slots over
            # again straight after, and the two writes crossed: the discharge
            # slots ended up without Sell ticked (#36). It notifies once done.
            return

        if self._original_settings is None and not (
            await self._async_capture_user_timer()
        ):
            return
        if not self._has_work():
            if await self._async_stand_by():
                self._current_source = "none"
                self._next_boundary = None
                self._cancel_boundary_timer()
                self._notify_listeners()
            return
        if self._standing_by:
            await self._async_take_over()

        now = self._now()
        plan = self._plan(now)

        # Slot 1 holds the earlier boundary. Slots 2-6 all hold the later one,
        # with slot 6's start, so slot 1 ends there and whichever of them the
        # inverter treats as in force, it does the same thing (#36; slot 2's
        # start bounding slot 1, #21). Started together, these land in the
        # coordinator's queue in the same turn and go out as one System Mode
        # request, which keeps write volume down on parallel systems (#21).
        wrote = await asyncio.gather(
            self._write_window_if_changed(1, plan.slot1, plan.slot1_start),
            *(
                self._write_window_if_changed(n, plan.slot6, plan.slot6_start)
                for n in range(2, 7)
            ),
        )
        wrote_current = await self._apply_current_if_changed(plan.active_resolution)

        changed = any(wrote) or wrote_current
        if plan.active_resolution.source != self._current_source:
            _LOGGER.info(
                "Virtual slots [%s]: now %s (%s) on timer slot %s, next change %s",
                self._serial,
                plan.active_resolution.source,
                plan.active_resolution.mode,
                plan.active_physical,
                plan.next_boundary.isoformat() if plan.next_boundary else "none",
            )
            self._current_source = plan.active_resolution.source
            changed = True
        if plan.active_physical != self._current_physical_slot:
            self._current_physical_slot = plan.active_physical
            changed = True
        boundary_changed = plan.next_boundary != self._next_boundary
        if boundary_changed:
            self._next_boundary = plan.next_boundary
            changed = True
        if boundary_changed or (
            plan.next_boundary is not None and self._unsub_boundary is None
        ):
            self._schedule_boundary_timer(plan.next_boundary)

        if changed:
            self._notify_listeners()

    async def _write_window_if_changed(
        self, index: int, resolution: Resolution, start: str
    ) -> bool:
        """Write time{n}on / cap{n} / sellTime{n}Pac / sellTime{n} (start) / sellTime{n}on (sell permission).

        The per-slot "Sell" permission checkbox is `sellTime{n}on`, not
        `sellTime{n}En` as originally guessed from naming convention alone
        — confirmed by a reporter (#21) inspecting their own raw settings
        dump, where `sellTime{n}En` doesn't appear at all but
        `sellTime3on` was `true` for the one slot they had manually ticked
        "Sell" on via the inverter screen. Without this field set, a slot
        can have `on`, `cap` and `sellTime{n}Pac` all correctly set and
        still never actually export anything while System Work Mode is
        Zero-Export/Limited to Home.
        """
        # Grid Charge only for a charge window: a discharge or idle slot with
        # it ticked would top the battery up from the grid (#36).
        on = resolution.mode == MODE_CHARGE
        floor = self._soc_floor()
        cap = max(resolution.target_soc or 0, floor)
        # With Use Timer on and Sell unticked, a slot's power is also the
        # most the inverter may draw from the battery for the house load.
        # 0 W pushed the whole house onto the grid while VSS was holding an
        # idle window (#21), so only a discharge window carries a real cap.
        pac = (
            resolution.sell_power
            if resolution.mode == MODE_DISCHARGE
            else self._uncapped_sell_power()
        )
        rated = self._rated_power()
        if resolution.mode == MODE_DISCHARGE and rated is not None and pac > rated:
            # More than the inverter can deliver means nothing, and would
            # hide what the slot really does.
            _LOGGER.warning(
                "Virtual slots [%s]: lowering sell power from %s W to the "
                "inverter's rated %s W",
                self._serial,
                pac,
                rated,
            )
            pac = rated
        share = self._export_share()
        if resolution.mode == MODE_DISCHARGE and share is not None and pac > share:
            # Only a selling slot exports. An idle or charge slot's power is
            # the battery's cap for the house, so it is left uncapped.
            _LOGGER.warning(
                "Virtual slots [%s]: lowering sell power from %s W to %s W, "
                "this inverter's share of the %s W maximum export",
                self._serial,
                pac,
                share,
                self._max_export_power,
            )
            pac = share
        sell_en = resolution.mode == MODE_DISCHARGE
        cache_key = (on, cap, pac, start, sell_en)

        keys = _PHYSICAL_KEYS[index]
        window = {
            keys["on"]: 1 if on else 0,
            keys["cap"]: cap,
            keys["pac"]: pac,
            keys["start"]: start,
        }
        # The per-slot sell flag is sellTime{n}on on some Sunsynk models and
        # sellTime{n}En on Deye ones; write whichever this inverter has (#33).
        settings = (
            (self._coordinator.data or {}).get(self._serial, {}).get("settings", {})
        )
        sell_key = next(
            (key for key in (keys["en"], keys["en_alt"]) if key in settings), None
        )
        if sell_key is not None:
            window[sell_key] = 1 if sell_en else 0

        if self._last_written.get(index) == cache_key:
            # Written before, but check the inverter has it: a write can be
            # lost, e.g. one crossing another, and the slot would then stay
            # wrong until the window changed (#36). Until the inverter
            # confirms a write, the coordinator reports the value sent, so
            # this only acts on what the inverter actually reports.
            differs = [
                key
                for key, value in window.items()
                if not SunsynkCoordinator._values_match(value, settings.get(key))
            ]
            if not differs:
                self._rewrites.pop(index, None)
                return False
            attempts = self._rewrites.get(index, 0)
            if attempts >= _MAX_REWRITES:
                if attempts == _MAX_REWRITES:
                    _LOGGER.warning(
                        "Virtual slots [%s]: timer slot %s still reports %s "
                        "after %s rewrites, giving up until the window changes",
                        self._serial,
                        index,
                        {key: settings.get(key) for key in differs},
                        _MAX_REWRITES,
                    )
                    self._rewrites[index] = attempts + 1
                return False
            self._rewrites[index] = attempts + 1
            _LOGGER.warning(
                "Virtual slots [%s]: timer slot %s reports %s instead of %s, "
                "writing it again",
                self._serial,
                index,
                {key: settings.get(key) for key in differs},
                {key: window[key] for key in differs},
            )
        else:
            self._rewrites.pop(index, None)

        await self._coordinator.async_write_settings(self._serial, window)

        self._last_written[index] = cache_key
        return True

    async def _apply_current_if_changed(self, resolution: Resolution) -> bool:
        """Write chargeCurrent/dischargeCurrent for whatever is in force
        right now. Skipped entirely while a price override is active —
        TariffChargingManager already owns those registers in that case.
        """
        if resolution.source == "price_override":
            # The Tariff Manager now owns the currents and leaves its own
            # values behind, so whatever this scheduler last wrote is gone.
            self._last_current_key = None
            return False

        if resolution.mode == MODE_CHARGE and resolution.current is not None:
            key, value = "chargeCurrent", resolution.current
        elif resolution.mode == MODE_DISCHARGE and resolution.current is not None:
            key, value = "dischargeCurrent", resolution.current
        elif resolution.mode == MODE_IDLE:
            # Best-effort restore; nothing to do if no normal current configured.
            wrote = False
            if self._last_current_key is _CurrentState.IDLE:
                return False
            if self._normal_charge_current is not None:
                await self._coordinator.async_write_setting(
                    self._serial,
                    "chargeCurrent",
                    self._safe("chargeCurrent", self._normal_charge_current),
                )
                wrote = True
            if self._normal_discharge_current is not None:
                await self._coordinator.async_write_setting(
                    self._serial,
                    "dischargeCurrent",
                    self._safe("dischargeCurrent", self._normal_discharge_current),
                )
                wrote = True
            self._last_current_key = _CurrentState.IDLE
            return wrote
        else:
            return False

        current_key = (key, value)
        if current_key == self._last_current_key:
            return False
        await self._coordinator.async_write_setting(
            self._serial, key, self._safe(key, value)
        )
        self._last_current_key = current_key
        return True

    def _safe(self, key: str, amps: int) -> int:
        """`amps`, lowered if the parallel group would exceed the battery BMS."""
        direction = "charge" if key == "chargeCurrent" else "discharge"
        return battery_safe_current(self._coordinator, self._serial, direction, amps)

    # ── Properties ───────────────────────────────────────────────────────

    @property
    def is_enabled(self) -> bool:
        return self._enabled

    @property
    def serial(self) -> str:
        return self._serial

    @property
    def active_source(self) -> str:
        return self._current_source

    @property
    def current_physical_slot(self) -> int:
        return self._current_physical_slot

    @property
    def next_boundary(self) -> datetime | None:
        return self._next_boundary

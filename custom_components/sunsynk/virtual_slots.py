"""Up-to-10 HA-side virtual charge/discharge slots, resolved onto 2 owned
physical Sunsynk time slots (1 and 6).

Background
----------
Per Sunsynk's own documentation ("Avoiding Conflicts in the System Mode
Timer"), the 6 System Mode Timer slots MUST be set chronologically —
each slot's start time must be later than the previous one — and *only*
Timer 6 is allowed to roll across midnight into Timer 1. No other pair of
slots can wrap, and setting a lower-numbered slot to a later time-of-day
than a higher-numbered slot is exactly the "conflict" that article warns
about: the inverter will not reliably act on the out-of-order slot.

This module takes exclusive ownership of physical slots **1 and 6** (2-5
are disabled; slot 2's start is pinned as slot 1's boundary) — the only
pair the firmware actually supports wrapping past midnight — and enforces
the invariant that slot 1 always holds the earlier time-of-day boundary and
slot 6 the later one,
recomputed fresh on every tick. Which one is "currently active" flips
between them as the day progresses (e.g. slot 1 governs the daytime arc,
slot 6 governs the overnight arc that wraps into the next day), but their
relative ordering by time-of-day never does.

Price-driven decisions from TariffChargingManager do not need a
breakpoint of their own — they always take priority and are applied by
live-patching whichever of slot 1 / slot 6 is currently active (cap /
sell power / on), without touching its start time or the other slot.
"""

from __future__ import annotations

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

if TYPE_CHECKING:
    from .tariff import TariffChargingManager

_LOGGER = logging.getLogger(__name__)

STORAGE_VERSION = 1

MAX_VIRTUAL_SLOTS = 10
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

# setting_key names for each owned physical slot
_PHYSICAL_KEYS: dict[int, dict[str, str]] = {
    1: {
        "on": "time1on",
        "cap": "cap1",
        "pac": "sellTime1Pac",
        "start": "sellTime1",
        "en": "sellTime1on",
    },
    6: {
        "on": "time6on",
        "cap": "cap6",
        "pac": "sellTime6Pac",
        "start": "sellTime6",
        "en": "sellTime6on",
    },
}
# Slots 2-5 are turned off once when the scheduler takes ownership. Slot 2's
# start is also maintained as slot 1's end boundary; slots 3-5 stay untouched.
_UNUSED_SLOT_ON_KEYS = ("time2on", "time3on", "time4on", "time5on")

# Restore slot data fields before enable flags so the inverter never briefly
# activates a partially restored window. Currents return to configured normal
# values separately; snapshotting a live tariff override would be unsafe.
_MANAGED_SETTING_KEYS = (
    "cap1",
    "sellTime1Pac",
    "sellTime1",
    "sellTime1on",
    "sellTime2",
    "cap6",
    "sellTime6Pac",
    "sellTime6",
    "sellTime6on",
    "time1on",
    "time2on",
    "time3on",
    "time4on",
    "time5on",
    "time6on",
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
    """Owns physical slots 1 & 6; resolves up to 10 virtual slots plus a
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
    ) -> None:
        self._hass = hass
        self._coordinator = coordinator
        self._entry_id = entry_id
        self._serial = serial
        self._tariff_manager = tariff_manager
        self._normal_charge_current = normal_charge_current
        self._normal_discharge_current = normal_discharge_current

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
        self._last_current_key: tuple[str, int] | _CurrentState | None = None
        self._last_written_slot2_boundary: str | None = None
        self._original_settings: dict[str, Any] | None = None
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
        for cb in self._listeners:
            cb()

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
        if migrated:
            await self._async_persist()

    async def _async_persist(self) -> None:
        await self._store.async_save(
            {"slots": [s.to_dict() for s in self._slots.values()]}
        )

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
        if enabled:
            self._hass.async_create_task(self._async_bootstrap())
        else:
            self._hass.async_create_task(
                self._async_shutdown(force_disable=was_enabled)
            )
        self._notify_listeners()

    async def _async_bootstrap(self) -> None:
        """Take ownership: disable slots 2-5, then compute 1 & 6 from scratch."""
        async with self._operation_lock:
            # The enable task may still be queued when an unload/disable wins
            # the race. Never take ownership after shutdown has begun.
            if not self._enabled:
                return
            if self._original_settings is None:
                settings = (
                    (self._coordinator.data or {})
                    .get(self._serial, {})
                    .get("settings", {})
                )
                self._original_settings = {
                    key: settings[key]
                    for key in _MANAGED_SETTING_KEYS
                    if key in settings
                }
                missing = set(_MANAGED_SETTING_KEYS) - self._original_settings.keys()
                if missing:
                    _LOGGER.warning(
                        "Virtual slots [%s]: cannot snapshot missing settings: %s",
                        self._serial,
                        ", ".join(sorted(missing)),
                    )

            await self._coordinator.async_write_settings(
                self._serial,
                {key: 0 for key in _UNUSED_SLOT_ON_KEYS},
            )
            self._last_written = {}
            self._last_current_key = None
            self._last_written_slot2_boundary = None
            await self._async_tick_locked()

    async def _async_shutdown(self, force_disable: bool = True) -> bool:
        """Restore the exact settings captured before taking ownership."""
        success = True
        async with self._operation_lock:
            had_ownership = self._original_settings is not None or force_disable
            pending: dict[str, Any] = {}
            if self._original_settings:
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
            elif force_disable:
                try:
                    await self._coordinator.async_write_settings(
                        self._serial,
                        {
                            _PHYSICAL_KEYS[1]["on"]: 0,
                            _PHYSICAL_KEYS[6]["on"]: 0,
                        },
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
                current_restores = {
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
                            self._serial, current_restores
                        )
                    except Exception as err:  # noqa: BLE001
                        success = False
                        _LOGGER.error(
                            "Virtual slots shutdown [%s]: could not restore currents: %s",
                            self._serial,
                            err,
                        )

            self._original_settings = pending or None
            self._last_written = {}
            self._last_current_key = None
            self._last_written_slot2_boundary = None

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
            if not slot.enabled:
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

    def _resolve_override(self) -> Resolution | None:
        """Live price-driven decision from the Tariff Manager, if any."""
        tm = self._tariff_manager
        if tm is None:
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
                sell_power=self._uncapped_sell_power(),
                source="price_override",
            )
        return None

    _DEFAULT_UNCAPPED_SELL_POWER = 30000

    def _uncapped_sell_power(self) -> int:
        """Best-effort "no artificial cap" sell power for a price-override
        discharge.

        Tariff Manager has no config option of its own for max export
        power — unlike a virtual slot's `sell_power` field, which the user
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
        inverter_info = (
            (self._coordinator.data or {}).get(self._serial, {}).get("inverter", {})
        )
        rate = inverter_info.get("ratePower")
        try:
            if rate:
                return int(rate)
        except (TypeError, ValueError):
            pass
        return self._DEFAULT_UNCAPPED_SELL_POWER

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
        active_hhmm = (
            window_start.strftime("%H:%M") if window_start is not None else "00:00"
        )

        if next_boundary is not None:
            upcoming_resolution, _, _ = self._resolve_virtual(next_boundary)
            upcoming_hhmm = next_boundary.strftime("%H:%M")
        else:
            upcoming_resolution = _IDLE
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
        async with self._operation_lock:
            await self._async_tick_locked()

    async def _async_tick_locked(self) -> None:
        if not self._enabled:
            return

        now = self._now()
        plan = self._plan(now)

        wrote1 = await self._write_window_if_changed(1, plan.slot1, plan.slot1_start)
        wrote6 = await self._write_window_if_changed(6, plan.slot6, plan.slot6_start)
        wrote_boundary = await self._write_slot2_boundary_if_changed(plan.slot6_start)
        wrote_current = await self._apply_current_if_changed(plan.active_resolution)

        changed = wrote1 or wrote6 or wrote_boundary or wrote_current
        if plan.active_resolution.source != self._current_source:
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
        on = resolution.mode != MODE_IDLE
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
        sell_en = resolution.mode == MODE_DISCHARGE
        cache_key = (on, cap, pac, start, sell_en)
        if self._last_written.get(index) == cache_key:
            return False

        keys = _PHYSICAL_KEYS[index]
        await self._coordinator.async_write_settings(
            self._serial,
            {
                keys["on"]: 1 if on else 0,
                keys["cap"]: cap,
                keys["pac"]: pac,
                keys["start"]: start,
                keys["en"]: 1 if sell_en else 0,
            },
        )

        self._last_written[index] = cache_key
        return True

    async def _write_slot2_boundary_if_changed(self, slot6_start: str) -> bool:
        """Keep physical slot 2's start time pinned to slot 6's start.

        Slot 1's *end* isn't a field this scheduler writes anywhere — the
        inverter derives it from the next physical slot's own start time.
        Slot 2 stays permanently disabled (`time2on=0`, see
        `_async_bootstrap`), but a reporter found its *leftover* start time
        from before VSS took ownership still silently bounded slot 1's end
        on the inverter screen, even while disabled — so a virtual slot
        assigned to physical slot 1 could get truncated (or extended) back
        to whatever slot 2 happened to be set to previously, once the
        inverter re-validated the full System Mode Timer a short time
        after activation. `slot1_start <= slot6_start` always holds by
        construction (slot 1 is defined as whichever boundary has the
        earlier time-of-day), so pinning slot 2's start to slot 6's start
        is always a validly-ordered value and gives slot 1 a real,
        controlled end for the first time (#21).
        """
        if self._last_written_slot2_boundary == slot6_start:
            return False

        await self._coordinator.async_write_setting(
            self._serial, "sellTime2", slot6_start
        )

        self._last_written_slot2_boundary = slot6_start
        return True

    async def _apply_current_if_changed(self, resolution: Resolution) -> bool:
        """Write chargeCurrent/dischargeCurrent for whatever is in force
        right now. Skipped entirely while a price override is active —
        TariffChargingManager already owns those registers in that case.
        """
        if resolution.source == "price_override":
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
                    self._serial, "chargeCurrent", self._normal_charge_current
                )
                wrote = True
            if self._normal_discharge_current is not None:
                await self._coordinator.async_write_setting(
                    self._serial,
                    "dischargeCurrent",
                    self._normal_discharge_current,
                )
                wrote = True
            self._last_current_key = _CurrentState.IDLE
            return wrote
        else:
            return False

        current_key = (key, value)
        if current_key == self._last_current_key:
            return False
        await self._coordinator.async_write_setting(self._serial, key, value)
        self._last_current_key = current_key
        return True

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

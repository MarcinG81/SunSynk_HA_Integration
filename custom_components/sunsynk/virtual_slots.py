"""Up-to-10 HA-side virtual charge/discharge slots, resolved onto 2 owned
physical Sunsynk time slots (1 and 2).

Background
----------
Sunsynk/Deye ToU slots do not have an independent end time — a slot runs
from its own start time until whichever *other* slot has the next start
time (slot 6 wraps to slot 1 the following day). That makes it impossible
to freely toggle a single physical slot on/off at an arbitrary moment
without disturbing the whole day's ordering if other slots are also user
managed.

This module sidesteps that by taking exclusive ownership of physical
slots 1 and 2 (3-6 are turned off and left untouched) and treating them as
a rolling pair:

- One physical slot is always "current" — its start time is <= now and it
  holds whatever should be in force right now.
- The other is "next" — pre-armed with the next known time-based
  transition from the virtual schedule.
- When "now" reaches the armed slot's start time, the roles swap and a
  fresh transition is armed into the newly-inactive slot.

Price-driven decisions from TariffChargingManager do not need a breakpoint
of their own — they always take priority and are applied by live-patching
whichever physical slot is currently "current" (cap / sell power / on),
without touching its start time.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Callable, TYPE_CHECKING

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import DOMAIN
from .coordinator import SunsynkCoordinator

if TYPE_CHECKING:
    from .tariff import TariffChargingManager

_LOGGER = logging.getLogger(__name__)

STORAGE_VERSION = 1

MAX_VIRTUAL_SLOTS = 10
PHYSICAL_SLOTS = (1, 2)
MODE_CHARGE = "charge"
MODE_DISCHARGE = "discharge"
MODE_IDLE = "idle"

WEEKDAY_NAMES = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
ALL_WEEKDAYS: frozenset[int] = frozenset(range(7))

# setting_key names for each owned physical slot
_PHYSICAL_KEYS: dict[int, dict[str, str]] = {
    1: {"on": "time1on", "cap": "cap1", "pac": "sellTime1Pac", "start": "sellTime1"},
    2: {"on": "time2on", "cap": "cap2", "pac": "sellTime2Pac", "start": "sellTime2"},
}
# Slots 3-6 are turned off once when the scheduler takes ownership, and
# otherwise left alone.
_UNUSED_SLOT_ON_KEYS = ("time3on", "time4on", "time5on", "time6on")


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


def _most_recent_occurrence(ref: datetime, weekday: int, hour: int, minute: int) -> datetime:
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
    def from_dict(cls, data: dict[str, Any]) -> "VirtualSlot":
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
    """What should be in force right now (or at a projected future instant)."""

    mode: str
    current: int | None
    target_soc: int | None
    sell_power: int
    source: str  # "price_override" | "virtual_slot:<id>" | "none"


_IDLE = Resolution(mode=MODE_IDLE, current=None, target_soc=None, sell_power=0, source="none")


class VirtualSlotScheduler:
    """Owns physical slots 1 & 2; resolves up to 10 virtual slots plus a
    live price-driven override from TariffChargingManager onto them.
    """

    def __init__(
        self,
        hass: HomeAssistant,
        coordinator: SunsynkCoordinator,
        entry_id: str,
        tariff_manager: "TariffChargingManager | None" = None,
        normal_charge_current: int | None = None,
        normal_discharge_current: int | None = None,
    ) -> None:
        self._hass = hass
        self._coordinator = coordinator
        self._entry_id = entry_id
        self._tariff_manager = tariff_manager
        self._normal_charge_current = normal_charge_current
        self._normal_discharge_current = normal_discharge_current

        self._store: Store = Store(hass, STORAGE_VERSION, f"{DOMAIN}_virtual_slots_{entry_id}")
        self._slots: dict[int, VirtualSlot] = {}

        self._enabled = False
        self._current_index = 1
        self._armed_next_start: datetime | None = None
        self._last_written: dict[int, tuple] = {}
        self._last_current_key: tuple | None = None
        self._current_source = "none"

        self._listeners: list[Callable[[], None]] = []
        self._unsub_coordinator: Any = None
        self._unsub_tariff: Any = None

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

    async def async_load(self) -> None:
        data = await self._store.async_load() or {}
        self._slots = {
            int(s["slot_id"]): VirtualSlot.from_dict(s) for s in data.get("slots", [])
        }

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

    @callback
    def _on_tick(self) -> None:
        self._hass.async_create_task(self._async_tick())

    # ── Enable / disable ─────────────────────────────────────────────────

    def set_enabled(self, enabled: bool) -> None:
        self._enabled = enabled
        if enabled:
            self._hass.async_create_task(self._async_bootstrap())
        else:
            self._hass.async_create_task(self._async_shutdown())
        self._notify_listeners()

    async def _async_bootstrap(self) -> None:
        """Take ownership: disable slots 3-6, then arm 1 & 2 from scratch."""
        for serial in self._coordinator.serials:
            for key in _UNUSED_SLOT_ON_KEYS:
                await self._coordinator.async_write_setting(serial, key, 0)
        self._current_index = 1
        self._armed_next_start = None
        self._last_written = {}
        self._last_current_key = None
        await self._async_tick(force=True)

    async def _async_shutdown(self) -> None:
        for serial in self._coordinator.serials:
            await self._coordinator.async_write_setting(serial, _PHYSICAL_KEYS[1]["on"], 0)
            await self._coordinator.async_write_setting(serial, _PHYSICAL_KEYS[2]["on"], 0)
        self._armed_next_start = None
        self._last_written = {}
        self._last_current_key = None

    # ── Slot management (called by services) ───────────────────────────

    async def async_set_slot(self, slot: VirtualSlot) -> None:
        if not (1 <= slot.slot_id <= MAX_VIRTUAL_SLOTS):
            raise ValueError(f"slot_id must be 1-{MAX_VIRTUAL_SLOTS}")
        self._slots[slot.slot_id] = slot
        await self._async_persist()
        self._notify_listeners()
        if self._enabled:
            await self._async_tick(force=True)

    async def async_clear_slot(self, slot_id: int) -> None:
        self._slots.pop(slot_id, None)
        await self._async_persist()
        self._notify_listeners()
        if self._enabled:
            await self._async_tick(force=True)

    def list_slots(self) -> list[dict[str, Any]]:
        return [s.to_dict() for s in sorted(self._slots.values(), key=lambda s: s.slot_id)]

    # ── Resolution ───────────────────────────────────────────────────────

    def _resolve_virtual(self, at: datetime) -> tuple[Resolution, datetime | None]:
        """Resolve the virtual (time-based) schedule at `at`. Ignores price override.

        Returns (resolution, next_boundary) where next_boundary is the
        earliest datetime > `at` at which the resolution could change.
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
            return _IDLE, next_boundary

        slot = chosen[0]
        resolution = Resolution(
            mode=slot.mode,
            current=slot.current,
            target_soc=slot.target_soc,
            sell_power=slot.sell_power,
            source=f"virtual_slot:{slot.slot_id}",
        )
        return resolution, next_boundary

    def _resolve_now(self, now: datetime) -> tuple[Resolution, datetime | None]:
        """Resolve the effective state right now, honouring the live price
        override. The override never gets a boundary of its own — it only
        ever applies to "now", never to a projected future instant, so
        this must not be used for anything but the current tick's `now`.
        """
        tm = self._tariff_manager
        if tm is not None:
            if tm.is_charging_active:
                return (
                    Resolution(
                        mode=MODE_CHARGE,
                        current=None,  # tariff manager owns chargeCurrent
                        target_soc=tm.target_soc,
                        sell_power=0,
                        source="price_override",
                    ),
                    None,
                )
            if tm.is_discharging_active:
                return (
                    Resolution(
                        mode=MODE_DISCHARGE,
                        current=None,  # tariff manager owns dischargeCurrent
                        target_soc=tm.discharge_min_soc,
                        sell_power=0,
                        source="price_override",
                    ),
                    None,
                )
        return self._resolve_virtual(now)

    def _now(self) -> datetime:
        return dt_util.now()

    # ── Tick ─────────────────────────────────────────────────────────────

    async def _async_tick(self, force: bool = False) -> None:
        if not self._enabled:
            return

        now = self._now()
        changed = False
        need_arm = force or self._armed_next_start is None

        if self._armed_next_start is not None and now >= self._armed_next_start:
            # The pre-armed slot's start time has arrived on the hardware's
            # own timeline — swap roles so our bookkeeping matches reality.
            other_index = 2 if self._current_index == 1 else 1
            self._current_index = other_index
            need_arm = True
            changed = True

        resolution, _ = self._resolve_now(now)
        wrote_window = await self._write_window_if_changed(
            self._current_index, resolution, start=None
        )
        wrote_current = await self._apply_current_if_changed(resolution)
        changed = changed or wrote_window or wrote_current
        if resolution.source != self._current_source:
            self._current_source = resolution.source
            changed = True

        if need_arm:
            other_index = 2 if self._current_index == 1 else 1
            prev_boundary = self._armed_next_start
            await self._arm_next(other_index, now)
            if self._armed_next_start != prev_boundary:
                changed = True

        if changed:
            self._notify_listeners()

    async def _arm_next(self, index: int, now: datetime) -> None:
        _, next_boundary = self._resolve_virtual(now)
        if next_boundary is None:
            # Nothing scheduled — arm far in the future, idle.
            next_boundary = now + timedelta(days=7)
            projected = _IDLE
        else:
            projected, _ = self._resolve_virtual(next_boundary)
        self._armed_next_start = next_boundary
        # This slot isn't active yet — only the permission window is
        # pre-armed. Its chargeCurrent/dischargeCurrent must never be
        # applied now, since those registers are global/immediate-effect.
        await self._write_window_if_changed(
            index, projected, start=next_boundary.strftime("%H:%M")
        )

    async def _write_window_if_changed(
        self, index: int, resolution: Resolution, *, start: str | None
    ) -> bool:
        """Write time{n}on / cap{n} / sellTime{n}Pac (+ start time if given)."""
        on = resolution.mode != MODE_IDLE
        cap = resolution.target_soc if resolution.target_soc is not None else 0
        pac = resolution.sell_power if resolution.mode == MODE_DISCHARGE else 0
        cache_key = (on, cap, pac, start)
        if self._last_written.get(index) == cache_key:
            return False

        keys = _PHYSICAL_KEYS[index]
        for serial in self._coordinator.serials:
            await self._coordinator.async_write_setting(serial, keys["on"], 1 if on else 0)
            await self._coordinator.async_write_setting(serial, keys["cap"], cap)
            await self._coordinator.async_write_setting(serial, keys["pac"], pac)
            if start is not None:
                await self._coordinator.async_write_setting(serial, keys["start"], start)

        self._last_written[index] = cache_key
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
            current_key = ("idle",)
            if current_key == self._last_current_key:
                return False
            for serial in self._coordinator.serials:
                if self._normal_charge_current is not None:
                    await self._coordinator.async_write_setting(
                        serial, "chargeCurrent", self._normal_charge_current
                    )
                    wrote = True
                if self._normal_discharge_current is not None:
                    await self._coordinator.async_write_setting(
                        serial, "dischargeCurrent", self._normal_discharge_current
                    )
                    wrote = True
            self._last_current_key = current_key
            return wrote
        else:
            return False

        current_key = (key, value)
        if current_key == self._last_current_key:
            return False
        for serial in self._coordinator.serials:
            await self._coordinator.async_write_setting(serial, key, value)
        self._last_current_key = current_key
        return True

    # ── Properties ───────────────────────────────────────────────────────

    @property
    def is_enabled(self) -> bool:
        return self._enabled

    @property
    def active_source(self) -> str:
        return self._current_source

    @property
    def current_physical_slot(self) -> int:
        return self._current_index

    @property
    def next_boundary(self) -> datetime | None:
        return self._armed_next_start

"""Tests for the virtual slot resolution engine."""
from __future__ import annotations

from datetime import datetime
from unittest.mock import MagicMock

import pytest

from custom_components.sunsynk.virtual_slots import (
    MODE_CHARGE,
    MODE_DISCHARGE,
    MODE_IDLE,
    VirtualSlot,
    VirtualSlotScheduler,
)

MON = 0
TUE = 1


def _dt(year, month, day, hour, minute) -> datetime:
    return datetime(year, month, day, hour, minute)


# ── VirtualSlot.window_containing ───────────────────────────────────────────


def test_window_containing_simple_same_day():
    slot = VirtualSlot(slot_id=1, start="10:00", end="14:00", mode=MODE_CHARGE)
    # 2024-01-01 is a Monday
    assert slot.window_containing(_dt(2024, 1, 1, 12, 0)) is not None
    assert slot.window_containing(_dt(2024, 1, 1, 9, 0)) is None
    assert slot.window_containing(_dt(2024, 1, 1, 14, 0)) is None  # end exclusive


def test_window_containing_wraps_midnight():
    slot = VirtualSlot(slot_id=1, start="22:00", end="06:00", mode=MODE_CHARGE)
    # active late on the start day
    assert slot.window_containing(_dt(2024, 1, 1, 23, 0)) is not None
    # active early the next calendar day
    assert slot.window_containing(_dt(2024, 1, 2, 5, 0)) is not None
    # not active mid-day
    assert slot.window_containing(_dt(2024, 1, 1, 12, 0)) is None


def test_window_containing_respects_weekdays():
    slot = VirtualSlot(
        slot_id=1, start="10:00", end="14:00", mode=MODE_CHARGE, weekdays=frozenset({MON})
    )
    # 2024-01-02 is a Tuesday — slot only applies on Monday
    assert slot.window_containing(_dt(2024, 1, 2, 12, 0)) is None
    assert slot.window_containing(_dt(2024, 1, 1, 12, 0)) is not None


def test_window_containing_disabled_returns_none():
    slot = VirtualSlot(slot_id=1, start="10:00", end="14:00", mode=MODE_CHARGE, enabled=False)
    assert slot.window_containing(_dt(2024, 1, 1, 12, 0)) is None


def test_next_start_after():
    slot = VirtualSlot(slot_id=1, start="10:00", end="14:00", mode=MODE_CHARGE)
    nxt = slot.next_start_after(_dt(2024, 1, 1, 12, 0))
    assert nxt == _dt(2024, 1, 2, 10, 0)


# ── VirtualSlotScheduler._resolve_virtual ───────────────────────────────────


def _make_scheduler(mock_hass, mock_coordinator, tariff_manager=None) -> VirtualSlotScheduler:
    return VirtualSlotScheduler(
        hass=mock_hass,
        coordinator=mock_coordinator,
        entry_id="test_entry",
        tariff_manager=tariff_manager,
    )


def test_resolve_virtual_no_slots_is_idle(mock_hass, mock_coordinator):
    sched = _make_scheduler(mock_hass, mock_coordinator)
    resolution, window_start, next_boundary = sched._resolve_virtual(_dt(2024, 1, 1, 12, 0))
    assert resolution.mode == MODE_IDLE
    assert window_start is None
    assert next_boundary is None


def test_resolve_virtual_single_active_slot(mock_hass, mock_coordinator):
    sched = _make_scheduler(mock_hass, mock_coordinator)
    sched._slots[1] = VirtualSlot(
        slot_id=1, start="22:00", end="06:00", mode=MODE_CHARGE, current=100, target_soc=90
    )
    resolution, window_start, next_boundary = sched._resolve_virtual(_dt(2024, 1, 1, 23, 0))
    assert resolution.mode == MODE_CHARGE
    assert resolution.source == "virtual_slot:1"
    assert resolution.current == 100
    assert resolution.target_soc == 90
    assert window_start == _dt(2024, 1, 1, 22, 0)
    assert next_boundary == _dt(2024, 1, 2, 6, 0)  # window end


def test_resolve_virtual_priority_tiebreak(mock_hass, mock_coordinator):
    sched = _make_scheduler(mock_hass, mock_coordinator)
    # Both cover 12:00-13:00 on Monday, slot 2 has higher priority.
    sched._slots[1] = VirtualSlot(
        slot_id=1, start="10:00", end="14:00", mode=MODE_CHARGE, priority=1, current=50
    )
    sched._slots[2] = VirtualSlot(
        slot_id=2, start="12:00", end="13:00", mode=MODE_DISCHARGE, priority=5, current=80
    )
    resolution, _, _ = sched._resolve_virtual(_dt(2024, 1, 1, 12, 30))
    assert resolution.source == "virtual_slot:2"
    assert resolution.mode == MODE_DISCHARGE


def test_resolve_virtual_next_boundary_is_soonest_of_all_slots(mock_hass, mock_coordinator):
    sched = _make_scheduler(mock_hass, mock_coordinator)
    sched._slots[1] = VirtualSlot(slot_id=1, start="10:00", end="18:00", mode=MODE_CHARGE)
    sched._slots[2] = VirtualSlot(slot_id=2, start="12:00", end="13:00", mode=MODE_DISCHARGE)
    # At 11:00 slot 1 is active (ends 18:00) but slot 2 starts at 12:00 first.
    _, _, next_boundary = sched._resolve_virtual(_dt(2024, 1, 1, 11, 0))
    assert next_boundary == _dt(2024, 1, 1, 12, 0)


# ── Price override takes precedence ─────────────────────────────────────────


def test_override_beats_virtual_slot(mock_hass, mock_coordinator):
    tariff_manager = MagicMock()
    tariff_manager.is_charging_active = True
    tariff_manager.is_discharging_active = False
    tariff_manager.target_soc = 95
    sched = _make_scheduler(mock_hass, mock_coordinator, tariff_manager=tariff_manager)
    sched._slots[1] = VirtualSlot(
        slot_id=1, start="00:00", end="23:59", mode=MODE_DISCHARGE, current=50
    )
    plan = sched._plan(_dt(2024, 1, 1, 12, 0))
    assert plan.active_resolution.source == "price_override"
    assert plan.active_resolution.mode == MODE_CHARGE
    assert plan.active_resolution.target_soc == 95


def test_no_override_falls_back_to_virtual(mock_hass, mock_coordinator):
    tariff_manager = MagicMock()
    tariff_manager.is_charging_active = False
    tariff_manager.is_discharging_active = False
    sched = _make_scheduler(mock_hass, mock_coordinator, tariff_manager=tariff_manager)
    sched._slots[1] = VirtualSlot(
        slot_id=1, start="00:00", end="23:59", mode=MODE_CHARGE, current=50, target_soc=80
    )
    plan = sched._plan(_dt(2024, 1, 1, 12, 0))
    assert plan.active_resolution.source == "virtual_slot:1"


# ── Physical slot assignment (1 = earlier time-of-day, 6 = later) ──────────


def test_plan_assigns_earlier_time_to_slot1(mock_hass, mock_coordinator):
    sched = _make_scheduler(mock_hass, mock_coordinator)
    sched._slots[1] = VirtualSlot(
        slot_id=1, start="10:00", end="18:00", mode=MODE_CHARGE, current=40, target_soc=80
    )
    sched._slots[2] = VirtualSlot(
        slot_id=2, start="18:00", end="10:00", mode=MODE_DISCHARGE, current=30, target_soc=20,
        sell_power=3000,
    )
    plan = sched._plan(_dt(2024, 1, 1, 12, 0))  # slot 1 active, slot 2 upcoming at 18:00
    assert plan.slot1_start == "10:00"
    assert plan.slot1.mode == MODE_CHARGE
    assert plan.slot6_start == "18:00"
    assert plan.slot6.mode == MODE_DISCHARGE
    assert plan.active_physical == 1


def test_plan_puts_wrapping_active_window_on_slot6(mock_hass, mock_coordinator):
    """Regression test: a slot that started late in the day (e.g. 23:30) and
    is still active past midnight must land on physical slot 6, never slot 1
    — Sunsynk only allows Timer 6 to wrap into Timer 1, and putting a late
    start time on slot 1 is exactly the ordering conflict reported against
    a real Sunsynk Acure inverter (slot 1 was silently ignored).
    """
    sched = _make_scheduler(mock_hass, mock_coordinator)
    sched._slots[1] = VirtualSlot(
        slot_id=1, start="23:30", end="05:30", mode=MODE_CHARGE, current=100, target_soc=90
    )
    sched._slots[2] = VirtualSlot(
        slot_id=2, start="05:30", end="23:30", mode=MODE_DISCHARGE, current=20, target_soc=20,
        sell_power=4000,
    )
    # 01:00 — the wrapping charge window (started 23:30 yesterday) is active.
    plan = sched._plan(_dt(2024, 1, 2, 1, 0))
    assert plan.active_resolution.mode == MODE_CHARGE
    assert plan.active_physical == 6, "the late-starting (23:30) active window must be on slot 6"
    assert plan.slot6_start == "23:30"
    assert plan.slot1_start == "05:30"
    assert plan.slot1.mode == MODE_DISCHARGE


def test_plan_idle_with_nothing_scheduled_uses_slot1_and_disables_slot6(
    mock_hass, mock_coordinator
):
    sched = _make_scheduler(mock_hass, mock_coordinator)
    plan = sched._plan(_dt(2024, 1, 1, 12, 0))
    assert plan.active_physical == 1
    assert plan.slot1.mode == MODE_IDLE
    assert plan.slot6.mode == MODE_IDLE
    assert plan.slot1_start == "00:00"


# ── End-to-end tick / bootstrap wiring ──────────────────────────────────────


def _written(mock_coordinator) -> set[tuple[str, object]]:
    return {(c.args[1], c.args[2]) for c in mock_coordinator.async_write_setting.call_args_list}


@pytest.mark.asyncio
async def test_bootstrap_disables_unused_slots_and_writes_slot1(mock_hass, mock_coordinator):
    sched = _make_scheduler(mock_hass, mock_coordinator)
    sched._slots[1] = VirtualSlot(
        slot_id=1, start="00:00", end="23:59", mode=MODE_CHARGE, current=60, target_soc=90
    )
    sched._now = lambda: _dt(2024, 1, 1, 12, 0)
    sched._enabled = True

    await sched._async_bootstrap()

    written = _written(mock_coordinator)
    assert ("time2on", 0) in written
    assert ("time3on", 0) in written
    assert ("time4on", 0) in written
    assert ("time5on", 0) in written
    assert ("time1on", 1) in written
    assert ("cap1", 90) in written
    assert ("chargeCurrent", 60) in written
    assert sched.active_source == "virtual_slot:1"
    assert sched.current_physical_slot == 1


@pytest.mark.asyncio
async def test_tick_reassigns_physical_slot_across_midnight_wrap(mock_hass, mock_coordinator):
    """slot 1 = 23:30-05:30 charge (wraps), slot 2 = 05:30-23:30 discharge.

    While the wrapping charge window is active (e.g. 01:00) it must sit on
    physical slot 6. Once the plain daytime discharge window takes over
    (05:30 <= now < 23:30, no wrap) it must move to physical slot 1.
    """
    sched = _make_scheduler(mock_hass, mock_coordinator)
    sched._slots[1] = VirtualSlot(
        slot_id=1, start="23:30", end="05:30", mode=MODE_CHARGE, current=40, target_soc=80
    )
    sched._slots[2] = VirtualSlot(
        slot_id=2, start="05:30", end="23:30", mode=MODE_DISCHARGE, current=30, target_soc=20,
        sell_power=3000,
    )
    sched._now = lambda: _dt(2024, 1, 2, 1, 0)
    sched._enabled = True
    await sched._async_bootstrap()
    assert sched.current_physical_slot == 6
    assert sched.active_source == "virtual_slot:1"

    mock_coordinator.async_write_setting.reset_mock()
    sched._now = lambda: _dt(2024, 1, 2, 10, 0)
    await sched._async_tick()

    assert sched.current_physical_slot == 1
    assert sched.active_source == "virtual_slot:2"
    # Slot 1 was already pre-armed with the discharge window's exact
    # parameters back at bootstrap (it was the "upcoming" boundary then),
    # so crossing into it needs no register rewrite — only the current
    # limit (a separate, immediate-effect register) changes.
    written = _written(mock_coordinator)
    assert ("dischargeCurrent", 30) in written
    assert ("time1on", 1) not in written


@pytest.mark.asyncio
async def test_tick_does_not_apply_current_while_price_override_active(mock_hass, mock_coordinator):
    tariff_manager = MagicMock()
    tariff_manager.is_charging_active = True
    tariff_manager.is_discharging_active = False
    tariff_manager.target_soc = 95
    sched = _make_scheduler(mock_hass, mock_coordinator, tariff_manager=tariff_manager)
    sched._slots[1] = VirtualSlot(
        slot_id=1, start="00:00", end="23:59", mode=MODE_DISCHARGE, current=30, target_soc=20
    )
    sched._now = lambda: _dt(2024, 1, 1, 12, 0)
    sched._enabled = True

    await sched._async_bootstrap()

    written = _written(mock_coordinator)
    assert sched.active_source == "price_override"
    assert ("cap1", 95) in written  # window follows the override's target SOC
    assert ("chargeCurrent", 30) not in written
    assert ("dischargeCurrent", 30) not in written


@pytest.mark.asyncio
async def test_shutdown_disables_slot1_and_slot6(mock_hass, mock_coordinator):
    sched = _make_scheduler(mock_hass, mock_coordinator)
    sched._enabled = True
    mock_coordinator.async_write_setting.reset_mock()

    await sched._async_shutdown()

    written = _written(mock_coordinator)
    assert ("time1on", 0) in written
    assert ("time6on", 0) in written

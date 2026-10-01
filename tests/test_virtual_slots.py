"""Tests for the virtual slot resolution engine."""
from __future__ import annotations

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

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


@pytest.mark.asyncio
async def test_next_boundary_has_own_timer(mock_hass, mock_coordinator):
    sched = _make_scheduler(mock_hass, mock_coordinator)
    sched._slots[1] = VirtualSlot(
        slot_id=1, start="10:00", end="11:00", mode=MODE_CHARGE
    )
    sched._now = lambda: _dt(2024, 1, 1, 10, 30)
    sched._enabled = True
    unsubscribe = MagicMock()

    def _consume_task(coro):
        coro.close()
        return MagicMock()

    mock_hass.async_create_task.side_effect = _consume_task

    with patch(
        "custom_components.sunsynk.virtual_slots.async_track_point_in_time",
        return_value=unsubscribe,
    ) as track:
        await sched._async_tick()
        callback = track.call_args.args[1]
        assert track.call_args.args[2] == _dt(2024, 1, 1, 11, 0)
        callback(_dt(2024, 1, 1, 11, 0))
        mock_hass.async_create_task.assert_called()
        sched.stop()

    # A fired callback clears its own cancellation handle; stop is harmless.
    unsubscribe.assert_not_called()


# ── VirtualSlotScheduler._resolve_virtual ───────────────────────────────────


def _make_scheduler(
    mock_hass,
    mock_coordinator,
    tariff_manager=None,
    serial="TEST123",
    normal_charge_current=None,
    normal_discharge_current=None,
) -> VirtualSlotScheduler:
    return VirtualSlotScheduler(
        hass=mock_hass,
        coordinator=mock_coordinator,
        entry_id="test_entry",
        serial=serial,
        tariff_manager=tariff_manager,
        normal_charge_current=normal_charge_current,
        normal_discharge_current=normal_discharge_current,
    )


def _consume_created_tasks(hass):
    def _consume(coro):
        coro.close()
        return MagicMock()

    hass.async_create_task.side_effect = _consume


def test_lifecycle_listeners_enable_and_properties(
    mock_hass, mock_coordinator
):
    _consume_created_tasks(mock_hass)
    tariff = MagicMock()
    unsub_coordinator = MagicMock()
    unsub_tariff = MagicMock()
    mock_coordinator.async_add_listener.return_value = unsub_coordinator
    tariff.async_add_listener.return_value = unsub_tariff
    sched = _make_scheduler(mock_hass, mock_coordinator, tariff_manager=tariff)
    listener = MagicMock()
    remove = sched.async_add_listener(listener)

    sched.start()
    sched.set_enabled(True)
    assert sched.is_enabled
    assert sched.serial == "TEST123"
    assert sched.active_source == "none"
    assert sched.current_physical_slot == 1
    assert sched.next_boundary is None
    sched.set_enabled(False)
    sched.stop()
    remove()

    assert listener.call_count == 2
    unsub_coordinator.assert_called_once()
    unsub_tariff.assert_called_once()


@pytest.mark.asyncio
async def test_slot_mutations_validate_and_tick_when_enabled(
    mock_hass, mock_coordinator
):
    sched = _make_scheduler(mock_hass, mock_coordinator)
    sched._store = MagicMock(async_save=AsyncMock())
    sched._async_tick = AsyncMock()
    with pytest.raises(ValueError, match="slot_id"):
        await sched.async_set_slot(
            VirtualSlot(slot_id=99, start="00:00", end="01:00", mode=MODE_IDLE)
        )
    sched._enabled = True
    await sched.async_set_slot(
        VirtualSlot(slot_id=1, start="00:00", end="01:00", mode=MODE_IDLE)
    )
    await sched.async_clear_slot(1)
    assert sched._async_tick.await_count == 2


@pytest.mark.asyncio
async def test_disabled_tick_and_duplicate_idle_current_are_noops(
    mock_hass, mock_coordinator
):
    sched = _make_scheduler(mock_hass, mock_coordinator)
    await sched._async_tick_locked()
    sched._normal_charge_current = 20
    resolution = MagicMock(mode=MODE_IDLE, source="none", current=None)
    assert await sched._apply_current_if_changed(resolution) is True
    assert await sched._apply_current_if_changed(
        resolution
    ) is False
    mock_coordinator.async_write_setting.assert_awaited_once_with(
        "TEST123", "chargeCurrent", 20
    )


def test_invalid_rated_power_falls_back(mock_hass, mock_coordinator):
    mock_coordinator.data["TEST123"]["inverter"] = {"ratePower": object()}
    sched = _make_scheduler(mock_hass, mock_coordinator)
    assert sched._uncapped_sell_power() == sched._DEFAULT_UNCAPPED_SELL_POWER
    assert sched._now() is not None


def test_disabled_slot_is_ignored_for_boundaries(mock_hass, mock_coordinator):
    sched = _make_scheduler(mock_hass, mock_coordinator)
    sched._slots[1] = VirtualSlot(
        slot_id=1,
        start="10:00",
        end="11:00",
        mode=MODE_CHARGE,
        enabled=False,
    )
    resolution, start, boundary = sched._resolve_virtual(
        _dt(2024, 1, 1, 10, 30)
    )
    assert resolution.mode == MODE_IDLE
    assert start is None
    assert boundary is None


@pytest.mark.asyncio
async def test_shutdown_failure_paths(mock_hass, mock_coordinator):
    sched = _make_scheduler(
        mock_hass,
        mock_coordinator,
        normal_charge_current=40,
    )
    mock_coordinator.async_write_settings.side_effect = RuntimeError("boom")
    assert await sched._async_shutdown(force_disable=True) is False

    sched._original_settings = {"time1on": "true"}
    assert await sched._async_shutdown(force_disable=False) is False


def test_resolve_virtual_no_slots_is_idle(mock_hass, mock_coordinator):
    sched = _make_scheduler(mock_hass, mock_coordinator)
    resolution, window_start, next_boundary = sched._resolve_virtual(_dt(2024, 1, 1, 12, 0))
    assert resolution.mode == MODE_IDLE
    assert window_start is None
    assert next_boundary is None


@pytest.mark.asyncio
async def test_load_seeds_per_inverter_store_from_legacy_slots(
    mock_hass, mock_coordinator
):
    sched = _make_scheduler(mock_hass, mock_coordinator)
    sched._store = MagicMock(
        async_load=AsyncMock(return_value=None),
        async_save=AsyncMock(),
    )
    legacy_slot = VirtualSlot(
        slot_id=3,
        start="01:00",
        end="02:00",
        mode=MODE_CHARGE,
    ).to_dict()

    await sched.async_load(legacy_slots=[legacy_slot])

    assert sched.list_slots() == [legacy_slot]
    sched._store.async_save.assert_awaited_once()


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
    tariff_manager.is_charging_active_for.return_value = True
    tariff_manager.is_discharging_active_for.return_value = False
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
    tariff_manager.is_charging_active_for.return_value = False
    tariff_manager.is_discharging_active_for.return_value = False
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
    written = {
        (call.args[1], call.args[2])
        for call in mock_coordinator.async_write_setting.call_args_list
    }
    for call in mock_coordinator.async_write_settings.call_args_list:
        written.update(call.args[1].items())
    return written


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
    slot1_batches = [
        call.args[1]
        for call in mock_coordinator.async_write_settings.call_args_list
        if "time1on" in call.args[1]
    ]
    assert slot1_batches == [
        {
            "time1on": 1,
            "cap1": 90,
            # Not a discharge window: no artificial cap on house load (#21).
            "sellTime1Pac": VirtualSlotScheduler._DEFAULT_UNCAPPED_SELL_POWER,
            "sellTime1": "00:00",
            "sellTime1on": 0,
        }
    ]


@pytest.mark.asyncio
async def test_queued_bootstrap_does_nothing_after_shutdown_started(
    mock_hass, mock_coordinator
):
    sched = _make_scheduler(mock_hass, mock_coordinator)
    sched._enabled = False

    await sched._async_bootstrap()

    mock_coordinator.async_write_setting.assert_not_awaited()
    mock_coordinator.async_write_settings.assert_not_awaited()
    assert sched._original_settings is None


# ── slot 2 as slot 1's implicit end boundary (#21) ────────────────────────
#
# Slot 1's *end* isn't a field this scheduler writes — Sunsynk derives it
# from the next physical slot's (2) own start time, even while slot 2 is
# disabled. A reporter found slot 1 silently truncated back to whatever
# slot 2's leftover, pre-VSS start time was. Slot 2's start is now pinned
# to slot 6's start (always a validly later-or-equal value, since slot 1
# is defined as whichever boundary has the earlier time-of-day).


@pytest.mark.asyncio
async def test_bootstrap_pins_slot2_start_to_slot6_start(mock_hass, mock_coordinator):
    sched = _make_scheduler(mock_hass, mock_coordinator)
    sched._slots[1] = VirtualSlot(
        slot_id=1, start="10:00", end="18:00", mode=MODE_CHARGE, current=40, target_soc=80
    )
    sched._slots[2] = VirtualSlot(
        slot_id=2, start="18:00", end="10:00", mode=MODE_DISCHARGE, current=30, target_soc=20,
        sell_power=3000,
    )
    sched._now = lambda: _dt(2024, 1, 1, 12, 0)  # slot 1 active (10:00), slot 6 upcoming (18:00)
    sched._enabled = True

    await sched._async_bootstrap()

    assert ("sellTime2", "18:00") in _written(mock_coordinator)


@pytest.mark.asyncio
async def test_slot2_boundary_not_rewritten_when_unchanged(mock_hass, mock_coordinator):
    sched = _make_scheduler(mock_hass, mock_coordinator)
    sched._slots[1] = VirtualSlot(
        slot_id=1, start="10:00", end="18:00", mode=MODE_CHARGE, current=40, target_soc=80
    )
    sched._now = lambda: _dt(2024, 1, 1, 12, 0)
    sched._enabled = True
    await sched._async_bootstrap()

    mock_coordinator.async_write_setting.reset_mock()
    mock_coordinator.async_write_settings.reset_mock()
    await sched._async_tick()

    assert ("sellTime2", "18:00") not in _written(mock_coordinator)


@pytest.mark.asyncio
async def test_slot2_boundary_updates_when_slot6_start_changes(mock_hass, mock_coordinator):
    sched = _make_scheduler(mock_hass, mock_coordinator)
    sched._slots[1] = VirtualSlot(
        slot_id=1, start="10:00", end="18:00", mode=MODE_CHARGE, current=40, target_soc=80
    )
    sched._now = lambda: _dt(2024, 1, 1, 12, 0)
    sched._enabled = True
    await sched._async_bootstrap()
    assert ("sellTime2", "18:00") in _written(mock_coordinator)

    mock_coordinator.async_write_setting.reset_mock()
    mock_coordinator.async_write_settings.reset_mock()
    sched._slots[1] = VirtualSlot(
        slot_id=1, start="10:00", end="19:00", mode=MODE_CHARGE, current=40, target_soc=80
    )
    await sched._async_tick()

    assert ("sellTime2", "19:00") in _written(mock_coordinator)


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
    mock_coordinator.async_write_settings.reset_mock()
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
    tariff_manager.is_charging_active_for.return_value = True
    tariff_manager.is_discharging_active_for.return_value = False
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
    mock_coordinator.async_write_settings.reset_mock()

    await sched._async_shutdown()

    written = _written(mock_coordinator)
    assert ("time1on", 0) in written


# ── sellTime{n}on — the "Sell" permission checkbox (#21) ─────────────────────
#
# Sunsynk added a per-slot "Sell" checkbox (sellTime{n}on) specifically so
# battery discharge can be sold to the grid while System Work Mode is
# Zero-Export/Limited to Home — a slot can have on/cap/sellTime{n}Pac all
# correctly set and still export nothing without it. Previously never
# written by this scheduler at all.


@pytest.mark.asyncio
async def test_discharge_slot_enables_sell_permission(mock_hass, mock_coordinator):
    sched = _make_scheduler(mock_hass, mock_coordinator)
    sched._slots[1] = VirtualSlot(
        slot_id=1, start="00:00", end="23:59", mode=MODE_DISCHARGE, current=30,
        target_soc=20, sell_power=3000,
    )
    sched._now = lambda: _dt(2024, 1, 1, 12, 0)
    sched._enabled = True

    await sched._async_bootstrap()

    written = _written(mock_coordinator)
    assert ("sellTime1on", 1) in written


@pytest.mark.asyncio
async def test_charge_slot_does_not_enable_sell_permission(mock_hass, mock_coordinator):
    sched = _make_scheduler(mock_hass, mock_coordinator)
    sched._slots[1] = VirtualSlot(
        slot_id=1, start="00:00", end="23:59", mode=MODE_CHARGE, current=60, target_soc=90
    )
    sched._now = lambda: _dt(2024, 1, 1, 12, 0)
    sched._enabled = True

    await sched._async_bootstrap()

    written = _written(mock_coordinator)
    assert ("sellTime1on", 0) in written
    assert ("sellTime1on", 1) not in written


@pytest.mark.asyncio
async def test_idle_slot_does_not_enable_sell_permission(mock_hass, mock_coordinator):
    sched = _make_scheduler(mock_hass, mock_coordinator)
    sched._now = lambda: _dt(2024, 1, 1, 12, 0)
    sched._enabled = True

    await sched._async_bootstrap()

    written = _written(mock_coordinator)
    assert ("sellTime1on", 0) in written


@pytest.mark.asyncio
async def test_price_override_discharge_enables_sell_permission(mock_hass, mock_coordinator):
    """A live Tariff Manager discharge override goes through the same
    physical-slot write path, so it needs sellTime{n}on too."""
    tariff_manager = MagicMock()
    tariff_manager.is_charging_active_for.return_value = False
    tariff_manager.is_discharging_active_for.return_value = True
    tariff_manager.discharge_min_soc = 10
    sched = _make_scheduler(mock_hass, mock_coordinator, tariff_manager=tariff_manager)
    sched._now = lambda: _dt(2024, 1, 1, 12, 0)
    sched._enabled = True

    await sched._async_bootstrap()

    written = _written(mock_coordinator)
    assert sched.active_source == "price_override"
    assert ("sellTime1on", 1) in written
    assert ("time6on", 0) in written


@pytest.mark.asyncio
async def test_shutdown_restores_exact_pre_ownership_settings(
    mock_hass, mock_coordinator
):
    original = {
        "cap1": 55,
        "sellTime1": "06:30",
        "time1on": "true",
        "sellTime2": "09:00",
        "time2on": "true",
        "time3on": "false",
        "cap6": 25,
        "sellTime6": "22:30",
        "time6on": "false",
        "chargeCurrent": 47,
        "dischargeCurrent": 53,
    }
    mock_coordinator.data["TEST123"]["settings"].update(original)
    sched = _make_scheduler(
        mock_hass,
        mock_coordinator,
        normal_charge_current=47,
        normal_discharge_current=53,
    )
    sched._now = lambda: _dt(2024, 1, 1, 12, 0)
    sched._enabled = True

    await sched._async_bootstrap()
    mock_coordinator.async_write_setting.reset_mock()
    mock_coordinator.async_write_settings.reset_mock()
    success = await sched.async_shutdown()

    assert success is True
    restored = {}
    for call in mock_coordinator.async_write_settings.call_args_list:
        restored.update(call.args[1])
    assert restored == original
    assert sched._original_settings is None


@pytest.mark.asyncio
async def test_shutdown_continues_restoring_after_one_setting_fails(
    mock_hass, mock_coordinator
):
    sched = _make_scheduler(mock_hass, mock_coordinator)
    sched._original_settings = {"time1on": "true", "time6on": "false"}
    mock_coordinator.async_write_settings.side_effect = RuntimeError(
        "temporary failure"
    )

    success = await sched.async_shutdown()

    assert success is False
    mock_coordinator.async_write_settings.assert_awaited_once()
    assert sched._original_settings == {
        "time1on": "true",
        "time6on": "false",
    }


@pytest.mark.asyncio
async def test_shutdown_uses_live_tariff_normal_currents(
    mock_hass, mock_coordinator
):
    tariff_manager = MagicMock(
        normal_charge_current=61,
        normal_discharge_current=62,
    )
    sched = _make_scheduler(
        mock_hass,
        mock_coordinator,
        tariff_manager=tariff_manager,
        normal_charge_current=47,
        normal_discharge_current=53,
    )
    sched._original_settings = {"time1on": "true"}

    assert await sched.async_shutdown() is True

    mock_coordinator.async_write_settings.assert_any_await(
        "TEST123",
        {"chargeCurrent": 61, "dischargeCurrent": 62},
    )


# ── Price-override discharge must not cap export at 0 W (#21) ───────────────
#
# Resolution.sell_power used to be hardcoded to 0 for a price-override
# discharge, since Tariff Manager has no config option of its own for max
# export power. That meant a price-driven discharge correctly raised
# dischargeCurrent and still exported nothing — the active physical slot's
# own sellTime{n}Pac silently capped it at zero. Now uses the inverter's
# own rated power so only Tariff Manager's dischargeCurrent actually
# limits export.


@pytest.mark.asyncio
async def test_price_override_discharge_uses_inverter_rated_power_as_sell_cap(
    mock_hass, mock_coordinator
):
    mock_coordinator.data["TEST123"]["inverter"] = {"ratePower": 8000}
    tariff_manager = MagicMock()
    tariff_manager.is_charging_active_for.return_value = False
    tariff_manager.is_discharging_active_for.return_value = True
    tariff_manager.discharge_min_soc = 10
    sched = _make_scheduler(mock_hass, mock_coordinator, tariff_manager=tariff_manager)
    sched._now = lambda: _dt(2024, 1, 1, 12, 0)
    sched._enabled = True

    await sched._async_bootstrap()

    assert ("sellTime1Pac", 8000) in _written(mock_coordinator)


@pytest.mark.asyncio
async def test_price_override_discharge_falls_back_to_default_when_rate_power_unknown(
    mock_hass, mock_coordinator
):
    tariff_manager = MagicMock()
    tariff_manager.is_charging_active_for.return_value = False
    tariff_manager.is_discharging_active_for.return_value = True
    tariff_manager.discharge_min_soc = 10
    sched = _make_scheduler(mock_hass, mock_coordinator, tariff_manager=tariff_manager)
    sched._now = lambda: _dt(2024, 1, 1, 12, 0)
    sched._enabled = True

    await sched._async_bootstrap()

    written = dict(_written(mock_coordinator))
    assert written["sellTime1Pac"] == VirtualSlotScheduler._DEFAULT_UNCAPPED_SELL_POWER
    assert written["sellTime1Pac"] > 0


# ── write_target_serials dedup: a parallel slave's writes are skipped ───────
#
# Regression coverage (#21): async_write_setting redirects a slave's write
# to its master, but this scheduler used to still call it once per
# *configured* serial — for a parallel group, that meant writing the same
# setting to the master twice per tick (once via the redirected slave call,
# once via the native master call). A second write landing right behind the
# first was, on its own, enough to make the master intermittently
# reject/revert one of them.


@pytest.mark.asyncio
async def test_bootstrap_never_calls_write_setting_for_a_parallel_slave(
    mock_hass, mock_coordinator
):
    mock_coordinator.serials = ["SLAVE1", "MASTER1"]
    mock_coordinator.write_target_serials = ["MASTER1"]  # slave collapsed in
    sched = _make_scheduler(mock_hass, mock_coordinator, serial="MASTER1")
    sched._slots[1] = VirtualSlot(
        slot_id=1, start="00:00", end="23:59", mode=MODE_CHARGE, current=60, target_soc=90
    )
    sched._now = lambda: _dt(2024, 1, 1, 12, 0)
    sched._enabled = True

    await sched._async_bootstrap()

    called_serials = {
        call.args[0]
        for method in (
            mock_coordinator.async_write_setting,
            mock_coordinator.async_write_settings,
        )
        for call in method.call_args_list
    }
    assert called_serials == {"MASTER1"}


@pytest.mark.asyncio
async def test_scheduler_writes_only_its_independent_target(
    mock_hass, mock_coordinator
):
    mock_coordinator.write_target_serials = ["INV1", "INV2"]
    sched = _make_scheduler(mock_hass, mock_coordinator, serial="INV2")
    sched._slots[1] = VirtualSlot(
        slot_id=1,
        start="00:00",
        end="23:59",
        mode=MODE_CHARGE,
        current=60,
        target_soc=90,
    )
    sched._now = lambda: _dt(2024, 1, 1, 12, 0)
    sched._enabled = True

    await sched._async_bootstrap()

    called_serials = {
        call.args[0]
        for method in (
            mock_coordinator.async_write_setting,
            mock_coordinator.async_write_settings,
        )
        for call in method.call_args_list
    }
    assert called_serials == {"INV2"}


# ── Idle/charge windows must not starve the house load or zero the SOC ─────
#
# Regression coverage (#21): a discharge slot created at 20:10 for 20:30
# put the idle 00:00-20:30 window on physical slot 1 with sellTime1Pac = 0
# and cap1 = 0. With Use Timer on and Sell unticked, that power value is the
# most the inverter may use for the house, so the house ran from the grid
# until 20:30, and a 0% SOC relied on the inverter's own protection.


def _slot_batch(mock_coordinator, index: int) -> dict[str, object]:
    batches = [
        call.args[1]
        for call in mock_coordinator.async_write_settings.call_args_list
        if f"time{index}on" in call.args[1]
    ]
    return batches[-1]


@pytest.mark.asyncio
async def test_idle_window_before_discharge_keeps_house_on_battery(
    mock_hass, mock_coordinator
):
    mock_coordinator.data["TEST123"]["inverter"] = {"ratePower": 12000}
    mock_coordinator.data["TEST123"]["settings"]["batteryLowCap"] = "15"
    sched = _make_scheduler(mock_hass, mock_coordinator)
    sched._slots[1] = VirtualSlot(
        slot_id=1,
        start="20:30",
        end="21:00",
        mode=MODE_DISCHARGE,
        target_soc=20,
        sell_power=5000,
    )
    sched._now = lambda: _dt(2024, 1, 1, 20, 10)
    sched._enabled = True

    await sched._async_bootstrap()

    idle = _slot_batch(mock_coordinator, 1)
    assert idle["time1on"] == 0
    assert idle["sellTime1"] == "00:00"
    assert idle["sellTime1Pac"] == 12000
    assert idle["cap1"] == 15
    discharge = _slot_batch(mock_coordinator, 6)
    assert discharge["sellTime6"] == "20:30"
    assert discharge["sellTime6Pac"] == 5000
    assert discharge["cap6"] == 20


@pytest.mark.asyncio
async def test_target_soc_below_battery_low_capacity_is_raised(
    mock_hass, mock_coordinator
):
    mock_coordinator.data["TEST123"]["settings"]["batteryLowCap"] = 25
    sched = _make_scheduler(mock_hass, mock_coordinator)
    sched._slots[1] = VirtualSlot(
        slot_id=1,
        start="00:00",
        end="23:59",
        mode=MODE_DISCHARGE,
        target_soc=10,
        sell_power=3000,
    )
    sched._now = lambda: _dt(2024, 1, 1, 12, 0)
    sched._enabled = True

    await sched._async_bootstrap()

    assert _slot_batch(mock_coordinator, 1)["cap1"] == 25


@pytest.mark.asyncio
@pytest.mark.parametrize("low_cap", [None, "bad"])
async def test_unknown_battery_low_capacity_uses_safe_default(
    mock_hass, mock_coordinator, low_cap
):
    if low_cap is not None:
        mock_coordinator.data["TEST123"]["settings"]["batteryLowCap"] = low_cap
    sched = _make_scheduler(mock_hass, mock_coordinator)
    sched._now = lambda: _dt(2024, 1, 1, 12, 0)
    sched._enabled = True

    await sched._async_bootstrap()

    assert (
        _slot_batch(mock_coordinator, 1)["cap1"]
        == VirtualSlotScheduler._DEFAULT_SOC_FLOOR
    )

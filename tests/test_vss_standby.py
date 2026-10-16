"""Virtual Slot Scheduler on with nothing to do leaves the user's timer alone.

With no enabled window and no price override, the scheduler stands by: the
user's own timer stays in place, and is put back when a Tariff Manager
override ends, so Tariff Manager can be used without virtual slots and
without leaving the schedule changed (#21).
"""

from __future__ import annotations

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.sunsynk.virtual_slots import (
    MODE_CHARGE,
    MODE_IDLE,
    Resolution,
    VirtualSlot,
    VirtualSlotScheduler,
)

SERIAL = "INV1"

_USER_TIMER = {
    "cap1": 10,
    "sellTime1Pac": 12000,
    "sellTime1": "00:00",
    "sellTime1En": "0",
    "sellTime2": "05:30",
    "cap6": 10,
    "sellTime6Pac": 6200,
    "sellTime6": "21:00",
    "sellTime6En": "1",
    "time1on": "false",
    "time2on": "false",
    "time3on": "true",
    "time4on": "false",
    "time5on": "false",
    "time6on": "false",
}


def _setup(tariff=None, *, stored=None):
    coordinator = MagicMock()
    coordinator.serials = [SERIAL]
    coordinator.resolve_write_target.side_effect = lambda serial: serial
    coordinator.last_update_success = True
    coordinator.data = {
        SERIAL: {
            "inverter": {"ratePower": 12000},
            "battery": {},
            "settings": {**_USER_TIMER, "batteryLowCap": 10},
        }
    }
    coordinator.async_write_setting = AsyncMock()
    coordinator.async_write_settings = AsyncMock()
    scheduler = VirtualSlotScheduler(
        MagicMock(), coordinator, "entry", SERIAL, tariff_manager=tariff
    )
    scheduler._store = MagicMock(async_save=AsyncMock(), async_load=AsyncMock())
    scheduler._original_settings = stored
    scheduler._enabled = True
    scheduler._now = lambda: datetime(2024, 1, 4, 17, 0)
    return coordinator, scheduler


def _tariff(discharging=False):
    tariff = MagicMock(discharge_min_soc=10, target_soc=90)
    tariff.is_charging_active_for.return_value = False
    tariff.is_discharging_active_for.return_value = discharging
    return tariff


def _writes(coordinator):
    return [call.args[1] for call in coordinator.async_write_settings.call_args_list]


@pytest.mark.asyncio
async def test_enabling_with_nothing_scheduled_writes_nothing():
    coordinator, scheduler = _setup(_tariff())

    await scheduler._async_bootstrap()

    assert _writes(coordinator) == []
    coordinator.async_write_setting.assert_not_called()
    assert scheduler._standing_by is True
    assert scheduler._original_settings == _USER_TIMER


@pytest.mark.asyncio
async def test_a_disabled_window_is_nothing_to_do():
    coordinator, scheduler = _setup(_tariff())
    scheduler._slots[1] = VirtualSlot(
        1, "16:00", "19:00", MODE_CHARGE, target_soc=90, enabled=False
    )

    await scheduler._async_bootstrap()

    assert _writes(coordinator) == []


@pytest.mark.asyncio
async def test_override_takes_the_slots_and_its_end_puts_the_user_timer_back():
    tariff = _tariff()
    coordinator, scheduler = _setup(tariff)
    await scheduler._async_bootstrap()

    tariff.is_discharging_active_for.return_value = True
    await scheduler._async_tick_locked()

    writes = _writes(coordinator)
    assert writes[0] == {"time2on": 0, "time3on": 0, "time4on": 0, "time5on": 0}
    assert any(w.get("sellTime1En") == 1 or w.get("sellTime6En") == 1 for w in writes)
    assert scheduler.active_source == "price_override"
    assert scheduler._standing_by is False

    coordinator.async_write_settings.reset_mock()
    tariff.is_discharging_active_for.return_value = False
    await scheduler._async_tick_locked()

    assert _writes(coordinator) == [_USER_TIMER]
    assert scheduler.active_source == "none"
    assert scheduler.next_boundary is None
    assert scheduler._standing_by is True


@pytest.mark.asyncio
async def test_a_change_made_in_the_app_is_what_comes_back():
    tariff = _tariff()
    coordinator, scheduler = _setup(tariff)
    await scheduler._async_bootstrap()

    # The user edits their timer in the Sunsynk app while VSS stands by.
    coordinator.data[SERIAL]["settings"]["sellTime6Pac"] = 5500
    await scheduler._async_tick_locked()
    assert scheduler._original_settings["sellTime6Pac"] == 5500
    saved = scheduler._store.async_save.call_args.args[0]
    assert saved["original_settings"]["sellTime6Pac"] == 5500

    tariff.is_discharging_active_for.return_value = True
    await scheduler._async_tick_locked()
    coordinator.async_write_settings.reset_mock()
    tariff.is_discharging_active_for.return_value = False
    await scheduler._async_tick_locked()

    assert _writes(coordinator) == [{**_USER_TIMER, "sellTime6Pac": 5500}]


@pytest.mark.asyncio
async def test_settings_from_a_failed_refresh_are_not_captured():
    coordinator, scheduler = _setup(_tariff())
    await scheduler._async_bootstrap()

    coordinator.last_update_success = False
    coordinator.data[SERIAL]["settings"]["cap1"] = 99
    await scheduler._async_tick_locked()

    assert scheduler._original_settings["cap1"] == 10


@pytest.mark.asyncio
async def test_a_failed_put_back_is_retried_on_the_next_update():
    tariff = _tariff(discharging=True)
    coordinator, scheduler = _setup(tariff)
    await scheduler._async_bootstrap()
    tariff.is_discharging_active_for.return_value = False

    coordinator.async_write_settings.reset_mock()
    coordinator.async_write_settings.side_effect = RuntimeError("cloud down")
    await scheduler._async_tick_locked()
    assert scheduler._standing_by is False

    coordinator.async_write_settings.side_effect = None
    coordinator.async_write_settings.reset_mock()
    await scheduler._async_tick_locked()

    assert _writes(coordinator) == [_USER_TIMER]
    assert scheduler._standing_by is True


@pytest.mark.asyncio
async def test_after_a_restart_the_user_timer_is_put_back_first():
    """The inverter may still hold the scheduler's layout from before."""
    stored = {**_USER_TIMER, "cap1": 33}
    coordinator, scheduler = _setup(_tariff(), stored=stored)

    await scheduler._async_bootstrap()

    assert _writes(coordinator) == [stored]
    assert scheduler._original_settings == stored


@pytest.mark.asyncio
async def test_adding_a_window_takes_the_slots_over():
    coordinator, scheduler = _setup(_tariff())
    await scheduler._async_bootstrap()

    scheduler._slots[1] = VirtualSlot(1, "16:00", "19:00", MODE_CHARGE, target_soc=90)
    await scheduler._async_tick_locked()

    writes = _writes(coordinator)
    assert writes[0] == {"time2on": 0, "time3on": 0, "time4on": 0, "time5on": 0}
    assert scheduler.active_source == "virtual_slot:1"


@pytest.mark.asyncio
async def test_without_a_snapshot_standing_by_writes_nothing():
    coordinator, scheduler = _setup(_tariff(), stored={})
    scheduler._standing_by = False

    assert await scheduler._async_stand_by() is True
    assert _writes(coordinator) == []


@pytest.mark.asyncio
async def test_idle_window_restores_both_normal_currents():
    coordinator, scheduler = _setup()
    scheduler._normal_charge_current = 50
    scheduler._normal_discharge_current = 60

    await scheduler._apply_current_if_changed(Resolution(MODE_IDLE, None, None, 0, "none"))

    assert [c.args[1:] for c in coordinator.async_write_setting.call_args_list] == [
        ("chargeCurrent", 50),
        ("dischargeCurrent", 60),
    ]

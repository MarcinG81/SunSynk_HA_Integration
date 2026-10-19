"""Charge straight into discharge, and slots the inverter didn't take (#36).

At 05:30 the cheap period ended and the export price was already high, so
Tariff Manager went from charging to idle to discharging in the same second.
The Virtual Slot Scheduler wrote the idle slots in between and the discharge
slots straight after; the two writes crossed and the discharge slots had no
Sell ticked. The scheduler now waits for Tariff Manager to finish, and writes
a slot again if the inverter doesn't report it as written.
"""

from __future__ import annotations

from datetime import datetime
from unittest.mock import MagicMock

import pytest

from custom_components.sunsynk.virtual_slots import VirtualSlotScheduler
from tests.test_tariff import _make_manager


def _scheduler(mock_hass, mock_coordinator, tariff_manager):
    scheduler = VirtualSlotScheduler(
        hass=mock_hass,
        coordinator=mock_coordinator,
        entry_id="test_entry",
        tariff_manager=tariff_manager,
    )
    scheduler._now = lambda: datetime(2024, 1, 1, 5, 30)
    scheduler._enabled = True
    return scheduler


def _discharge_override():
    tariff = MagicMock(is_busy=False, discharge_min_soc=10)
    tariff.is_charging_active = False
    tariff.is_discharging_active = True
    return tariff


def _writes(coordinator):
    return [call.args[1] for call in coordinator.async_write_settings.call_args_list]


@pytest.mark.asyncio
async def test_nothing_is_written_while_the_tariff_manager_is_busy(
    mock_hass, mock_coordinator
):
    tariff = _discharge_override()
    tariff.is_busy = True
    scheduler = _scheduler(mock_hass, mock_coordinator, tariff)

    await scheduler._async_tick()

    assert _writes(mock_coordinator) == []


@pytest.mark.asyncio
async def test_tariff_manager_is_busy_while_evaluating_and_notifies_when_done(
    mock_hass, mock_coordinator
):
    manager = _make_manager(mock_hass, mock_coordinator)
    seen = []
    manager.async_add_listener(lambda: seen.append(manager.is_busy))

    async def evaluate_now():
        assert manager.is_busy is True

    manager._evaluate_now = evaluate_now
    await manager._evaluate()

    assert manager.is_busy is False
    assert seen == [False]


@pytest.mark.asyncio
async def test_a_lost_sell_flag_is_written_again(mock_hass, mock_coordinator):
    scheduler = _scheduler(mock_hass, mock_coordinator, _discharge_override())
    await scheduler._async_tick()
    mock_coordinator.async_write_settings.reset_mock()

    mock_coordinator.data["TEST123"]["settings"]["sellTime1on"] = "false"
    await scheduler._async_tick()

    (rewrite,) = _writes(mock_coordinator)
    assert rewrite["sellTime1on"] == 1


@pytest.mark.asyncio
async def test_a_slot_reported_as_written_is_left_alone(mock_hass, mock_coordinator):
    scheduler = _scheduler(mock_hass, mock_coordinator, _discharge_override())
    await scheduler._async_tick()
    mock_coordinator.async_write_settings.reset_mock()
    mock_coordinator.data["TEST123"]["settings"].update(
        {"sellTime1on": "true", "time1on": "false"}
    )

    await scheduler._async_tick()

    assert _writes(mock_coordinator) == []


@pytest.mark.asyncio
async def test_a_slot_the_inverter_refuses_is_given_up_after_three_rewrites(
    mock_hass, mock_coordinator, caplog
):
    tariff = _discharge_override()
    scheduler = _scheduler(mock_hass, mock_coordinator, tariff)
    await scheduler._async_tick()
    mock_coordinator.async_write_settings.reset_mock()

    for _ in range(6):
        mock_coordinator.data["TEST123"]["settings"]["sellTime1on"] = "false"
        await scheduler._async_tick()

    assert len(_writes(mock_coordinator)) == 3
    assert caplog.text.count("giving up until the window changes") == 1

    # A new window starts counting again.
    tariff.is_discharging_active = False
    await scheduler._async_tick()
    assert 1 not in scheduler._rewrites

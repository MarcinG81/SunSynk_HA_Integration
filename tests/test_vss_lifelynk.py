"""Virtual Slot Scheduler on a LifeLynk (#51).

A LifeLynk X has four timer slots, not six: `sellTime5` is slot 4's end
(23:59), and there is no cap5/cap6 or sellTime6. Its Grid Charge tick is
`time{n}On`, it has no chargeCurrent / dischargeCurrent, and it refuses a
slot power above 2000 W although rated at 3600 W. Before, the scheduler wrote
six slots at 3600 W and the portal refused the lot.
"""

from __future__ import annotations

import logging
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.sunsynk.virtual_slots import (
    MODE_CHARGE,
    MODE_DISCHARGE,
    VirtualSlot,
    VirtualSlotScheduler,
    _slot_numbers,
)

SERIAL = "LYNK1"

# The reporter's timer, from the #51 diagnostics.
_LIFELYNK_TIMER = {
    **{
        key: value
        for n, start, volt in [
            (1, "00:00", 41),
            (2, "05:30", 41),
            (3, "20:00", 52),
            (4, "22:59", 52),
        ]
        for key, value in {
            f"cap{n}": 100 if n == 1 else 20,
            f"sellTime{n}": start,
            f"sellTime{n}Pac": 2000,
            f"sellTime{n}Volt": volt,
            f"sellTime{n}on": False,
            f"time{n}On": 1 if n == 1 else 0,
        }.items()
    },
    "sellTime5": "23:59",
}


def _setup(now: datetime, *slots: VirtualSlot):
    coordinator = MagicMock()
    coordinator.serials = [SERIAL]
    coordinator.resolve_write_target.side_effect = lambda serial: serial
    coordinator.last_update_success = True
    coordinator.data = {
        SERIAL: {
            "inverter": {"ratePower": 3600},
            "battery": {},
            "settings": {
                **_LIFELYNK_TIMER,
                "batteryLowCap": 20,
                "batteryMaxCurrentCharge": 40,
            },
        }
    }
    coordinator.async_write_setting = AsyncMock()

    async def write(serial, settings):
        coordinator.data[serial]["settings"].update(settings)

    coordinator.async_write_settings = AsyncMock(side_effect=write)
    scheduler = VirtualSlotScheduler(MagicMock(), coordinator, "entry", SERIAL)
    scheduler._store = MagicMock(async_save=AsyncMock(), async_load=AsyncMock())
    scheduler._enabled = True
    scheduler._now = lambda: now
    for slot in slots:
        scheduler._slots[slot.slot_id] = slot
    return coordinator, scheduler


def _written(coordinator) -> dict:
    merged: dict = {}
    for call in coordinator.async_write_settings.call_args_list:
        merged.update(call.args[1])
    return merged


def _charge(start="16:00", end="22:00", current=40):
    return VirtualSlot(1, start, end, MODE_CHARGE, current=current, target_soc=100)


def test_the_slots_are_the_ones_the_inverter_reports():
    assert _slot_numbers(_LIFELYNK_TIMER) == (1, 2, 3, 4)
    assert _slot_numbers({}) == (1, 2, 3, 4, 5, 6)
    # A partial dump is not a model with fewer slots.
    assert _slot_numbers({"cap1": 10}) == (1, 2, 3, 4, 5, 6)


@pytest.mark.asyncio
async def test_a_charge_window_uses_the_four_slots_and_their_names():
    coordinator, scheduler = _setup(datetime(2026, 11, 23, 17, 0), _charge())

    await scheduler._async_bootstrap()

    written = _written(coordinator)
    # Nothing for slots it doesn't have, and slot 4's end left alone.
    assert (
        not {
            "cap5",
            "cap6",
            "sellTime6",
            "sellTime5Pac",
            "sellTime6Pac",
            "sellTime5",
        }
        & written.keys()
    )
    assert not any(key.startswith("time") and key.endswith("on") for key in written)
    # Slot 1 from midnight holds what runs over it, slot 2 the charge.
    assert written["sellTime1"] == "00:00"
    assert written["time1On"] == 0
    assert (written["sellTime2"], written["time2On"], written["cap2"]) == (
        "16:00",
        1,
        100,
    )
    for n in (3, 4):
        assert (written[f"sellTime{n}"], written[f"time{n}On"]) == ("22:00", 0)
    # Never above what its slots take, although rated at 3600 W.
    assert {written[f"sellTime{n}Pac"] for n in range(1, 5)} == {2000}
    assert scheduler.current_physical_slot == 2


@pytest.mark.asyncio
async def test_a_current_it_has_no_setting_for_is_not_sent(caplog):
    coordinator, scheduler = _setup(datetime(2026, 11, 23, 17, 0), _charge())

    with caplog.at_level(logging.WARNING):
        await scheduler._async_bootstrap()
        await scheduler._async_tick_locked()

    coordinator.async_write_setting.assert_not_called()
    assert caplog.text.count("has no chargeCurrent setting") == 1


@pytest.mark.asyncio
async def test_a_current_it_has_is_still_sent():
    coordinator, scheduler = _setup(datetime(2026, 11, 23, 17, 0), _charge())
    coordinator.data[SERIAL]["settings"]["chargeCurrent"] = 40

    await scheduler._async_bootstrap()

    coordinator.async_write_setting.assert_awaited_once_with(
        SERIAL, "chargeCurrent", 40
    )


@pytest.mark.parametrize(
    ("hour", "slot"),
    [(17, 2), (23, 4), (3, 1)],
)
@pytest.mark.asyncio
async def test_the_slot_in_force_is_the_one_covering_now(hour, slot):
    coordinator, scheduler = _setup(datetime(2026, 11, 23, hour, 0), _charge())

    await scheduler._async_bootstrap()

    assert scheduler.current_physical_slot == slot


@pytest.mark.asyncio
async def test_a_window_from_midnight_starts_on_slot_1():
    coordinator, scheduler = _setup(
        datetime(2026, 11, 23, 1, 0), _charge("00:00", "06:00")
    )

    await scheduler._async_bootstrap()

    written = _written(coordinator)
    assert (written["sellTime1"], written["time1On"]) == ("00:00", 1)
    assert (written["sellTime2"], written["time2On"]) == ("00:00", 1)
    assert (written["sellTime3"], written["time3On"]) == ("06:00", 0)


@pytest.mark.asyncio
async def test_a_discharge_above_what_its_slots_take_is_lowered(caplog):
    window = VirtualSlot(
        1, "16:00", "19:00", MODE_DISCHARGE, target_soc=20, sell_power=3000
    )
    coordinator, scheduler = _setup(datetime(2026, 11, 23, 17, 0), window)

    with caplog.at_level(logging.WARNING):
        await scheduler._async_bootstrap()

    written = _written(coordinator)
    assert written["sellTime2Pac"] == 2000
    assert written["sellTime2on"] == 1
    assert "the most this inverter's timer slots take" in caplog.text


@pytest.mark.asyncio
async def test_no_power_in_the_users_timer_leaves_the_slot_power_alone():
    coordinator, scheduler = _setup(datetime(2026, 11, 23, 17, 0), _charge())
    settings = coordinator.data[SERIAL]["settings"]
    for n in range(1, 5):
        settings[f"sellTime{n}Pac"] = "?" if n == 1 else 0

    await scheduler._async_bootstrap()

    assert _written(coordinator)["sellTime2Pac"] == 3600


@pytest.mark.asyncio
async def test_switching_off_puts_back_its_own_grid_charge_ticks():
    coordinator, scheduler = _setup(datetime(2026, 11, 23, 17, 0), _charge())
    await scheduler._async_bootstrap()
    assert scheduler._original_settings["time1On"] == 1

    coordinator.async_write_settings.reset_mock()
    await scheduler._async_shutdown()

    restored = _written(coordinator)
    assert restored["time1On"] == 1
    assert restored["sellTime5"] == "23:59"
    assert restored["sellTime2"] == "05:30"


@pytest.mark.asyncio
async def test_with_nothing_captured_switching_off_clears_its_own_ticks():
    coordinator, scheduler = _setup(datetime(2026, 11, 23, 17, 0))
    scheduler._original_settings = None
    scheduler._had_ownership = True

    await scheduler._async_shutdown()

    assert _written(coordinator) == {f"time{n}On": 0 for n in range(1, 5)}


@pytest.mark.asyncio
async def test_a_missing_grid_charge_tick_is_reported(caplog):
    coordinator, scheduler = _setup(datetime(2026, 11, 23, 17, 0), _charge())
    del coordinator.data[SERIAL]["settings"]["time3On"]

    with caplog.at_level(logging.WARNING):
        await scheduler._async_capture_user_timer()

    assert "cannot capture missing settings, they won't be put back: time3on" in (
        caplog.text
    )

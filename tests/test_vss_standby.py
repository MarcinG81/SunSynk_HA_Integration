"""Virtual Slot Scheduler on with nothing to do leaves the user's timer alone.

With no enabled window and no price override, the scheduler stands by: the
user's own timer stays in place, and is put back when a Tariff Manager
override ends, so Tariff Manager can be used without virtual slots and
without leaving the schedule changed (#21).
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.sunsynk.tariff import TariffChargingManager
from custom_components.sunsynk.virtual_slots import (
    MODE_CHARGE,
    MODE_IDLE,
    Resolution,
    VirtualSlot,
    VirtualSlotScheduler,
)

SERIAL = "INV1"

# The user's own six-slot timer, as a Deye reports it (#36: sell slots 6200 W
# in the evening, house supply from the battery the rest of the day).
_USER_TIMER = {
    **{
        key: value
        for n, start, pac, sell in [
            (1, "00:00", 12000, "0"),
            (2, "05:30", 12000, "0"),
            (3, "09:00", 12000, "0"),
            (4, "16:00", 6200, "1"),
            (5, "19:00", 6200, "1"),
            (6, "21:00", 12000, "0"),
        ]
        for key, value in {
            f"cap{n}": 10,
            f"sellTime{n}Pac": pac,
            f"sellTime{n}": start,
            f"sellTime{n}En": sell,
        }.items()
    },
    **{f"time{n}on": "false" for n in range(1, 7)},
    "time3on": "true",
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

    async def write(serial, settings):
        # As the real coordinator, report what was written until a poll says
        # otherwise.
        coordinator.data[serial]["settings"].update(settings)

    coordinator.async_write_settings = AsyncMock(side_effect=write)
    scheduler = VirtualSlotScheduler(
        MagicMock(), coordinator, "entry", SERIAL, tariff_manager=tariff
    )
    scheduler._store = MagicMock(async_save=AsyncMock(), async_load=AsyncMock())
    scheduler._original_settings = stored
    scheduler._enabled = True
    scheduler._now = lambda: datetime(2024, 1, 4, 17, 0)
    return coordinator, scheduler


def _tariff(discharging=False):
    tariff = MagicMock(discharge_min_soc=10, target_soc=90, is_busy=False)
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
    # Every slot carries the discharge, so none of the user's own start
    # times is left in force, and none has Grid Charge ticked (#36).
    for n in range(1, 7):
        slot = next(w for w in writes if f"time{n}on" in w)
        assert slot[f"time{n}on"] == 0
        assert slot[f"sellTime{n}En"] == 1
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
    # A charge window: Grid Charge ticked on the slots that carry it.
    assert any(w.get("time1on") == 1 or w.get("time6on") == 1 for w in writes)
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


# ── 2.0 audit N3/N4/N5: never drive without a way back ─────────────────────


@pytest.mark.asyncio
async def test_no_takeover_until_the_user_timer_can_be_captured():
    """Enabled before the first poll: nothing to put back, so drive nothing."""
    coordinator, scheduler = _setup(_tariff(discharging=True))
    coordinator.data[SERIAL]["settings"] = {}

    await scheduler._async_bootstrap()
    assert _writes(coordinator) == []
    assert scheduler._original_settings is None

    coordinator.data[SERIAL]["settings"] = dict(_USER_TIMER)
    await scheduler._async_tick_locked()

    assert scheduler._original_settings == _USER_TIMER
    assert scheduler.active_source == "price_override"


@pytest.mark.asyncio
async def test_the_user_timer_is_never_captured_from_a_failed_refresh():
    coordinator, scheduler = _setup(_tariff(discharging=True))
    coordinator.last_update_success = False

    await scheduler._async_bootstrap()

    assert scheduler._original_settings is None
    assert _writes(coordinator) == []


@pytest.mark.asyncio
async def test_removing_a_window_puts_the_normal_current_back():
    coordinator, scheduler = _setup()
    scheduler._normal_charge_current = 50
    scheduler._slots[1] = VirtualSlot(
        1, "16:00", "19:00", MODE_CHARGE, current=100, target_soc=90
    )
    await scheduler._async_bootstrap()
    assert ("chargeCurrent", 100) in [
        c.args[1:] for c in coordinator.async_write_setting.call_args_list
    ]

    coordinator.async_write_setting.reset_mock()
    scheduler._slots.clear()
    await scheduler._async_tick_locked()

    assert [c.args[1:] for c in coordinator.async_write_setting.call_args_list] == [
        ("chargeCurrent", 50)
    ]
    assert scheduler._standing_by is True


@pytest.mark.asyncio
async def test_switching_off_while_standing_by_writes_nothing():
    coordinator, scheduler = _setup(_tariff())
    scheduler._normal_charge_current = 50
    await scheduler._async_bootstrap()

    assert await scheduler.async_shutdown() is True

    assert _writes(coordinator) == []
    coordinator.async_write_setting.assert_not_called()
    assert scheduler._original_settings is None


def test_a_slot_with_no_weekdays_is_nothing_to_do():
    _, scheduler = _setup()
    scheduler._slots[1] = VirtualSlot(
        1, "16:00", "19:00", MODE_CHARGE, weekdays=frozenset(), target_soc=90
    )

    assert scheduler._has_work() is False
    assert scheduler._resolve_virtual(datetime(2024, 1, 4, 12, 0))[2] is None



@pytest.mark.asyncio
async def test_a_capture_from_before_six_slots_is_completed_before_takeover():
    """A beta.14/15 capture only covered slots 1, 2 and 6. Slots 3-5 are
    added from the inverter before they are first written (#36)."""
    old_keys = [k for k in _USER_TIMER if k[-1] in "126" or k in ("sellTime2",)]
    stored = {k: _USER_TIMER[k] for k in old_keys if not any(
        k.endswith(str(n)) or k.startswith(f"time{n}") for n in (3, 4, 5)
    )}
    coordinator, scheduler = _setup(_tariff(discharging=True), stored=dict(stored))
    scheduler._standing_by = True

    await scheduler._async_tick_locked()

    assert scheduler._original_settings == _USER_TIMER
    saved = scheduler._store.async_save.call_args_list[0].args[0]
    assert saved["original_settings"]["sellTime4"] == "16:00"


# ── Charge straight into discharge (#36) ───────────────────────────────────


def _real_tariff(coordinator):
    hass = MagicMock()
    hass.async_create_task.side_effect = lambda coroutine: coroutine.close()
    tariff = TariffChargingManager(
        hass=hass,
        coordinator=coordinator,
        price_entity="sensor.price",
        cheap_threshold=0.10,
        cheap_current=100,
        normal_charge_current=50,
        target_soc=100,
        expensive_threshold=0.16,
        peak_discharge_current=100,
        normal_discharge_current=50,
        discharge_min_soc=10,
        start_hour=None,
        end_hour=None,
    )
    tariff._enabled = True
    return tariff


@pytest.mark.asyncio
async def test_charge_straight_into_discharge_never_puts_the_user_timer_back():
    """At 05:30 the cheap period ended and the export price was already
    high: idle and discharging in the same second. The scheduler acted on
    the idle in between, put the user's timer back and took over again, and
    the two writes crossed: the discharge slots had no Sell ticked."""
    coordinator, scheduler = _setup()
    tariff = _real_tariff(coordinator)
    scheduler._tariff_manager = tariff

    async def write(serial, settings):
        await asyncio.sleep(0)  # a real write lets other tasks run
        coordinator.data[serial]["settings"].update(settings)

    async def write_one(serial, key, value):
        await write(serial, {key: value})

    coordinator.async_write_setting.side_effect = write_one
    coordinator.async_write_settings.side_effect = write
    tasks = []
    scheduler._hass.async_create_task.side_effect = lambda coroutine: tasks.append(
        asyncio.ensure_future(coroutine)
    )
    tariff.async_add_listener(scheduler._on_tick)

    await tariff._evaluate_charging(SERIAL, 0.05, 90, True)
    await scheduler._async_bootstrap()
    assert scheduler.active_source == "price_override"
    await asyncio.gather(*tasks)
    coordinator.async_write_settings.reset_mock()
    tasks.clear()

    async with tariff._evaluate_lock:
        await tariff._evaluate_charging(SERIAL, 0.20, 100, True)
        await tariff._evaluate_discharging(SERIAL, 0.20, 100, True)
        await asyncio.sleep(0)
    tariff._notify_listeners()
    while tasks:
        await tasks.pop(0)

    writes = _writes(coordinator)
    assert _USER_TIMER not in writes
    settings = coordinator.data[SERIAL]["settings"]
    for n in range(1, 7):
        assert settings[f"sellTime{n}En"] == 1
        assert settings[f"time{n}on"] == 0


@pytest.mark.asyncio
async def test_nothing_is_done_while_the_tariff_manager_is_busy():
    tariff = _tariff(discharging=True)
    tariff.is_busy = True
    coordinator, scheduler = _setup(tariff)

    await scheduler._async_bootstrap()

    assert _writes(coordinator) == []
    assert scheduler.active_source == "none"


@pytest.mark.asyncio
async def test_tariff_manager_is_busy_while_evaluating():
    coordinator, _ = _setup()
    tariff = _real_tariff(coordinator)

    assert tariff.is_busy is False
    async with tariff._evaluate_lock:
        assert tariff.is_busy is True


# ── A slot the inverter doesn't report as written is written again (#36) ──


async def _discharging(coordinator, scheduler, tariff):
    tariff.is_discharging_active_for.return_value = True
    await scheduler._async_bootstrap()
    coordinator.async_write_settings.reset_mock()


@pytest.mark.asyncio
async def test_a_lost_sell_flag_is_written_again():
    tariff = _tariff()
    coordinator, scheduler = _setup(tariff)
    await _discharging(coordinator, scheduler, tariff)

    # The inverter reports Sell unticked on slot 3 after the write.
    coordinator.data[SERIAL]["settings"]["sellTime3En"] = "0"
    await scheduler._async_tick_locked()

    (rewrite,) = _writes(coordinator)
    assert rewrite["sellTime3En"] == 1
    assert rewrite["sellTime3Pac"] == 12000


@pytest.mark.asyncio
async def test_a_slot_the_inverter_refuses_is_given_up_after_three_rewrites(caplog):
    tariff = _tariff()
    coordinator, scheduler = _setup(tariff)
    await _discharging(coordinator, scheduler, tariff)

    for _ in range(6):
        coordinator.data[SERIAL]["settings"]["sellTime3En"] = "0"
        await scheduler._async_tick_locked()

    assert len(_writes(coordinator)) == 3
    assert caplog.text.count("giving up until the window changes") == 1


@pytest.mark.asyncio
async def test_a_slot_the_inverter_reports_as_written_is_left_alone():
    tariff = _tariff()
    coordinator, scheduler = _setup(tariff)
    await _discharging(coordinator, scheduler, tariff)
    # Reported in the inverter's own format.
    coordinator.data[SERIAL]["settings"].update(
        {"sellTime3En": "1", "time3on": "false", "cap3": "10.0"}
    )

    await scheduler._async_tick_locked()

    assert _writes(coordinator) == []


@pytest.mark.asyncio
async def test_rewrites_start_again_for_a_new_window():
    tariff = _tariff()
    coordinator, scheduler = _setup(tariff)
    await _discharging(coordinator, scheduler, tariff)
    for _ in range(5):
        coordinator.data[SERIAL]["settings"]["sellTime3En"] = "0"
        await scheduler._async_tick_locked()
    assert scheduler._rewrites[3] == 4

    tariff.is_discharging_active_for.return_value = False
    tariff.is_charging_active_for.return_value = True
    await scheduler._async_tick_locked()

    assert 3 not in scheduler._rewrites


# ── The user's timer put back must be what the inverter has (#36) ──────────


async def _discharge_then_restore_lost(coordinator, scheduler, tariff):
    """A discharge ends, and the write putting the user's timer back is lost:
    the inverter still reports the discharge layout, Sell ticked."""
    await scheduler._async_bootstrap()
    tariff.is_discharging_active_for.return_value = True
    await scheduler._async_tick_locked()
    layout = dict(coordinator.data[SERIAL]["settings"])
    tariff.is_discharging_active_for.return_value = False
    await scheduler._async_tick_locked()
    assert scheduler._standing_by is True
    coordinator.data[SERIAL]["settings"] = layout
    coordinator.async_write_settings.reset_mock()
    return layout


@pytest.mark.asyncio
async def test_a_lost_put_back_is_written_again_and_not_taken_as_the_user_timer():
    tariff = _tariff()
    coordinator, scheduler = _setup(tariff)
    await _discharge_then_restore_lost(coordinator, scheduler, tariff)

    await scheduler._async_tick_locked()

    (rewrite,) = _writes(coordinator)
    assert rewrite["sellTime1En"] == "0"
    assert all(rewrite[f"sellTime{n}En"] == _USER_TIMER[f"sellTime{n}En"]
               for n in range(1, 7) if f"sellTime{n}En" in rewrite)
    assert scheduler._original_settings == _USER_TIMER

    # The inverter now reports the user's timer: done, nothing more written.
    coordinator.async_write_settings.reset_mock()
    await scheduler._async_tick_locked()
    assert _writes(coordinator) == []
    assert scheduler._restore_pending is False
    assert scheduler._written_layout == {}


@pytest.mark.asyncio
async def test_a_put_back_the_inverter_keeps_refusing_raises_a_notification(caplog):
    tariff = _tariff()
    coordinator, scheduler = _setup(tariff)
    layout = await _discharge_then_restore_lost(coordinator, scheduler, tariff)

    for _ in range(6):
        coordinator.data[SERIAL]["settings"] = dict(layout)
        await scheduler._async_tick_locked()

    assert len(_writes(coordinator)) == 3
    assert scheduler._original_settings == _USER_TIMER
    assert caplog.text.count("attempts to put your own timer back") == 1
    notification = scheduler._hass.services.async_call.call_args
    assert notification.args[:2] == ("persistent_notification", "create")
    assert "Sell" in notification.args[2]["message"]


@pytest.mark.asyncio
async def test_a_failed_rewrite_is_tried_again():
    tariff = _tariff()
    coordinator, scheduler = _setup(tariff)
    layout = await _discharge_then_restore_lost(coordinator, scheduler, tariff)
    coordinator.async_write_settings.side_effect = RuntimeError("cloud down")

    await scheduler._async_tick_locked()

    assert scheduler._original_settings == _USER_TIMER
    coordinator.async_write_settings.side_effect = None
    coordinator.data[SERIAL]["settings"] = dict(layout)
    await scheduler._async_tick_locked()
    assert len(_writes(coordinator)) == 2


@pytest.mark.asyncio
async def test_a_change_made_in_the_app_after_the_put_back_is_kept():
    tariff = _tariff()
    coordinator, scheduler = _setup(tariff)
    await _discharge_then_restore_lost(coordinator, scheduler, tariff)
    # Not the scheduler's layout: the user set their own value meanwhile.
    coordinator.data[SERIAL]["settings"] = {
        **_USER_TIMER,
        "sellTime6Pac": 4321,
    }

    await scheduler._async_tick_locked()

    assert _writes(coordinator) == []
    assert scheduler._original_settings["sellTime6Pac"] == 4321
    assert scheduler._restore_pending is False


@pytest.mark.asyncio
async def test_once_put_back_a_later_app_change_is_never_reverted():
    tariff = _tariff()
    coordinator, scheduler = _setup(tariff)
    layout = await _discharge_then_restore_lost(coordinator, scheduler, tariff)
    coordinator.data[SERIAL]["settings"] = dict(_USER_TIMER)
    await scheduler._async_tick_locked()  # the inverter has the user's timer

    # The user then ticks Sell on slot 1, as the scheduler's layout had it.
    coordinator.data[SERIAL]["settings"]["sellTime1En"] = layout["sellTime1En"]
    await scheduler._async_tick_locked()

    assert _writes(coordinator) == []
    assert scheduler._original_settings["sellTime1En"] == layout["sellTime1En"]


@pytest.mark.asyncio
async def test_after_a_restart_its_own_layout_is_still_recognised():
    """HA restarted mid-discharge: the user's timer is put back from the
    store, the write is lost, and the layout read from the store keeps the
    discharge slots from being taken for the user's timer."""
    tariff = _tariff()
    coordinator, scheduler = _setup(tariff)
    await _discharge_then_restore_lost(coordinator, scheduler, tariff)
    saved = scheduler._store.async_save.call_args.args[0]
    assert saved["written_layout"]["sellTime1En"] == 1

    layout = dict(coordinator.data[SERIAL]["settings"])
    coordinator2, restarted = _setup(tariff, stored=dict(_USER_TIMER))
    restarted._store.async_load.return_value = saved
    await restarted.async_load()
    coordinator2.data[SERIAL]["settings"] = dict(layout)

    await restarted._async_bootstrap()  # puts the user's timer back
    coordinator2.data[SERIAL]["settings"] = dict(layout)  # ...and it's lost
    coordinator2.async_write_settings.reset_mock()
    await restarted._async_tick_locked()

    (rewrite,) = _writes(coordinator2)
    assert rewrite["sellTime1En"] == "0"
    assert restarted._original_settings == _USER_TIMER

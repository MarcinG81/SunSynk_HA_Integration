"""Virtual Slot Scheduler discharge windows against the battery and inverter.

A discharge window sets two things: the discharge current (A), which the
battery supplies, and the slot's sell power (W), which the inverter exports.
- Current: never more than the inverter's Max A (set_virtual_slot refuses
  it) or this inverter's share of the battery's BMS limit (lowered when
  written; in a parallel group the inverters' currents add up on one bank).
- Sell power: never more than the inverter's rated power or this inverter's
  share of the maximum export power. It is not capped by the battery: sell
  power also covers solar, and the battery is protected by the current.
"""

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.exceptions import ServiceValidationError

from custom_components.sunsynk import async_setup
from custom_components.sunsynk.const import DOMAIN
from custom_components.sunsynk.coordinator import SunsynkCoordinator
from custom_components.sunsynk.virtual_slots import (
    MODE_DISCHARGE,
    VirtualSlot,
    VirtualSlotScheduler,
)

MASTER = "MASTER"


def _coordinator(inverters: int, *, max_a=240, bms=1040, rated=12000) -> MagicMock:
    """A master plus `inverters - 1` slaves on one battery bank (#21 shape)."""
    slaves = [f"SLAVE{n}" for n in range(1, inverters)]
    coordinator = MagicMock()
    coordinator.serials = [*slaves, MASTER]
    coordinator.resolve_write_target.side_effect = lambda serial: MASTER
    coordinator.data = {
        MASTER: {
            "inverter": {"ratePower": rated},
            "battery": {"dischargeCurrentLimit": bms},
            "settings": {
                "batteryMaxCurrentDischarge": max_a,
                "batteryLowCap": 10,
                "sellTime1En": "0",
                "sellTime6En": "0",
            },
        },
        **{slave: {"battery": {}, "settings": {}} for slave in slaves},
    }
    coordinator.async_write_setting = AsyncMock()
    coordinator.async_write_settings = AsyncMock()
    return coordinator


def _scheduler(coordinator, max_export_power=None) -> VirtualSlotScheduler:
    scheduler = VirtualSlotScheduler(
        MagicMock(), coordinator, "entry", MASTER, max_export_power=max_export_power
    )
    scheduler._store = MagicMock(async_save=AsyncMock(), async_load=AsyncMock())
    scheduler._enabled = True
    scheduler._now = lambda: datetime(2024, 1, 4, 17, 0)  # inside 16:00-19:00
    return scheduler


async def _run_discharge_window(scheduler, current, sell_power):
    scheduler._slots[1] = VirtualSlot(
        1,
        "16:00",
        "19:00",
        MODE_DISCHARGE,
        current=current,
        target_soc=20,
        sell_power=sell_power,
    )
    await scheduler._async_tick_locked()


def _discharge_current(coordinator) -> list[int]:
    return [
        call.args[2]
        for call in coordinator.async_write_setting.call_args_list
        if call.args[1] == "dischargeCurrent"
    ]


def _selling_pac(coordinator) -> int:
    windows = [
        call.args[1]
        for call in coordinator.async_write_settings.call_args_list
        if call.args[1].get("sellTime1En") == 1 or call.args[1].get("sellTime6En") == 1
    ]
    assert len(windows) == 1
    window = windows[0]
    return window.get("sellTime1Pac", window.get("sellTime6Pac"))


# ── A whole tick: current and sell power together ──────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("inverters", "bms", "current", "expected_a"),
    [
        (1, 1040, 240, 240),  # single, well within the BMS
        (1, 150, 240, 150),  # single, above the BMS
        (2, 1040, 240, 240),  # #21: 2 x 240 = 480 A of 1040 A
        (2, 400, 240, 200),  # 2 x 240 = 480 A > 400 A
        (3, 600, 240, 200),
    ],
)
async def test_window_discharge_current_never_exceeds_the_battery(
    inverters, bms, current, expected_a
):
    coordinator = _coordinator(inverters, bms=bms)

    await _run_discharge_window(_scheduler(coordinator), current, 5000)

    assert _discharge_current(coordinator) == [expected_a]
    assert expected_a * inverters <= bms


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("inverters", "rated", "export", "sell_power", "expected_w"),
    [
        (1, 12000, None, 8000, 8000),  # within everything
        (1, 12000, None, 20000, 12000),  # above the inverter: rated power
        (2, 12000, None, 20000, 12000),  # rated power is per inverter
        (1, 12000, 11000, 12000, 11000),  # export limit below rated
        (2, 12000, 11000, 12000, 5500),  # #21: 11 kW shared by two
        (2, 5000, 11000, 12000, 5000),  # rated below the share
    ],
)
async def test_window_sell_power_never_exceeds_inverter_or_export_limit(
    inverters, rated, export, sell_power, expected_w
):
    coordinator = _coordinator(inverters, rated=rated)

    await _run_discharge_window(_scheduler(coordinator, export), 100, sell_power)

    assert _selling_pac(coordinator) == expected_w
    assert expected_w <= rated
    if export:
        assert expected_w * inverters <= export


@pytest.mark.asyncio
async def test_unknown_rated_power_leaves_the_window_sell_power_alone():
    coordinator = _coordinator(1, rated=None)

    await _run_discharge_window(_scheduler(coordinator), 100, 20000)

    assert _selling_pac(coordinator) == 20000


@pytest.mark.asyncio
async def test_sell_power_is_not_capped_by_the_battery():
    """Sell power also covers solar; the battery is protected by the current."""
    coordinator = _coordinator(1, bms=50)

    await _run_discharge_window(_scheduler(coordinator), 100, 8000)

    assert _selling_pac(coordinator) == 8000
    assert _discharge_current(coordinator) == [50]


# ── set_virtual_slot refuses a current above the inverter's Max A ──────────


def _real_coordinator(
    inverters: int, *, max_a: int, rated_bms: int | None, rated_w: int = 12000
):
    coordinator = object.__new__(SunsynkCoordinator)
    slaves = [f"SLAVE{n}" for n in range(1, inverters)]
    coordinator.serials = [*slaves, MASTER]
    coordinator.data = {
        MASTER: {
            "inverter": {"ratePower": rated_w},
            "settings": {
                "parallel": int(inverters > 1),
                "equipMode": 1,
                "batteryMaxCurrentDischarge": max_a,
            },
            "battery": {"maxDischargeCurrentLimit": rated_bms},
        },
        **{slave: {"settings": {"parallel": 1, "equipMode": 0}} for slave in slaves},
    }
    coordinator.async_write_setting = AsyncMock()
    return coordinator


async def _set_virtual_slot(
    inverters,
    *,
    max_a=240,
    rated_bms=None,
    current=100,
    sell_power=5000,
    rated_w=12000,
    max_export_power=None,
):
    coordinator = _real_coordinator(
        inverters, max_a=max_a, rated_bms=rated_bms, rated_w=rated_w
    )
    scheduler = VirtualSlotScheduler(
        MagicMock(), coordinator, "entry", MASTER, max_export_power=max_export_power
    )
    scheduler.async_set_slot = AsyncMock()
    handlers = {}
    hass = MagicMock()
    hass.data = {DOMAIN: {"entry": coordinator, "entry_vslots": {MASTER: scheduler}}}
    hass.http.async_register_static_paths = AsyncMock()
    hass.services.async_register.side_effect = lambda domain, service, handler, schema: (
        handlers.setdefault(service, handler)
    )
    await async_setup(hass, {})
    await handlers["set_virtual_slot"](
        SimpleNamespace(
            data={
                "serial": MASTER,
                "slot_id": 1,
                "start": "16:00",
                "end": "19:00",
                "mode": "discharge",
                "current": current,
                "sell_power": sell_power,
                "priority": 0,
                "enabled": True,
            }
        )
    )
    return scheduler


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("inverters", "max_a", "rated_bms", "current", "message"),
    [
        (1, 120, None, 121, "inverter 120 A"),
        (2, 240, None, 241, "inverter 240 A"),
        (1, 240, 200, 201, "battery BMS 200 A"),
        (2, 240, 400, 201, "battery BMS 400 A shared by 2 inverters = 200 A each"),
    ],
)
async def test_set_virtual_slot_refuses_a_current_above_inverter_or_battery(
    inverters, max_a, rated_bms, current, message
):
    with pytest.raises(ServiceValidationError, match=message):
        await _set_virtual_slot(
            inverters, max_a=max_a, rated_bms=rated_bms, current=current
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("inverters", "max_a", "rated_bms", "current"),
    [
        (1, 120, None, 120),
        (2, 240, None, 240),
        (2, 240, 1040, 240),  # #21
        (2, 240, 400, 200),
    ],
)
async def test_set_virtual_slot_accepts_a_current_within_both(
    inverters, max_a, rated_bms, current
):
    scheduler = await _set_virtual_slot(
        inverters, max_a=max_a, rated_bms=rated_bms, current=current
    )

    scheduler.async_set_slot.assert_awaited_once()


# ── set_virtual_slot refuses more sell power than may be exported ──────────


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("inverters", "rated_w", "export", "sell_power", "message"),
    [
        (1, 12000, None, 12001, "at most 12000 W \\(inverter rated 12000 W\\)"),
        (2, 12000, None, 20000, "at most 12000 W"),  # rated is per inverter
        (1, 12000, 11000, 11001, "maximum export 11000 W\\)"),
        (
            2,
            12000,
            11000,
            6000,
            "maximum export 11000 W shared by 2 inverters = 5500 W each",
        ),
        (2, 5000, 11000, 5001, "at most 5000 W"),  # rated below the share
    ],
)
async def test_set_virtual_slot_refuses_sell_power_above_what_may_be_exported(
    inverters, rated_w, export, sell_power, message
):
    with pytest.raises(ServiceValidationError, match=message):
        await _set_virtual_slot(
            inverters, rated_w=rated_w, max_export_power=export, sell_power=sell_power
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("inverters", "rated_w", "export", "sell_power"),
    [
        (1, 12000, None, 12000),
        (1, 12000, 11000, 11000),
        (2, 12000, 11000, 5500),  # #21: exactly the share
        (2, 0, None, 30000),  # rated power not known yet: nothing to check
    ],
)
async def test_set_virtual_slot_accepts_sell_power_within_the_limits(
    inverters, rated_w, export, sell_power
):
    scheduler = await _set_virtual_slot(
        inverters, rated_w=rated_w, max_export_power=export, sell_power=sell_power
    )

    scheduler.async_set_slot.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["idle", "charge"])
async def test_sell_power_of_non_selling_windows_is_not_checked(mode):
    coordinator = _real_coordinator(2, max_a=240, rated_bms=None)
    scheduler = VirtualSlotScheduler(
        MagicMock(), coordinator, "entry", MASTER, max_export_power=11000
    )
    scheduler.async_set_slot = AsyncMock()
    handlers = {}
    hass = MagicMock()
    hass.data = {DOMAIN: {"entry": coordinator, "entry_vslots": {MASTER: scheduler}}}
    hass.http.async_register_static_paths = AsyncMock()
    hass.services.async_register.side_effect = lambda domain, service, handler, schema: (
        handlers.setdefault(service, handler)
    )
    await async_setup(hass, {})

    await handlers["set_virtual_slot"](
        SimpleNamespace(
            data={
                "serial": MASTER,
                "slot_id": 1,
                "start": "16:00",
                "end": "19:00",
                "mode": mode,
                "sell_power": 30000,
                "priority": 0,
                "enabled": True,
            }
        )
    )

    scheduler.async_set_slot.assert_awaited_once()

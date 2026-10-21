"""2.0 audit of 2026-10-16, findings A2-A7, and the Price Quality start-up gap.

Each test reproduces the audit's scenario. A3 (a settings page rebuilt from
the cache after a failed fresh read) is covered in test_coordinator.py by
test_a_settings_page_is_never_rebuilt_from_the_cache.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.exceptions import ConfigEntryNotReady

from custom_components.sunsynk import async_setup_entry
from custom_components.sunsynk.coordinator import SunsynkCoordinator
from custom_components.sunsynk.current_limits import (
    battery_safe_current,
    current_limit,
    group_sizes,
)
from custom_components.sunsynk.virtual_slots import (
    MODE_CHARGE,
    VirtualSlot,
    VirtualSlotScheduler,
)
from tests.test_integration_lifecycle import _entry, _hass
from tests.test_tariff import _make_manager, _price_state

# ── A2: a failed settings read must not undo a known parallel group ────────


def _group(slave_settings: dict) -> SimpleNamespace:
    coordinator = SimpleNamespace(
        serials=["S", "M"],
        data={
            "M": {
                "settings": {"parallel": 1, "equipMode": 1},
                "battery": {"chargeCurrentLimit": 192},
                "plant": {"id": 7},
            },
            "S": {
                "settings": slave_settings,
                "battery": {},
                "plant": {"id": 7},
            },
        },
    )
    coordinator.resolve_write_target = lambda serial: (
        SunsynkCoordinator.resolve_write_target(coordinator, serial)
    )
    return coordinator


def _plant_id(self, serial):
    return SunsynkCoordinator._plant_id(self, serial)


def test_a2_partial_settings_failure_must_keep_known_parallel_topology():
    coordinator = _group({"parallel": 1, "equipMode": 0})
    coordinator._plant_id = _plant_id
    assert SunsynkCoordinator.write_target_serials.fget(coordinator) == ["M"]
    assert group_sizes(coordinator) == {"M": 2}
    assert battery_safe_current(coordinator, "M", "charge", 120) == 96

    # The next refresh: only the slave's settings read failed.
    coordinator.data["S"]["settings"] = {}

    assert SunsynkCoordinator.write_target_serials.fget(coordinator) == ["M"]
    assert group_sizes(coordinator) == {"M": 2}
    assert battery_safe_current(coordinator, "M", "charge", 120) == 96


def test_a2_a_role_reported_again_is_taken():
    """The memory only fills gaps: valid data always wins."""
    coordinator = _group({"parallel": 1, "equipMode": 0})
    assert SunsynkCoordinator._parallel_role(coordinator, "S") == "slave"

    coordinator.data["S"]["settings"] = {"parallel": 0, "equipMode": 0}

    assert SunsynkCoordinator._parallel_role(coordinator, "S") is None


# ── A4: an override in the other direction mustn't lose a current to put back


@pytest.mark.asyncio
async def test_a4_standby_must_restore_current_owned_before_opposite_tariff_override():
    coordinator = MagicMock()
    coordinator.serials = ["INV1"]
    coordinator.resolve_write_target.side_effect = lambda serial: serial
    coordinator.last_update_success = True
    timer = {"cap1": 10, "sellTime1": "00:00", "sellTime1Pac": 5000, "time1on": "false"}
    coordinator.data = {"INV1": {"inverter": {}, "battery": {}, "settings": dict(timer)}}
    coordinator.async_write_setting = AsyncMock()

    async def write(serial, settings):
        coordinator.data[serial]["settings"].update(settings)

    coordinator.async_write_settings = AsyncMock(side_effect=write)
    tariff = MagicMock(is_busy=False, discharge_min_soc=10, target_soc=90)
    tariff.is_charging_active_for.return_value = False
    tariff.is_discharging_active_for.return_value = False
    scheduler = VirtualSlotScheduler(
        MagicMock(),
        coordinator,
        "entry",
        "INV1",
        tariff_manager=tariff,
        normal_charge_current=50,
    )
    scheduler._store = MagicMock(async_save=AsyncMock())
    scheduler._enabled = True
    scheduler._now = lambda: __import__("datetime").datetime(2024, 1, 1, 12, 0)

    # 1. A charge window sets 100 A (normal 50 A).
    scheduler._slots[1] = VirtualSlot(
        1, "10:00", "18:00", MODE_CHARGE, current=100, target_soc=90
    )
    await scheduler._async_bootstrap()
    assert ("INV1", "chargeCurrent", 100) in [
        c.args for c in coordinator.async_write_setting.call_args_list
    ]
    # 2. A Tariff Manager discharge takes over dischargeCurrent only.
    tariff.is_discharging_active_for.return_value = True
    await scheduler._async_tick_locked()
    # 3. The window is removed; 4. the discharge ends.
    scheduler._slots.clear()
    tariff.is_discharging_active_for.return_value = False
    coordinator.async_write_setting.reset_mock()
    await scheduler._async_tick_locked()

    # 5. Standing by: chargeCurrent goes back to 50 A.
    assert scheduler._standing_by is True
    assert ("INV1", "chargeCurrent", 50) in [
        c.args for c in coordinator.async_write_setting.call_args_list
    ]
    assert scheduler._currents_to_restore == set()


# ── A5: a lower live BMS limit isn't hidden by a higher rated one ───────────


@pytest.mark.parametrize("direction", ["charge", "discharge"])
def test_a5_live_bms_limit_must_not_be_hidden_by_a_higher_rated_limit(direction):
    rated, live = (
        ("maxChargeCurrentLimit", "chargeCurrentLimit")
        if direction == "charge"
        else ("maxDischargeCurrentLimit", "dischargeCurrentLimit")
    )
    data = {"M": {"battery": {rated: 400, live: 40}, "settings": {}}}

    runtime = current_limit(data, ["M"], direction, {"M": 2}, live=True)
    configure = current_limit(data, ["M"], direction, {"M": 2})

    assert runtime.battery == 20  # 40 A for the bank, shared by two
    assert configure.battery == 200  # Configure checks fixed limits only


# ── A6: a restart from standing by takes the timer the inverter has ────────


@pytest.mark.asyncio
async def test_a6_restart_from_standby_must_not_overwrite_timer_edited_while_ha_was_down():
    coordinator = MagicMock()
    coordinator.serials = ["INV1"]
    coordinator.resolve_write_target.side_effect = lambda serial: serial
    coordinator.last_update_success = True
    # Edited in the app while Home Assistant was down: 10 -> 55.
    coordinator.data = {
        "INV1": {"inverter": {}, "battery": {}, "settings": {"cap1": 55, "time1on": "false"}}
    }
    coordinator.async_write_settings = AsyncMock()
    coordinator.async_write_setting = AsyncMock()
    scheduler = VirtualSlotScheduler(MagicMock(), coordinator, "entry", "INV1")
    scheduler._store = MagicMock(
        async_save=AsyncMock(),
        async_load=AsyncMock(
            return_value={
                "slots": [],
                "enabled": True,
                "original_settings": {"cap1": 10, "time1on": "false"},
                "owns_timer": False,  # saved while standing by
            }
        ),
    )
    await scheduler.async_load()
    scheduler._enabled = True

    await scheduler._async_bootstrap()

    coordinator.async_write_settings.assert_not_called()
    assert scheduler._original_settings == {"cap1": 55, "time1on": "false"}


# ── A7: a failed first refresh closes the coordinator's HTTP session ───────


@pytest.mark.asyncio
async def test_a7_first_refresh_failure_must_close_the_owned_http_session():
    coordinator = MagicMock()
    coordinator.async_config_entry_first_refresh = AsyncMock(
        side_effect=ConfigEntryNotReady("cloud down")
    )
    coordinator.async_close = AsyncMock()

    with (
        patch("custom_components.sunsynk.SunsynkCoordinator", return_value=coordinator),
        pytest.raises(ConfigEntryNotReady),
    ):
        await async_setup_entry(_hass(), _entry())

    coordinator.async_close.assert_awaited_once()


# ── Price Quality right after Home Assistant starts ────────────────────────


@pytest.mark.asyncio
async def test_price_quality_follows_the_price_while_tariff_manager_is_off(
    mock_hass, mock_coordinator
):
    """The price helper loads after the integration: quality showed
    "not_found" until Tariff Manager was switched on."""
    mock_hass.states.get.return_value = None
    manager = _make_manager(mock_hass, mock_coordinator)
    manager.start()
    assert manager.price_quality == "not_found"
    assert manager._enabled is False

    mock_hass.states.get.return_value = _price_state("0.20")
    await manager._evaluate()

    assert manager.price_quality == "ok"
    assert manager.export_price_quality == "ok"
    mock_coordinator.async_write_setting.assert_not_called()

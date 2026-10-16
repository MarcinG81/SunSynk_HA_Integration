"""Tariff Manager test mode: decides as usual, writes nothing (#21).

Lets someone see what their configuration would do, in the Tariff Mode
sensor and the log, without any risk to the battery.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.sunsynk.tariff import TariffChargingManager
from custom_components.sunsynk.virtual_slots import VirtualSlotScheduler


def _manager(test_mode: bool):
    hass = MagicMock()
    hass.async_create_task.side_effect = lambda coroutine: coroutine.close()
    coordinator = MagicMock()
    coordinator.serials = ["INV1"]
    coordinator.write_target_serials = ["INV1"]
    coordinator.resolve_write_target.side_effect = lambda serial: serial
    coordinator.data = {"INV1": {"battery": {"soc": 50}, "settings": {}}}
    coordinator.async_write_setting = AsyncMock()
    coordinator.async_write_settings = AsyncMock()
    manager = TariffChargingManager(
        hass=hass,
        coordinator=coordinator,
        price_entity="sensor.price",
        cheap_threshold=0.10,
        cheap_current=100,
        normal_charge_current=50,
        target_soc=90,
        expensive_threshold=0.30,
        peak_discharge_current=100,
        normal_discharge_current=50,
        discharge_min_soc=10,
        test_mode=test_mode,
    )
    manager._enabled = True
    return hass, coordinator, manager


@pytest.mark.asyncio
async def test_test_mode_decides_but_writes_nothing():
    hass, coordinator, manager = _manager(test_mode=True)

    await manager._evaluate_charging("INV1", 0.05, 50, True)
    assert manager.is_charging_active_for("INV1")
    await manager._evaluate_charging("INV1", 0.20, 50, True)
    assert not manager.is_charging_active_for("INV1")
    await manager._evaluate_discharging("INV1", 0.40, 50, True)
    assert manager.is_discharging_active_for("INV1")
    manager._enabled = False
    assert await manager._restore_active_currents_locked() is True

    coordinator.async_write_setting.assert_not_called()
    coordinator.async_write_settings.assert_not_called()
    assert manager.test_mode is True
    titles = [c.args[2]["title"] for c in hass.services.async_call.call_args_list]
    assert titles and all(t.startswith("[Test mode] ") for t in titles)


@pytest.mark.asyncio
async def test_without_test_mode_the_inverter_is_written():
    hass, coordinator, manager = _manager(test_mode=False)

    await manager._evaluate_charging("INV1", 0.05, 50, True)

    coordinator.async_write_setting.assert_awaited_once_with(
        "INV1", "chargeCurrent", 100
    )
    title = hass.services.async_call.call_args.args[2]["title"]
    assert not title.startswith("[Test mode]")
    assert manager.test_mode is False


@pytest.mark.parametrize(("test_mode", "override"), [(True, False), (False, True)])
def test_virtual_slot_scheduler_ignores_a_test_mode_override(test_mode, override):
    tariff = MagicMock(test_mode=test_mode, discharge_min_soc=10)
    tariff.is_charging_active_for.return_value = False
    tariff.is_discharging_active_for.return_value = True
    coordinator = MagicMock(data={"INV1": {"inverter": {}}})
    scheduler = VirtualSlotScheduler(
        MagicMock(), coordinator, "entry", "INV1", tariff_manager=tariff
    )

    assert (scheduler._resolve_override() is not None) is override

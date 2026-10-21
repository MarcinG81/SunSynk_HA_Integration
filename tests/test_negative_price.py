"""Solar Sell off while the export price is below zero (2.1)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.sunsynk.tariff import TariffChargingManager
from tests.test_tariff import _price_state

SERIAL = "TEST123"


def _manager(hass, coordinator, *, protection=True, entry_id="entry"):
    with patch("custom_components.sunsynk.tariff.Store") as store_cls:
        store = MagicMock(async_load=AsyncMock(return_value=None), async_save=AsyncMock())
        store_cls.return_value = store
        manager = TariffChargingManager(
            hass=hass,
            coordinator=coordinator,
            price_entity="sensor.price",
            cheap_threshold=None,
            cheap_current=None,
            normal_charge_current=None,
            target_soc=90,
            expensive_threshold=None,
            peak_discharge_current=None,
            normal_discharge_current=None,
            discharge_min_soc=10,
            negative_price_protection=protection,
            entry_id=entry_id,
        )
    manager._enabled = True
    return manager, store


def _sell(coordinator, value):
    coordinator.data[SERIAL]["settings"]["solarSell"] = value


def _written(coordinator):
    return [c.args[1:] for c in coordinator.async_write_setting.call_args_list]


@pytest.mark.asyncio
async def test_solar_sell_goes_off_below_zero_and_back_on_after(
    mock_hass, mock_coordinator
):
    _sell(mock_coordinator, "1")
    manager, store = _manager(mock_hass, mock_coordinator)

    mock_hass.states.get.return_value = _price_state("-0.05")
    await manager._evaluate()

    assert _written(mock_coordinator) == [("solarSell", 0)]
    assert manager.solar_sell_off == [SERIAL]
    store.async_save.assert_awaited_with({"sell_off": [SERIAL]})

    # Still negative: nothing more written.
    await manager._evaluate()
    assert len(_written(mock_coordinator)) == 1

    mock_hass.states.get.return_value = _price_state("0.02")
    await manager._evaluate()

    assert _written(mock_coordinator)[-1] == ("solarSell", 1)
    assert manager.solar_sell_off == []
    store.async_save.assert_awaited_with({"sell_off": []})


@pytest.mark.asyncio
async def test_solar_sell_the_user_turned_off_is_left_alone(mock_hass, mock_coordinator):
    _sell(mock_coordinator, "0")
    manager, _ = _manager(mock_hass, mock_coordinator)

    mock_hass.states.get.return_value = _price_state("-0.05")
    await manager._evaluate()
    mock_hass.states.get.return_value = _price_state("0.10")
    await manager._evaluate()

    mock_coordinator.async_write_setting.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("protection", [False, True])
async def test_off_unless_turned_on_and_tariff_manager_on(
    mock_hass, mock_coordinator, protection
):
    _sell(mock_coordinator, "1")
    manager, _ = _manager(mock_hass, mock_coordinator, protection=protection)
    manager._enabled = not protection  # one of the two is missing

    mock_hass.states.get.return_value = _price_state("-0.05")
    await manager._evaluate()

    mock_coordinator.async_write_setting.assert_not_called()


@pytest.mark.asyncio
async def test_switching_tariff_manager_off_turns_it_back_on(mock_hass, mock_coordinator):
    _sell(mock_coordinator, "1")
    manager, _ = _manager(mock_hass, mock_coordinator)
    mock_hass.states.get.return_value = _price_state("-0.05")
    await manager._evaluate()

    manager._enabled = False
    await manager._evaluate()

    assert _written(mock_coordinator)[-1] == ("solarSell", 1)


@pytest.mark.asyncio
async def test_no_trustworthy_price_turns_it_back_on(mock_hass, mock_coordinator):
    _sell(mock_coordinator, "1")
    manager, _ = _manager(mock_hass, mock_coordinator)
    mock_hass.states.get.return_value = _price_state("-0.05")
    await manager._evaluate()

    mock_hass.states.get.return_value = None  # price sensor gone
    await manager._evaluate()

    assert _written(mock_coordinator)[-1] == ("solarSell", 1)


@pytest.mark.asyncio
async def test_after_a_restart_it_still_turns_it_back_on(mock_hass, mock_coordinator):
    manager, store = _manager(mock_hass, mock_coordinator)
    store.async_load.return_value = {"sell_off": [SERIAL]}
    await manager.async_load()
    assert manager.solar_sell_off == [SERIAL]

    mock_hass.states.get.return_value = _price_state("0.10")
    await manager._evaluate()

    assert _written(mock_coordinator) == [("solarSell", 1)]


@pytest.mark.asyncio
async def test_failed_writes_are_logged_and_retried(mock_hass, mock_coordinator, caplog):
    _sell(mock_coordinator, "1")
    manager, _ = _manager(mock_hass, mock_coordinator)
    mock_coordinator.async_write_setting.side_effect = RuntimeError("cloud down")

    mock_hass.states.get.return_value = _price_state("-0.05")
    await manager._evaluate()
    assert manager.solar_sell_off == []
    assert "could not turn Solar Sell off" in caplog.text

    mock_coordinator.async_write_setting.side_effect = None
    await manager._evaluate()
    assert manager.solar_sell_off == [SERIAL]

    mock_coordinator.async_write_setting.side_effect = RuntimeError("cloud down")
    mock_hass.states.get.return_value = _price_state("0.10")
    await manager._evaluate()
    assert manager.solar_sell_off == [SERIAL]
    assert "could not turn Solar Sell back on" in caplog.text


@pytest.mark.asyncio
async def test_without_an_entry_nothing_is_saved(mock_hass, mock_coordinator):
    _sell(mock_coordinator, "1")
    manager, store = _manager(mock_hass, mock_coordinator, entry_id="")

    await manager.async_load()
    mock_hass.states.get.return_value = _price_state("-0.05")
    await manager._evaluate()

    assert manager.solar_sell_off == [SERIAL]
    store.async_save.assert_not_called()


def test_the_tariff_mode_sensor_lists_it(mock_hass, mock_coordinator):
    from custom_components.sunsynk.sensor import TariffStateSensor

    manager, _ = _manager(mock_hass, mock_coordinator)
    manager._sell_off_serials.add(SERIAL)

    attrs = TariffStateSensor("entry", manager, MagicMock()).extra_state_attributes

    assert attrs["solar_sell_off"] == [SERIAL]

"""Integration-style lifecycle tests using the smallest useful HA harness."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.sunsynk import (
    PLATFORMS,
    _async_update_listener,
    async_setup_entry,
    async_unload_entry,
)
from custom_components.sunsynk.const import DOMAIN


def _entry(*, dashboard: bool = False) -> MagicMock:
    entry = MagicMock()
    entry.entry_id = "entry-1"
    entry.data = {
        "api_server": "api.sunsynk.net",
        "username": "user@example.com",
        "password": "secret",
        "serials": ["INV1", "INV2"],
        "refresh_interval": 300,
    }
    entry.options = {
        "serials": ["INV1", "INV2"],
        "panel_kwp": 8.0,
        "latitude": 51.0,
        "longitude": -1.0,
        "performance_ratio": 0.8,
        "price_entity": "sensor.import_price",
        "export_price_entity": "sensor.export_price",
        "cheap_threshold": 0.1,
        "cheap_charge_current": 80,
        "normal_charge_current": 40,
        "cheap_target_soc": 90,
        "expensive_threshold": 0.3,
        "peak_discharge_current": 70,
        "normal_discharge_current": 35,
        "discharge_min_soc": 15,
        "tariff_start_hour": 22,
        "tariff_end_hour": 6,
        "price_max_age": 60,
        "create_dashboard": dashboard,
    }
    entry.add_update_listener.return_value = "unsubscribe"
    return entry


def _hass() -> SimpleNamespace:
    return SimpleNamespace(
        data={},
        config=SimpleNamespace(latitude=50.0, longitude=-2.0),
        config_entries=SimpleNamespace(
            async_forward_entry_setups=AsyncMock(),
            async_unload_platforms=AsyncMock(return_value=True),
            async_reload=AsyncMock(),
        ),
        async_create_task=MagicMock(),
    )


@pytest.mark.asyncio
async def test_setup_and_unload_two_independent_inverters_minimal_ha():
    hass = _hass()
    entry = _entry()

    coordinator = MagicMock()
    coordinator.serials = ["INV1", "INV2"]
    coordinator.write_target_serials = ["INV1", "INV2"]
    coordinator.data = {
        "INV1": {"pv": {"etoday": "2.5"}},
        "INV2": {"pv": {"etoday": "bad"}},
    }
    coordinator.async_config_entry_first_refresh = AsyncMock()
    coordinator.async_close = AsyncMock()

    calibrator = MagicMock(async_load=AsyncMock())
    forecast = MagicMock(
        async_config_entry_first_refresh=AsyncMock(), async_close=AsyncMock()
    )
    tariff = MagicMock(async_shutdown=AsyncMock(return_value=True))
    schedulers = [
        MagicMock(async_load=AsyncMock(), async_shutdown=AsyncMock(return_value=True)),
        MagicMock(async_load=AsyncMock(), async_shutdown=AsyncMock(return_value=True)),
    ]
    store = MagicMock(
        async_load=AsyncMock(return_value={"slots": [{"slot_id": 1}]}),
        async_remove=AsyncMock(),
    )

    with (
        patch("custom_components.sunsynk.SunsynkCoordinator", return_value=coordinator),
        patch("custom_components.sunsynk.PerformanceRatioCalibrator", return_value=calibrator),
        patch("custom_components.sunsynk.SolarForecastCoordinator", return_value=forecast) as forecast_cls,
        patch("custom_components.sunsynk.TariffChargingManager", return_value=tariff) as tariff_cls,
        patch("custom_components.sunsynk.VirtualSlotScheduler", side_effect=schedulers) as scheduler_cls,
        patch("custom_components.sunsynk.Store", return_value=store),
        patch("custom_components.sunsynk._migrate_virtual_slot_entity_unique_ids") as migrate,
    ):
        assert await async_setup_entry(hass, entry) is True

    coordinator.async_config_entry_first_refresh.assert_awaited_once()
    calibrator.async_load.assert_awaited_once()
    forecast.async_config_entry_first_refresh.assert_awaited_once()
    actual_energy_fn = forecast_cls.call_args.kwargs["actual_energy_fn"]
    assert actual_energy_fn() == 2.5
    coordinator.data = {}
    assert actual_energy_fn() is None
    tariff_cls.assert_called_once()
    assert scheduler_cls.call_count == 2
    assert {call.kwargs["serial"] for call in scheduler_cls.call_args_list} == {
        "INV1",
        "INV2",
    }
    for scheduler in schedulers:
        scheduler.async_load.assert_awaited_once_with(
            legacy_slots=[{"slot_id": 1}]
        )
        scheduler.start.assert_called_once()
    store.async_remove.assert_awaited_once()
    migrate.assert_called_once_with(hass, "entry-1", "INV1")
    hass.config_entries.async_forward_entry_setups.assert_awaited_once_with(
        entry, PLATFORMS
    )
    tariff.start.assert_called_once()
    hass.async_create_task.assert_not_called()

    assert await async_unload_entry(hass, entry) is True
    tariff.stop.assert_called_once()
    tariff.async_shutdown.assert_awaited_once()
    for scheduler in schedulers:
        scheduler.stop.assert_called_once()
        scheduler.async_shutdown.assert_awaited_once()
    coordinator.async_close.assert_awaited_once()
    forecast.async_close.assert_awaited_once()
    assert hass.data[DOMAIN] == {}


@pytest.mark.asyncio
async def test_setup_survives_initial_forecast_failure_and_starts_opt_in_dashboard():
    hass = _hass()
    entry = _entry(dashboard=True)
    coordinator = MagicMock(
        serials=["INV1"],
        write_target_serials=["INV1"],
        data={"INV1": {"pv": {"etoday": None}}},
        async_config_entry_first_refresh=AsyncMock(),
    )
    forecast = MagicMock(
        async_config_entry_first_refresh=AsyncMock(side_effect=RuntimeError("offline"))
    )
    scheduler = MagicMock(async_load=AsyncMock())

    with (
        patch("custom_components.sunsynk.SunsynkCoordinator", return_value=coordinator),
        patch(
            "custom_components.sunsynk.PerformanceRatioCalibrator",
            return_value=MagicMock(async_load=AsyncMock()),
        ),
        patch("custom_components.sunsynk.SolarForecastCoordinator", return_value=forecast) as forecast_cls,
        patch("custom_components.sunsynk.TariffChargingManager", return_value=MagicMock()),
        patch("custom_components.sunsynk.VirtualSlotScheduler", return_value=scheduler),
        patch("custom_components.sunsynk.Store", return_value=MagicMock(async_load=AsyncMock(return_value=None))),
        patch("custom_components.sunsynk._migrate_virtual_slot_entity_unique_ids"),
        patch("custom_components.sunsynk._async_enable_dashboard_frontend", new=AsyncMock()) as enable_frontend,
        patch("custom_components.sunsynk._async_setup_dashboard", new=AsyncMock()) as setup_dashboard,
    ):
        assert await async_setup_entry(hass, entry) is True

    assert forecast_cls.call_args.kwargs["actual_energy_fn"]() is None
    enable_frontend.assert_awaited_once_with(hass)
    dashboard_coro = hass.async_create_task.call_args.args[0]
    await dashboard_coro
    setup_dashboard.assert_awaited_once_with(hass, entry, coordinator)


@pytest.mark.asyncio
async def test_update_listener_reloads_entry():
    hass = _hass()
    entry = _entry()
    await _async_update_listener(hass, entry)
    hass.config_entries.async_reload.assert_awaited_once_with("entry-1")

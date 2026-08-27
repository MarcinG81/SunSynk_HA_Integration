"""Tests for the Forecast Export Guard."""
from __future__ import annotations

from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.sunsynk.forecast_guard import (
    DEFAULT_MARGIN_PERCENT,
    MODE_HOLDING_BACK,
    MODE_NO_FORECAST,
    MODE_SELLING,
    ForecastExportGuard,
    decide,
)


# ── decide() — pure logic ────────────────────────────────────────────────────


def test_decide_holds_back_when_forecast_at_or_below_needed():
    # 32 kWh capacity, at 50% SOC -> needs 16 kWh to refill. Forecast exactly
    # matches -> not a strict surplus, so hold back (sell only on strictly >).
    result = decide(
        forecast_tomorrow_kwh=16.0,
        battery_capacity_ah=640,
        battery_voltage=50,
        battery_soc=50,
        margin_percent=DEFAULT_MARGIN_PERCENT,
    )
    assert result.mode == MODE_HOLDING_BACK
    assert result.sell is False
    assert result.energy_needed_kwh == 16.0


def test_decide_sells_when_forecast_exceeds_needed():
    result = decide(
        forecast_tomorrow_kwh=20.0,
        battery_capacity_ah=640,
        battery_voltage=50,
        battery_soc=50,
        margin_percent=DEFAULT_MARGIN_PERCENT,
    )
    assert result.mode == MODE_SELLING
    assert result.sell is True


def test_decide_sells_at_full_soc_regardless_of_forecast():
    # SOC=100 -> energy_needed=0 -> any positive forecast clears the
    # threshold. This is the "battery already full" exception, and it
    # falls out of the formula with no special case needed.
    result = decide(
        forecast_tomorrow_kwh=0.5,
        battery_capacity_ah=640,
        battery_voltage=50,
        battery_soc=100,
        margin_percent=DEFAULT_MARGIN_PERCENT,
    )
    assert result.mode == MODE_SELLING
    assert result.energy_needed_kwh == 0.0


def test_decide_margin_makes_it_more_conservative():
    # needed=16 kWh; at 120% margin the sell threshold becomes 19.2 kWh,
    # so an 18 kWh forecast (which would have sold at 100% margin) now
    # holds back instead.
    result = decide(
        forecast_tomorrow_kwh=18.0,
        battery_capacity_ah=640,
        battery_voltage=50,
        battery_soc=50,
        margin_percent=120,
    )
    assert result.mode == MODE_HOLDING_BACK
    assert result.threshold_kwh == pytest.approx(19.2)


def test_decide_missing_forecast_fails_open():
    result = decide(
        forecast_tomorrow_kwh=None,
        battery_capacity_ah=640,
        battery_voltage=50,
        battery_soc=50,
        margin_percent=DEFAULT_MARGIN_PERCENT,
    )
    assert result.mode == MODE_NO_FORECAST
    assert result.sell is True  # fail open — don't hold back on missing data


def test_decide_missing_battery_data_fails_open():
    result = decide(
        forecast_tomorrow_kwh=10.0,
        battery_capacity_ah=None,
        battery_voltage=50,
        battery_soc=50,
        margin_percent=DEFAULT_MARGIN_PERCENT,
    )
    assert result.mode == MODE_NO_FORECAST
    assert result.sell is True


# ── ForecastExportGuard — evaluation wiring ─────────────────────────────────


def _make_guard(mock_hass, mock_coordinator, forecast_data) -> ForecastExportGuard:
    forecast_coordinator = MagicMock()
    forecast_coordinator.data = forecast_data
    return ForecastExportGuard(
        hass=mock_hass,
        coordinator=mock_coordinator,
        forecast_coordinator=forecast_coordinator,
        entry_id="test_entry",
    )


@pytest.mark.asyncio
async def test_evaluate_writes_solar_sell_off_when_holding_back(mock_hass, mock_coordinator):
    mock_coordinator.data = {
        "TEST123": {"battery": {"soc": 50, "capacity": "640", "voltage": "50"}}
    }
    guard = _make_guard(mock_hass, mock_coordinator, {"tomorrow_kwh": 5.0})
    guard._enabled = True

    await guard._async_evaluate()

    mock_coordinator.async_write_setting.assert_awaited_once_with("TEST123", "solarSell", 0)
    assert guard.mode == MODE_HOLDING_BACK


@pytest.mark.asyncio
async def test_evaluate_writes_solar_sell_on_when_selling(mock_hass, mock_coordinator):
    mock_coordinator.data = {
        "TEST123": {"battery": {"soc": 50, "capacity": "640", "voltage": "50"}}
    }
    guard = _make_guard(mock_hass, mock_coordinator, {"tomorrow_kwh": 30.0})
    guard._enabled = True

    await guard._async_evaluate()

    mock_coordinator.async_write_setting.assert_awaited_once_with("TEST123", "solarSell", 1)
    assert guard.mode == MODE_SELLING


@pytest.mark.asyncio
async def test_evaluate_skips_write_when_no_forecast(mock_hass, mock_coordinator):
    mock_coordinator.data = {
        "TEST123": {"battery": {"soc": 50, "capacity": "640", "voltage": "50"}}
    }
    guard = _make_guard(mock_hass, mock_coordinator, {"tomorrow_kwh": None})
    guard._enabled = True

    await guard._async_evaluate()

    mock_coordinator.async_write_setting.assert_not_awaited()
    assert guard.mode == MODE_NO_FORECAST


@pytest.mark.asyncio
async def test_disabled_guard_does_not_evaluate(mock_hass, mock_coordinator):
    guard = _make_guard(mock_hass, mock_coordinator, {"tomorrow_kwh": 5.0})
    guard._enabled = False

    await guard._async_evaluate()

    mock_coordinator.async_write_setting.assert_not_awaited()


@pytest.mark.asyncio
async def test_set_enabled_true_triggers_immediate_evaluation(mock_hass, mock_coordinator):
    mock_coordinator.data = {
        "TEST123": {"battery": {"soc": 50, "capacity": "640", "voltage": "50"}}
    }
    guard = _make_guard(mock_hass, mock_coordinator, {"tomorrow_kwh": 30.0})

    captured = {}

    def _create_task(coro):
        captured["coro"] = coro
        return MagicMock()

    mock_hass.async_create_task = MagicMock(side_effect=_create_task)
    guard.set_enabled(True)
    await captured["coro"]

    mock_coordinator.async_write_setting.assert_awaited_once_with("TEST123", "solarSell", 1)


# ── Evaluation window gating (once per day, before sunrise) ────────────────


def test_evaluation_window_closed_before_offset(mock_hass, mock_coordinator):
    guard = _make_guard(mock_hass, mock_coordinator, {"tomorrow_kwh": 5.0})
    now = datetime(2024, 6, 1, 3, 0)  # well before sunrise
    sunrise = datetime(2024, 6, 1, 5, 0)
    with patch(
        "custom_components.sunsynk.forecast_guard.get_astral_event_next",
        return_value=sunrise - timedelta(minutes=guard.sunrise_offset_minutes),
    ):
        assert guard._evaluation_window_open(now) is False


def test_evaluation_window_open_after_offset(mock_hass, mock_coordinator):
    guard = _make_guard(mock_hass, mock_coordinator, {"tomorrow_kwh": 5.0})
    sunrise = datetime(2024, 6, 1, 5, 0)
    now = datetime(2024, 6, 1, 4, 30)  # within offset window before sunrise
    with patch(
        "custom_components.sunsynk.forecast_guard.get_astral_event_next",
        return_value=sunrise - timedelta(minutes=guard.sunrise_offset_minutes),
    ):
        assert guard._evaluation_window_open(now) is True


def test_evaluation_window_only_opens_once_per_day(mock_hass, mock_coordinator):
    guard = _make_guard(mock_hass, mock_coordinator, {"tomorrow_kwh": 5.0})
    now = datetime(2024, 6, 1, 4, 30)
    guard._last_evaluated_date = now.date()
    with patch(
        "custom_components.sunsynk.forecast_guard.get_astral_event_next",
        return_value=now - timedelta(minutes=1),
    ):
        assert guard._evaluation_window_open(now) is False

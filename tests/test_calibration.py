"""Tests for PerformanceRatioCalibrator's per-month EMA learning."""
from __future__ import annotations

from datetime import date
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.sunsynk.calibration import PerformanceRatioCalibrator


def _make_calibrator(hass, default_ratio=0.80) -> PerformanceRatioCalibrator:
    with patch("custom_components.sunsynk.calibration.Store") as mock_store_cls:
        mock_store_cls.return_value = MagicMock(
            async_load=AsyncMock(return_value=None),
            async_save=AsyncMock(),
        )
        calibrator = PerformanceRatioCalibrator(hass, "entry1", default_ratio)
    return calibrator


def test_get_ratio_returns_default_with_no_samples(mock_hass):
    calibrator = _make_calibrator(mock_hass, default_ratio=0.80)
    assert calibrator.get_ratio(month=7) == 0.80


@pytest.mark.asyncio
async def test_no_calibration_on_first_day_seen(mock_hass):
    calibrator = _make_calibrator(mock_hass)
    await calibrator.async_update(date(2026, 7, 1), raw_kwh_today=10.0, actual_kwh_today=8.0)
    # First call only seeds tracking; no prior day to calibrate against yet.
    assert calibrator.get_ratio(month=7) == 0.80
    calibrator._store.async_save.assert_not_awaited()


@pytest.mark.asyncio
async def test_calibrates_on_day_rollover(mock_hass):
    calibrator = _make_calibrator(mock_hass, default_ratio=0.80)
    await calibrator.async_update(date(2026, 7, 1), raw_kwh_today=10.0, actual_kwh_today=8.0)
    # Rollover to July 2 with the final July-1 reading fed just before it.
    await calibrator.async_update(date(2026, 7, 2), raw_kwh_today=0.0, actual_kwh_today=0.0)

    # First-ever sample for the month is seeded directly at the observed ratio (0.8).
    assert calibrator.get_ratio(month=7) == pytest.approx(0.8)
    calibrator._store.async_save.assert_awaited_once()


@pytest.mark.asyncio
async def test_ema_blends_subsequent_days(mock_hass):
    calibrator = _make_calibrator(mock_hass, default_ratio=0.80)
    # Day 1: observed ratio 0.8 (seeds the month).
    await calibrator.async_update(date(2026, 7, 1), raw_kwh_today=10.0, actual_kwh_today=8.0)
    await calibrator.async_update(date(2026, 7, 2), raw_kwh_today=10.0, actual_kwh_today=6.0)
    # Day 2: observed ratio 0.6, blended via EMA (alpha=0.2) with the seeded 0.8.
    await calibrator.async_update(date(2026, 7, 3), raw_kwh_today=0.0, actual_kwh_today=0.0)

    expected = 0.2 * 0.6 + 0.8 * 0.8
    assert calibrator.get_ratio(month=7) == pytest.approx(expected)


@pytest.mark.asyncio
async def test_skips_calibration_on_negligible_sun_day(mock_hass):
    calibrator = _make_calibrator(mock_hass, default_ratio=0.80)
    await calibrator.async_update(date(2026, 12, 1), raw_kwh_today=0.1, actual_kwh_today=0.05)
    await calibrator.async_update(date(2026, 12, 2), raw_kwh_today=0.0, actual_kwh_today=0.0)

    # raw_kwh (0.1) is below the noise-floor threshold, so December stays uncalibrated.
    assert calibrator.get_ratio(month=12) == 0.80
    calibrator._store.async_save.assert_not_awaited()


@pytest.mark.asyncio
async def test_ratio_clipped_to_upper_bound(mock_hass):
    calibrator = _make_calibrator(mock_hass, default_ratio=0.80)
    await calibrator.async_update(date(2026, 7, 1), raw_kwh_today=10.0, actual_kwh_today=50.0)
    await calibrator.async_update(date(2026, 7, 2), raw_kwh_today=0.0, actual_kwh_today=0.0)

    assert calibrator.get_ratio(month=7) == pytest.approx(1.1)


@pytest.mark.asyncio
async def test_skips_calibration_when_actual_energy_unavailable(mock_hass):
    calibrator = _make_calibrator(mock_hass, default_ratio=0.80)
    await calibrator.async_update(date(2026, 7, 1), raw_kwh_today=10.0, actual_kwh_today=None)
    await calibrator.async_update(date(2026, 7, 2), raw_kwh_today=5.0, actual_kwh_today=4.0)

    # Both calls were skipped (actual_kwh_today was None first, so tracking never started).
    assert calibrator.get_ratio(month=7) == 0.80

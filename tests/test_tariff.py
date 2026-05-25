"""Tests for TariffChargingManager state machine."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

from custom_components.sunsynk.tariff import (
    QUALITY_INVALID,
    QUALITY_NOT_FOUND,
    QUALITY_OK,
    QUALITY_STALE,
    QUALITY_UNAVAILABLE,
    TariffChargingManager,
)


def _make_manager(
    hass,
    coordinator,
    *,
    cheap_threshold=0.10,
    cheap_current=100,
    normal_charge=50,
    target_soc=90,
    expensive_threshold=0.30,
    peak_discharge=100,
    normal_discharge=50,
    discharge_min_soc=10,
    start_hour=None,
    end_hour=None,
    price_max_age=90,
) -> TariffChargingManager:
    return TariffChargingManager(
        hass=hass,
        coordinator=coordinator,
        price_entity="sensor.electricity_price",
        cheap_threshold=cheap_threshold,
        cheap_current=cheap_current,
        normal_charge_current=normal_charge,
        target_soc=target_soc,
        expensive_threshold=expensive_threshold,
        peak_discharge_current=peak_discharge,
        normal_discharge_current=normal_discharge,
        discharge_min_soc=discharge_min_soc,
        start_hour=start_hour,
        end_hour=end_hour,
        price_max_age_minutes=price_max_age,
    )


def _price_state(value: str, age_seconds: int = 60) -> MagicMock:
    state = MagicMock()
    state.state = value
    state.last_updated = datetime.now(tz=timezone.utc) - timedelta(seconds=age_seconds)
    return state


# ── Mode property ────────────────────────────────────────────────────────────


def test_mode_disabled_by_default(mock_hass, mock_coordinator):
    mgr = _make_manager(mock_hass, mock_coordinator)
    assert mgr.mode == "disabled"
    assert not mgr.is_enabled


def test_mode_idle_when_enabled(mock_hass, mock_coordinator):
    mock_hass.states.get.return_value = _price_state("0.20")
    mgr = _make_manager(mock_hass, mock_coordinator)
    mgr._enabled = True
    assert mgr.mode == "idle"


def test_mode_charging(mock_hass, mock_coordinator):
    mgr = _make_manager(mock_hass, mock_coordinator)
    mgr._enabled = True
    mgr._charging_active = True
    assert mgr.mode == "charging"


def test_mode_discharging(mock_hass, mock_coordinator):
    mgr = _make_manager(mock_hass, mock_coordinator)
    mgr._enabled = True
    mgr._discharging_active = True
    assert mgr.mode == "discharging"


# ── Price quality ────────────────────────────────────────────────────────────


def test_quality_not_found_when_entity_missing(mock_hass, mock_coordinator):
    mock_hass.states.get.return_value = None
    mgr = _make_manager(mock_hass, mock_coordinator)
    quality, _ = mgr._compute_price_quality()
    assert quality == QUALITY_NOT_FOUND


def test_quality_unavailable(mock_hass, mock_coordinator):
    state = MagicMock()
    state.state = "unavailable"
    mock_hass.states.get.return_value = state
    mgr = _make_manager(mock_hass, mock_coordinator)
    quality, _ = mgr._compute_price_quality()
    assert quality == QUALITY_UNAVAILABLE


def test_quality_invalid_non_numeric(mock_hass, mock_coordinator):
    mock_hass.states.get.return_value = _price_state("not_a_number")
    mgr = _make_manager(mock_hass, mock_coordinator)
    quality, _ = mgr._compute_price_quality()
    assert quality == QUALITY_INVALID


def test_quality_ok(mock_hass, mock_coordinator):
    mock_hass.states.get.return_value = _price_state("0.15", age_seconds=60)
    mgr = _make_manager(mock_hass, mock_coordinator, price_max_age=90)
    quality, _ = mgr._compute_price_quality()
    assert quality == QUALITY_OK


def test_quality_stale(mock_hass, mock_coordinator):
    mock_hass.states.get.return_value = _price_state("0.15", age_seconds=6000)
    mgr = _make_manager(mock_hass, mock_coordinator, price_max_age=90)
    quality, _ = mgr._compute_price_quality()
    assert quality == QUALITY_STALE


def test_quality_no_age_check_when_max_age_none(mock_hass, mock_coordinator):
    mock_hass.states.get.return_value = _price_state("0.15", age_seconds=99999)
    mgr = _make_manager(mock_hass, mock_coordinator, price_max_age=None)
    quality, _ = mgr._compute_price_quality()
    assert quality == QUALITY_OK


# ── Schedule ────────────────────────────────────────────────────────────────


def test_is_in_schedule_no_restriction(mock_hass, mock_coordinator):
    mgr = _make_manager(mock_hass, mock_coordinator, start_hour=None, end_hour=None)
    assert mgr._is_in_schedule() is True


@pytest.mark.parametrize("hour", [8, 12, 21])
def test_is_in_schedule_normal_range(hour, mock_hass, mock_coordinator):
    mgr = _make_manager(mock_hass, mock_coordinator, start_hour=7, end_hour=22)
    with patch("custom_components.sunsynk.tariff.datetime") as mock_dt:
        mock_dt.now.return_value = MagicMock(hour=hour)
        assert mgr._is_in_schedule() is True


@pytest.mark.parametrize("hour", [5, 6])
def test_is_outside_schedule_normal_range(hour, mock_hass, mock_coordinator):
    mgr = _make_manager(mock_hass, mock_coordinator, start_hour=7, end_hour=22)
    with patch("custom_components.sunsynk.tariff.datetime") as mock_dt:
        mock_dt.now.return_value = MagicMock(hour=hour)
        assert mgr._is_in_schedule() is False


@pytest.mark.parametrize("hour", [22, 23, 0, 3, 5])
def test_is_in_schedule_midnight_wrap(hour, mock_hass, mock_coordinator):
    mgr = _make_manager(mock_hass, mock_coordinator, start_hour=22, end_hour=6)
    with patch("custom_components.sunsynk.tariff.datetime") as mock_dt:
        mock_dt.now.return_value = MagicMock(hour=hour)
        assert mgr._is_in_schedule() is True


@pytest.mark.parametrize("hour", [6, 10, 18, 21])
def test_is_outside_schedule_midnight_wrap(hour, mock_hass, mock_coordinator):
    mgr = _make_manager(mock_hass, mock_coordinator, start_hour=22, end_hour=6)
    with patch("custom_components.sunsynk.tariff.datetime") as mock_dt:
        mock_dt.now.return_value = MagicMock(hour=hour)
        assert mgr._is_in_schedule() is False


# ── Charging evaluation ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_evaluate_starts_charging(mock_hass, mock_coordinator):
    mock_hass.states.get.return_value = _price_state("0.05")
    mock_coordinator.data = {"TEST123": {"battery": {"soc": 50}}}
    mgr = _make_manager(mock_hass, mock_coordinator, cheap_threshold=0.10, target_soc=90)
    mgr._enabled = True
    mgr._price_quality = QUALITY_OK

    await mgr._evaluate_charging("TEST123", price=0.05, soc=50.0, in_schedule=True)

    mock_coordinator.async_write_setting.assert_awaited_once_with(
        "TEST123", "chargeCurrent", 100
    )
    assert mgr._charging_active is True


@pytest.mark.asyncio
async def test_evaluate_does_not_charge_above_threshold(mock_hass, mock_coordinator):
    mgr = _make_manager(mock_hass, mock_coordinator, cheap_threshold=0.10)
    mgr._enabled = True

    await mgr._evaluate_charging("TEST123", price=0.20, soc=50.0, in_schedule=True)

    mock_coordinator.async_write_setting.assert_not_awaited()
    assert mgr._charging_active is False


@pytest.mark.asyncio
async def test_evaluate_does_not_charge_when_soc_at_target(mock_hass, mock_coordinator):
    mgr = _make_manager(mock_hass, mock_coordinator, cheap_threshold=0.10, target_soc=90)
    mgr._enabled = True

    await mgr._evaluate_charging("TEST123", price=0.05, soc=90.0, in_schedule=True)

    mock_coordinator.async_write_setting.assert_not_awaited()
    assert mgr._charging_active is False


@pytest.mark.asyncio
async def test_evaluate_stops_charging_when_price_rises(mock_hass, mock_coordinator):
    mgr = _make_manager(mock_hass, mock_coordinator, cheap_threshold=0.10, normal_charge=50)
    mgr._enabled = True
    mgr._charging_active = True

    await mgr._evaluate_charging("TEST123", price=0.20, soc=50.0, in_schedule=True)

    mock_coordinator.async_write_setting.assert_awaited_once_with(
        "TEST123", "chargeCurrent", 50
    )
    assert mgr._charging_active is False


@pytest.mark.asyncio
async def test_evaluate_stops_charging_outside_schedule(mock_hass, mock_coordinator):
    mgr = _make_manager(mock_hass, mock_coordinator, cheap_threshold=0.10, normal_charge=50)
    mgr._enabled = True
    mgr._charging_active = True

    await mgr._evaluate_charging("TEST123", price=0.05, soc=50.0, in_schedule=False)

    mock_coordinator.async_write_setting.assert_awaited_once_with(
        "TEST123", "chargeCurrent", 50
    )
    assert mgr._charging_active is False


# ── Discharging evaluation ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_evaluate_starts_discharging(mock_hass, mock_coordinator):
    mock_hass.states.get.return_value = _price_state("0.40")
    mgr = _make_manager(
        mock_hass, mock_coordinator, expensive_threshold=0.30, discharge_min_soc=10
    )
    mgr._enabled = True
    mgr._price_quality = QUALITY_OK

    await mgr._evaluate_discharging("TEST123", price=0.40, soc=80.0, in_schedule=True)

    mock_coordinator.async_write_setting.assert_awaited_once_with(
        "TEST123", "dischargeCurrent", 100
    )
    assert mgr._discharging_active is True


@pytest.mark.asyncio
async def test_evaluate_does_not_discharge_below_threshold(mock_hass, mock_coordinator):
    mgr = _make_manager(mock_hass, mock_coordinator, expensive_threshold=0.30)
    mgr._enabled = True

    await mgr._evaluate_discharging("TEST123", price=0.20, soc=80.0, in_schedule=True)

    mock_coordinator.async_write_setting.assert_not_awaited()
    assert mgr._discharging_active is False


@pytest.mark.asyncio
async def test_evaluate_does_not_discharge_at_min_soc(mock_hass, mock_coordinator):
    mgr = _make_manager(
        mock_hass, mock_coordinator, expensive_threshold=0.30, discharge_min_soc=10
    )
    mgr._enabled = True

    await mgr._evaluate_discharging("TEST123", price=0.40, soc=10.0, in_schedule=True)

    mock_coordinator.async_write_setting.assert_not_awaited()
    assert mgr._discharging_active is False


@pytest.mark.asyncio
async def test_evaluate_stops_discharging_when_price_drops(mock_hass, mock_coordinator):
    mgr = _make_manager(
        mock_hass, mock_coordinator, expensive_threshold=0.30, normal_discharge=50
    )
    mgr._enabled = True
    mgr._discharging_active = True

    await mgr._evaluate_discharging("TEST123", price=0.20, soc=50.0, in_schedule=True)

    mock_coordinator.async_write_setting.assert_awaited_once_with(
        "TEST123", "dischargeCurrent", 50
    )
    assert mgr._discharging_active is False


# ── set_enabled ──────────────────────────────────────────────────────────────


def test_set_enabled_false_restores_currents(mock_hass, mock_coordinator):
    tasks = []
    mock_hass.async_create_task = lambda coro: tasks.append(coro)
    mgr = _make_manager(mock_hass, mock_coordinator, normal_charge=50, normal_discharge=50)
    mgr._enabled = True
    mgr._charging_active = True
    mgr._discharging_active = True

    mgr.set_enabled(False)

    assert not mgr.is_enabled
    assert not mgr._charging_active
    assert not mgr._discharging_active
    assert len(tasks) >= 2  # at least charge + discharge restore tasks created


def test_set_enabled_notifies_listeners(mock_hass, mock_coordinator):
    mock_hass.async_create_task = MagicMock()
    mgr = _make_manager(mock_hass, mock_coordinator)
    fired = []
    mgr.async_add_listener(lambda: fired.append(True))

    mgr.set_enabled(False)

    assert len(fired) == 1


def test_listener_unsub(mock_hass, mock_coordinator):
    mock_hass.async_create_task = MagicMock()
    mgr = _make_manager(mock_hass, mock_coordinator)
    fired = []
    unsub = mgr.async_add_listener(lambda: fired.append(True))
    unsub()

    mgr.set_enabled(False)

    assert len(fired) == 0


# ── No-op when thresholds not configured ────────────────────────────────────


@pytest.mark.asyncio
async def test_no_charging_when_threshold_none(mock_hass, mock_coordinator):
    mgr = _make_manager(mock_hass, mock_coordinator, cheap_threshold=None)
    mgr._enabled = True

    await mgr._evaluate_charging("TEST123", price=0.05, soc=50.0, in_schedule=True)

    mock_coordinator.async_write_setting.assert_not_awaited()


@pytest.mark.asyncio
async def test_no_discharging_when_threshold_none(mock_hass, mock_coordinator):
    mgr = _make_manager(mock_hass, mock_coordinator, expensive_threshold=None)
    mgr._enabled = True

    await mgr._evaluate_discharging("TEST123", price=0.50, soc=80.0, in_schedule=True)

    mock_coordinator.async_write_setting.assert_not_awaited()

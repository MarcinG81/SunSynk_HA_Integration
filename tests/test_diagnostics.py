"""Tests for diagnostics.py (custom_components/sunsynk/diagnostics.py)."""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

from custom_components.sunsynk.const import DOMAIN
from custom_components.sunsynk.diagnostics import (
    _safe_data,
    async_get_config_entry_diagnostics,
)


class TestSafeData:
    def test_passes_through_json_safe_scalars(self):
        assert _safe_data("x") == "x"
        assert _safe_data(1) == 1
        assert _safe_data(1.5) == 1.5
        assert _safe_data(True) is True
        assert _safe_data(None) is None

    def test_recurses_into_dicts(self):
        assert _safe_data({"a": {"b": 1}}) == {"a": {"b": 1}}

    def test_recurses_into_lists(self):
        assert _safe_data([1, {"a": 2}, "x"]) == [1, {"a": 2}, "x"]

    def test_stringifies_unknown_objects(self):
        class Weird:
            def __str__(self):
                return "weird-repr"

        assert _safe_data(Weird()) == "weird-repr"

    def test_stringifies_datetime(self):
        dt = datetime(2026, 1, 1, tzinfo=timezone.utc)
        assert _safe_data(dt) == str(dt)


@pytest.fixture
def hass_with_entry():
    hass = MagicMock()
    entry = MagicMock()
    entry.entry_id = "entry1"
    entry.data = {"username": "me@example.com", "password": "secret"}
    entry.options = {}

    coordinator = MagicMock()
    coordinator.last_update_success = True
    coordinator.last_update_success_time = datetime(2026, 1, 1, tzinfo=timezone.utc)
    coordinator.data = {"SN1": {"battery": {"soc": 50}}}

    hass.data = {DOMAIN: {"entry1": coordinator}}
    return hass, entry, coordinator


class TestAsyncGetConfigEntryDiagnostics:
    @pytest.mark.asyncio
    async def test_redacts_password_from_entry_data(self, hass_with_entry):
        hass, entry, _coordinator = hass_with_entry
        result = await async_get_config_entry_diagnostics(hass, entry)
        assert result["entry_data"]["password"] == "**REDACTED**"
        assert result["entry_data"]["username"] == "me@example.com"

    @pytest.mark.asyncio
    async def test_includes_coordinator_status(self, hass_with_entry):
        hass, entry, _coordinator = hass_with_entry
        result = await async_get_config_entry_diagnostics(hass, entry)
        assert result["coordinator"]["last_update_success"] is True
        assert result["coordinator"]["last_update_success_time"] == (
            "2026-01-01T00:00:00+00:00"
        )

    @pytest.mark.asyncio
    async def test_includes_inverter_data(self, hass_with_entry):
        hass, entry, _coordinator = hass_with_entry
        result = await async_get_config_entry_diagnostics(hass, entry)
        assert result["inverter_data"] == {"SN1": {"battery": {"soc": 50}}}

    @pytest.mark.asyncio
    async def test_no_forecast_or_tariff_keys_when_not_configured(self, hass_with_entry):
        hass, entry, _coordinator = hass_with_entry
        result = await async_get_config_entry_diagnostics(hass, entry)
        assert "forecast_data" not in result
        assert "tariff" not in result

    @pytest.mark.asyncio
    async def test_includes_forecast_data_when_configured(self, hass_with_entry):
        hass, entry, _coordinator = hass_with_entry
        forecast_coordinator = MagicMock()
        forecast_coordinator.data = {"today_kwh": 12.3}
        hass.data[DOMAIN]["entry1_forecast"] = forecast_coordinator

        result = await async_get_config_entry_diagnostics(hass, entry)

        assert result["forecast_data"] == {"today_kwh": 12.3}

    @pytest.mark.asyncio
    async def test_includes_tariff_summary_when_configured(self, hass_with_entry):
        hass, entry, _coordinator = hass_with_entry
        tariff_manager = MagicMock()
        tariff_manager.is_enabled = True
        tariff_manager.mode = "charging"
        tariff_manager.price_quality = "ok"
        tariff_manager.price_entity = "sensor.price"
        hass.data[DOMAIN]["entry1_tariff"] = tariff_manager

        result = await async_get_config_entry_diagnostics(hass, entry)

        assert result["tariff"] == {
            "enabled": True,
            "mode": "charging",
            "price_quality": "ok",
            "price_entity": "sensor.price",
        }

    @pytest.mark.asyncio
    async def test_handles_missing_last_update_success_time_attribute(self, hass_with_entry):
        hass, entry, _coordinator = hass_with_entry
        # spec= restricts attribute access to exactly this list, so
        # getattr(..., "last_update_success_time", None) genuinely falls
        # back to the default rather than auto-vivifying a MagicMock —
        # matching a real DataUpdateCoordinator on older HA versions
        # that predate this attribute.
        coordinator = MagicMock(spec=["last_update_success", "data"])
        coordinator.last_update_success = True
        coordinator.data = {}
        hass.data[DOMAIN]["entry1"] = coordinator

        result = await async_get_config_entry_diagnostics(hass, entry)

        assert result["coordinator"]["last_update_success_time"] is None

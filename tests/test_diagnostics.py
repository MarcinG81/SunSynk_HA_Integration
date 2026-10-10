"""Tests for diagnostics.py (custom_components/sunsynk/diagnostics.py)."""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

from custom_components.sunsynk.const import DOMAIN
from custom_components.sunsynk.diagnostics import (
    _redacted_data,
    _replace_sensitive_values,
    _safe_data,
    async_get_config_entry_diagnostics,
)


def test_replace_sensitive_values_handles_lists_and_scalars():
    assert _replace_sensitive_values(["SN1", 7], {"SN1": "inverter_1"}) == [
        "inverter_1",
        7,
    ]


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
    coordinator.serials = ["SN1"]
    coordinator.data = {
        "SN1": {
            "battery": {"soc": 50},
            "inverter": {
                "sn": "SN1",
                "alias": "Alice's garage",
                "plant": {"id": 123, "name": "Alice Home"},
            },
        }
    }

    hass.data = {DOMAIN: {"entry1": coordinator}}
    return hass, entry, coordinator


class TestAsyncGetConfigEntryDiagnostics:
    @pytest.mark.asyncio
    async def test_redacts_password_from_entry_data(self, hass_with_entry):
        hass, entry, _coordinator = hass_with_entry
        result = await async_get_config_entry_diagnostics(hass, entry)
        assert result["entry_data"]["password"] == "**REDACTED**"
        assert result["entry_data"]["username"] == "**REDACTED**"

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
        assert set(result["inverter_data"]) == {"inverter_1"}
        inverter = result["inverter_data"]["inverter_1"]
        assert inverter["battery"] == {"soc": 50}
        assert inverter["inverter"]["sn"] == "**REDACTED**"
        assert inverter["inverter"]["alias"] == "**REDACTED**"
        assert inverter["inverter"]["plant"]["id"] == "**REDACTED**"
        assert inverter["inverter"]["plant"]["name"] == "**REDACTED**"

    @pytest.mark.asyncio
    async def test_adds_serial_found_only_in_raw_data(self, hass_with_entry):
        hass, entry, coordinator = hass_with_entry
        coordinator.data["SN2"] = {"battery": {"soc": 20}}
        result = await async_get_config_entry_diagnostics(hass, entry)
        assert "inverter_2" in result["inverter_data"]

    @pytest.mark.asyncio
    async def test_serial_embedded_in_an_unexpected_string_is_replaced(
        self, hass_with_entry
    ):
        hass, entry, coordinator = hass_with_entry
        coordinator.data["SN1"]["debug_url"] = "https://example.test/inverter/SN1"

        result = await async_get_config_entry_diagnostics(hass, entry)

        assert result["inverter_data"]["inverter_1"]["debug_url"].endswith(
            "/inverter/inverter_1"
        )

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
        tariff_manager.export_price_quality = "ok"
        tariff_manager.export_price_entity = "sensor.price"
        hass.data[DOMAIN]["entry1_tariff"] = tariff_manager

        result = await async_get_config_entry_diagnostics(hass, entry)

        assert result["tariff"] == {
            "enabled": True,
            "mode": "charging",
            "price_quality": "ok",
            "price_entity": "**REDACTED**",
            "export_price_quality": "ok",
            "export_price_entity": "**REDACTED**",
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


@pytest.mark.asyncio
async def test_includes_virtual_slot_schedulers_with_redacted_serials(hass_with_entry):
    """#21: shows which scheduler actually drives each inverter."""
    hass, entry, coordinator = hass_with_entry
    serial = coordinator.serials[0]
    coordinator.write_target_serials = [serial]
    coordinator.resolve_write_target = lambda s: serial
    owner = MagicMock(is_enabled=True, active_source="virtual_slot:1")
    owner.list_slots.return_value = [{"slot_id": 1, "start": "14:30"}]
    stray = MagicMock(is_enabled=False, active_source="none")
    stray.list_slots.return_value = [{"slot_id": 1, "start": "20:30"}]
    hass.data[DOMAIN]["entry1_vslots"] = {serial: owner, "SLAVE9": stray}

    result = await async_get_config_entry_diagnostics(hass, entry)

    vslots = result["virtual_slots"]
    assert vslots["write_targets"] == ["inverter_1"]
    assert vslots["schedulers"]["inverter_1"]["drives_inverter"] is True
    assert vslots["schedulers"]["inverter_1"]["slots"] == [
        {"slot_id": 1, "start": "14:30"}
    ]
    assert vslots["schedulers"]["inverter_unknown"]["drives_inverter"] is False


def test_real_api_payload_identifiers_are_redacted():
    """Field names taken from a real 1.9.8 diagnostics file (#31)."""
    payload = {
        "inverter": {
            "gsn": "E4701",
            "custCode": 29,
            "thumbUrl": "https://example.invalid/plant/1.jpg",
            "user": {"nickname": "owner@example.com", "mobile": "0700"},
            "plant": {"installer": "Solar Ltd", "email": "x@example.com"},
        },
        "plant": {
            "lat": 51.08,
            "lon": -0.72,
            "principal": "Jane Doe",
            "master": {"nickname": "installer@example.com"},
        },
    }

    redacted = _redacted_data(payload, {})

    text = str(redacted)
    for secret in ("E4701", "owner@", "0700", "Solar Ltd", "51.08", "-0.72", "Jane Doe", "installer@", "example.invalid"):
        assert secret not in text

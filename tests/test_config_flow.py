"""Tests for pure/isolated logic in config_flow.py.

Full ConfigFlow/OptionsFlow step methods need the real HA config-entries
flow harness (hass.config_entries.flow.async_init(...)) to exercise
properly — out of scope here. This covers the two pieces of real logic
that don't require that: credential validation and tariff-field parsing.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.sunsynk.api.auth import SunsynkAuthError
from custom_components.sunsynk.config_flow import (
    SunsynkOptionsFlow,
    _async_validate_credentials,
)


def _fake_client_session_cm() -> MagicMock:
    """A fake `async with aiohttp.ClientSession() as session:` — avoids
    _async_validate_credentials opening a real network session, which
    leaves a background thread the HA test harness's strict cleanup
    check (verify_cleanup) flags as a leak even though it's unrelated to
    anything this test actually exercises.
    """
    session = MagicMock()
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=session)
    cm.__aexit__ = AsyncMock(return_value=False)
    client_session_cls = MagicMock(return_value=cm)
    return client_session_cls


class TestAsyncValidateCredentials:
    @pytest.mark.asyncio
    async def test_succeeds_when_token_obtained(self):
        mock_auth = AsyncMock()
        mock_auth.async_get_token = AsyncMock(return_value="token")
        with (
            patch("custom_components.sunsynk.config_flow.SunsynkAuth", return_value=mock_auth),
            patch(
                "custom_components.sunsynk.config_flow.aiohttp.ClientSession",
                _fake_client_session_cm(),
            ),
        ):
            await _async_validate_credentials("api.sunsynk.net", "user", "pass")
        mock_auth.async_get_token.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_propagates_auth_error(self):
        mock_auth = AsyncMock()
        mock_auth.async_get_token = AsyncMock(side_effect=SunsynkAuthError("bad creds"))
        with (
            patch("custom_components.sunsynk.config_flow.SunsynkAuth", return_value=mock_auth),
            patch(
                "custom_components.sunsynk.config_flow.aiohttp.ClientSession",
                _fake_client_session_cm(),
            ),
            pytest.raises(SunsynkAuthError),
        ):
            await _async_validate_credentials("api.sunsynk.net", "user", "wrong")


class TestParseTariffFields:
    def test_empty_input_yields_only_default_price_max_age(self):
        result = SunsynkOptionsFlow._parse_tariff_fields({})
        assert result == {"price_max_age": 90}

    def test_full_cheap_charging_block_parsed(self):
        result = SunsynkOptionsFlow._parse_tariff_fields({
            "cheap_threshold": "0.10",
            "cheap_charge_current": "100",
            "normal_charge_current": "50",
            "cheap_target_soc": 90,
        })
        assert result["cheap_threshold"] == 0.10
        assert result["cheap_charge_current"] == 100
        assert result["normal_charge_current"] == 50
        assert result["cheap_target_soc"] == 90

    def test_cheap_charging_requires_all_three_fields(self):
        with pytest.raises(ValueError, match="Cheap charging requires"):
            SunsynkOptionsFlow._parse_tariff_fields({"cheap_threshold": "0.10"})

    def test_cheap_charging_current_must_be_positive(self):
        with pytest.raises(ValueError, match="must be positive"):
            SunsynkOptionsFlow._parse_tariff_fields({
                "cheap_threshold": "0.10",
                "cheap_charge_current": "0",
                "normal_charge_current": "50",
            })

    def test_full_expensive_discharging_block_parsed(self):
        result = SunsynkOptionsFlow._parse_tariff_fields({
            "expensive_threshold": "0.30",
            "peak_discharge_current": "100",
            "normal_discharge_current": "50",
            "discharge_min_soc": 10,
        })
        assert result["expensive_threshold"] == 0.30
        assert result["peak_discharge_current"] == 100
        assert result["normal_discharge_current"] == 50
        assert result["discharge_min_soc"] == 10

    def test_discharging_requires_all_three_fields(self):
        with pytest.raises(ValueError, match="Discharge requires"):
            SunsynkOptionsFlow._parse_tariff_fields({"peak_discharge_current": "100"})

    def test_discharge_current_must_be_positive(self):
        with pytest.raises(ValueError, match="must be positive"):
            SunsynkOptionsFlow._parse_tariff_fields({
                "expensive_threshold": "0.30",
                "peak_discharge_current": "-5",
                "normal_discharge_current": "50",
            })

    def test_schedule_requires_both_hours(self):
        with pytest.raises(ValueError, match="requires both"):
            SunsynkOptionsFlow._parse_tariff_fields({"tariff_start_hour": "22"})

    def test_schedule_hours_must_be_in_range(self):
        with pytest.raises(ValueError, match="0–23"):
            SunsynkOptionsFlow._parse_tariff_fields({
                "tariff_start_hour": "22",
                "tariff_end_hour": "24",
            })

    def test_valid_schedule_parsed(self):
        result = SunsynkOptionsFlow._parse_tariff_fields({
            "tariff_start_hour": "22",
            "tariff_end_hour": "6",
        })
        assert result["tariff_start_hour"] == 22
        assert result["tariff_end_hour"] == 6

    def test_custom_price_max_age_overrides_default(self):
        result = SunsynkOptionsFlow._parse_tariff_fields({"price_max_age": 30})
        assert result["price_max_age"] == 30

    def test_blank_strings_treated_as_not_set(self):
        result = SunsynkOptionsFlow._parse_tariff_fields({
            "cheap_threshold": "  ",
            "expensive_threshold": "",
        })
        assert "cheap_threshold" not in result
        assert "expensive_threshold" not in result

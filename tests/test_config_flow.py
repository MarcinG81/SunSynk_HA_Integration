"""Tests for pure/isolated logic in config_flow.py.

Full ConfigFlow/OptionsFlow step methods need the real HA config-entries
flow harness (hass.config_entries.flow.async_init(...)) to exercise
properly — out of scope here. This covers the two pieces of real logic
that don't require that: credential validation and tariff-field parsing.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.sunsynk.api.auth import SunsynkAuthError
from custom_components.sunsynk.config_flow import (
    SunsynkConfigFlow,
    SunsynkOptionsFlow,
    _async_validate_credentials,
)


def _user_input(**updates):
    data = {
        "api_server": "api.sunsynk.net",
        "username": "user@example.com",
        "password": "secret",
        "serials": "INV1;INV2",
        "refresh_interval": 300,
    }
    data.update(updates)
    return data


@pytest.mark.asyncio
async def test_config_flow_all_user_outcomes():
    flow = SunsynkConfigFlow()
    assert (await flow.async_step_user())["type"] == "form"
    assert (await flow.async_step_user(_user_input(serials="  ")))["errors"]["serials"] == "invalid_serials"

    with patch(
        "custom_components.sunsynk.config_flow._async_validate_credentials",
        new=AsyncMock(side_effect=SunsynkAuthError("bad")),
    ):
        assert (await flow.async_step_user(_user_input()))["errors"]["base"] == "invalid_auth"
    with patch(
        "custom_components.sunsynk.config_flow._async_validate_credentials",
        new=AsyncMock(side_effect=RuntimeError("offline")),
    ):
        assert (await flow.async_step_user(_user_input()))["errors"]["base"] == "cannot_connect"

    flow.async_set_unique_id = AsyncMock()
    flow._abort_if_unique_id_configured = MagicMock()
    with patch(
        "custom_components.sunsynk.config_flow._async_validate_credentials",
        new=AsyncMock(),
    ):
        result = await flow.async_step_user(_user_input())
    assert result["type"] == "create_entry"
    assert result["data"]["serials"] == ["INV1", "INV2"]


def _options_flow():
    entry = MagicMock()
    entry.entry_id = "entry"
    entry.unique_id = "api.sunsynk.net_user@example.com"
    entry.data = _user_input(serials=["INV1", "INV2"])
    entry.options = {}
    flow = SunsynkOptionsFlow(entry)
    flow.hass = SimpleNamespace(
        config=SimpleNamespace(latitude=51.0, longitude=-1.0),
        config_entries=MagicMock(),
    )
    flow.hass.config_entries.async_entries.return_value = []
    return flow


def test_config_flow_builds_options_flow():
    entry = MagicMock()
    assert isinstance(
        SunsynkConfigFlow.async_get_options_flow(entry), SunsynkOptionsFlow
    )


@pytest.mark.asyncio
async def test_options_flow_form_validation_and_success_paths():
    flow = _options_flow()
    assert (await flow.async_step_init())["type"] == "form"

    base = _user_input(password="")
    missing_credentials = {**base, "username": "", "password": ""}
    assert (
        await flow.async_step_init(missing_credentials)
    )["errors"]["base"] == "invalid_auth"
    assert (await flow.async_step_init({**base, "serials": ""}))["errors"]["serials"] == "invalid_serials"
    assert (await flow.async_step_init({**base, "panel_kwp": "bad"}))["errors"]["base"] == "invalid_forecast_config"
    for overrides in (
        {"panel_kwp": "nan"},
        {"panel_kwp": "8", "latitude": "91"},
        {"panel_kwp": "8", "longitude": "-181"},
        {"panel_kwp": "8", "performance_ratio": "inf"},
        {"panel_kwp": "8", "performance_ratio": "1.01"},
    ):
        result = await flow.async_step_init({**base, **overrides})
        assert result["errors"]["base"] == "invalid_forecast_config"
    assert (
        await flow.async_step_init(
            {**base, "price_entity": "sensor.price", "cheap_threshold": "0.1"}
        )
    )["errors"]["base"] == "invalid_tariff_config"

    full = {
        **base,
        "panel_kwp": "8",
        "latitude": "",
        "longitude": "",
        "performance_ratio": "",
        "price_entity": "sensor.buy",
        "export_price_entity": "sensor.sell",
        "cheap_threshold": "0.1",
        "cheap_charge_current": "80",
        "normal_charge_current": "40",
        "expensive_threshold": "0.3",
        "peak_discharge_current": "70",
        "normal_discharge_current": "35",
        "cheap_target_soc": 90,
        "discharge_min_soc": 10,
        "price_max_age": 30,
        "create_dashboard": True,
    }
    result = await flow.async_step_init(full)
    assert result["type"] == "create_entry"
    assert result["data"]["create_dashboard"] is True


@pytest.mark.asyncio
async def test_options_flow_credential_change_outcomes():
    flow = _options_flow()
    changed = _user_input(username="new@example.com", password="new-secret")
    duplicate = MagicMock(entry_id="other", unique_id="api.sunsynk.net_new@example.com")
    flow.hass.config_entries.async_entries.return_value = [duplicate]
    assert (await flow.async_step_init(changed))["errors"]["base"] == "already_configured"

    flow.hass.config_entries.async_entries.return_value = []
    with patch(
        "custom_components.sunsynk.config_flow._async_validate_credentials",
        new=AsyncMock(side_effect=SunsynkAuthError("bad")),
    ):
        assert (await flow.async_step_init(changed))["errors"]["base"] == "invalid_auth"
    with patch(
        "custom_components.sunsynk.config_flow._async_validate_credentials",
        new=AsyncMock(side_effect=RuntimeError("offline")),
    ):
        assert (await flow.async_step_init(changed))["errors"]["base"] == "cannot_connect"
    with patch(
        "custom_components.sunsynk.config_flow._async_validate_credentials",
        new=AsyncMock(),
    ):
        result = await flow.async_step_init(changed)
    assert result["type"] == "create_entry"
    flow.hass.config_entries.async_update_entry.assert_called_once()


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

    @pytest.mark.parametrize("value", ["nan", "inf", "-inf"])
    def test_price_threshold_must_be_finite(self, value):
        with pytest.raises(ValueError, match="must be finite"):
            SunsynkOptionsFlow._parse_tariff_fields({
                "cheap_threshold": value,
                "cheap_charge_current": "50",
                "normal_charge_current": "50",
            })

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

    def test_cheap_charging_current_has_safe_upper_limit(self):
        with pytest.raises(ValueError, match="no greater than 300"):
            SunsynkOptionsFlow._parse_tariff_fields({
                "cheap_threshold": "0.10",
                "cheap_charge_current": "301",
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

    def test_discharge_current_has_safe_upper_limit(self):
        with pytest.raises(ValueError, match="no greater than 300"):
            SunsynkOptionsFlow._parse_tariff_fields({
                "expensive_threshold": "0.30",
                "peak_discharge_current": "301",
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

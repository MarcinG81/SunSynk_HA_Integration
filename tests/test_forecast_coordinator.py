"""Tests for the Open-Meteo forecast coordinator."""
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.helpers.update_coordinator import UpdateFailed

from custom_components.sunsynk.coordinator import SolarForecastCoordinator
from tests.conftest import FakeResponse, fake_session


def _coordinator(mock_hass, **kwargs):
    with patch("homeassistant.helpers.frame.report_usage", create=True):
        return SolarForecastCoordinator(
            mock_hass,
            latitude=51.0,
            longitude=-1.0,
            panel_kwp=2.0,
            performance_ratio=0.5,
            **kwargs,
        )


@pytest.mark.parametrize(
    "overrides",
    [
        {"latitude": 91},
        {"longitude": 181},
        {"panel_kwp": 0},
        {"performance_ratio": 0},
        {"performance_ratio": 1.1},
    ],
)
def test_rejects_invalid_configuration(mock_hass, overrides):
    values = {
        "latitude": 51.0,
        "longitude": -1.0,
        "panel_kwp": 2.0,
        "performance_ratio": 0.8,
        **overrides,
    }
    with pytest.raises(ValueError, match="Invalid solar forecast"):
        SolarForecastCoordinator(mock_hass, **values)


@pytest.mark.asyncio
async def test_parse_uses_forecast_timezone_and_calibrator(mock_hass):
    calibrator = MagicMock(
        get_ratio=MagicMock(return_value=0.75),
        async_update=AsyncMock(),
    )
    coordinator = _coordinator(
        mock_hass, calibrator=calibrator, actual_energy_fn=lambda: 1.25
    )
    raw = {
        "utc_offset_seconds": 7200,
        "hourly": {
            "time": ["bad", "2026-07-02T01:00", "2026-07-03T01:00"],
            "shortwave_radiation": [999, 1000, 500],
            "direct_normal_irradiance": [1, 600, 300],
            "cloud_cover": [1, 20, 30],
            "precipitation": [1, 0.4, 0.2],
        },
    }

    with patch(
        "custom_components.sunsynk.coordinator.dt_util.now",
        return_value=datetime(2026, 7, 1, 23, 15, tzinfo=timezone.utc),
    ):
        result = await coordinator._parse(raw)

    assert result == {
        "today_kwh": 1.5,
        "tomorrow_kwh": 0.75,
        "cloud_cover": 20.0,
        "precipitation": 0.4,
        "ghi": 1000.0,
        "dni": 600.0,
        "performance_ratio": 0.75,
    }
    calibrator.async_update.assert_awaited_once()


@pytest.mark.asyncio
async def test_parse_handles_missing_current_hour_and_invalid_offset(mock_hass):
    coordinator = _coordinator(mock_hass)
    with patch(
        "custom_components.sunsynk.coordinator.dt_util.now",
        return_value=datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc),
    ):
        result = await coordinator._parse(
            {
                "utc_offset_seconds": "bad",
                "hourly": {
                    "time": ["2026-07-01T13:00"],
                    "shortwave_radiation": [None],
                    "direct_normal_irradiance": [],
                    "cloud_cover": [],
                    "precipitation": [],
                },
            }
        )
    assert result["today_kwh"] == 0
    assert result["cloud_cover"] is None


@pytest.mark.asyncio
async def test_update_fetches_and_parses_response(mock_hass):
    coordinator = _coordinator(mock_hass)
    coordinator._session = fake_session(
        get=FakeResponse({"hourly": {"time": []}})
    )
    coordinator._session.closed = False
    result = await coordinator._async_update_data()
    assert result["today_kwh"] == 0


@pytest.mark.asyncio
async def test_update_wraps_request_failure(mock_hass):
    coordinator = _coordinator(mock_hass)
    coordinator._session = fake_session(get=FakeResponse({}, status=500))
    coordinator._session.closed = False
    with pytest.raises(UpdateFailed, match="Open-Meteo request failed"):
        await coordinator._async_update_data()


@pytest.mark.asyncio
async def test_session_creation_and_close(mock_hass):
    coordinator = _coordinator(mock_hass)
    session = MagicMock(closed=False, close=AsyncMock())
    with patch(
        "custom_components.sunsynk.coordinator.aiohttp.ClientSession",
        return_value=session,
    ):
        assert await coordinator._async_get_session() is session
        assert await coordinator._async_get_session() is session
    await coordinator.async_close()
    session.close.assert_awaited_once()

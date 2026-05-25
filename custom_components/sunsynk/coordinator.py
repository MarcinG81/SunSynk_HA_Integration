"""DataUpdateCoordinator for Sunsynk integration."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any

import aiohttp
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api.auth import SunsynkAuth, SunsynkAuthError
from .api.client import SunsynkApiError, SunsynkClient
from .const import (
    BATTERY_SETTING_KEYS,
    DOMAIN,
    SOLAR_FORECAST_UPDATE_INTERVAL,
    SYSTEM_MODE_SETTING_KEYS,
)

_LOGGER = logging.getLogger(__name__)


class SunsynkCoordinator(DataUpdateCoordinator[dict[str, dict[str, Any]]]):
    """Manages data fetching for all inverters in one config entry."""

    def __init__(
        self,
        hass: HomeAssistant,
        auth: SunsynkAuth,
        serials: list[str],
        refresh_interval: int,
        entry_id: str = "",
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=refresh_interval),
        )
        self._auth = auth
        self.serials = serials
        self._entry_id = entry_id
        self._session: aiohttp.ClientSession | None = None

    async def _async_get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session

    async def _async_update_data(self) -> dict[str, dict[str, Any]]:
        """Fetch data from all inverter endpoints."""
        session = await self._async_get_session()

        try:
            token = await self._auth.async_get_token(session)
        except SunsynkAuthError as err:
            ir.async_create_issue(
                self.hass,
                DOMAIN,
                f"auth_failed_{self._entry_id}",
                is_fixable=False,
                severity=ir.IssueSeverity.ERROR,
                translation_key="auth_failed",
            )
            raise UpdateFailed(f"Authentication failed: {err}") from err

        ir.async_delete_issue(self.hass, DOMAIN, f"auth_failed_{self._entry_id}")

        client = SunsynkClient(self._auth._api_server, token)

        result: dict[str, dict[str, Any]] = {}
        for serial in self.serials:
            try:
                result[serial] = await client.async_fetch_all(session, serial)
                _LOGGER.debug("Data updated for inverter %s", serial)
                ir.async_delete_issue(self.hass, DOMAIN, f"inverter_offline_{serial}")
            except Exception as err:  # noqa: BLE001
                _LOGGER.error("Failed to fetch data for inverter %s: %s", serial, err)
                result[serial] = self.data.get(serial, {}) if self.data else {}
                ir.async_create_issue(
                    self.hass,
                    DOMAIN,
                    f"inverter_offline_{serial}",
                    is_fixable=False,
                    severity=ir.IssueSeverity.WARNING,
                    translation_key="inverter_offline",
                    translation_placeholders={"serial": serial},
                )

        return result

    async def async_write_setting(
        self, serial: str, setting_key: str, value: Any
    ) -> None:
        """Write a single setting to the inverter."""
        session = await self._async_get_session()

        try:
            token = await self._auth.async_get_token(session)
        except SunsynkAuthError as err:
            raise UpdateFailed(f"Authentication failed: {err}") from err

        client = SunsynkClient(self._auth._api_server, token)

        current_settings = (self.data or {}).get(serial, {}).get("settings", {})
        if not current_settings:
            try:
                current_settings = await client.async_get_settings(session, serial)
            except SunsynkApiError as err:
                raise UpdateFailed(f"Cannot read settings for {serial}: {err}") from err

        if setting_key in BATTERY_SETTING_KEYS:
            allowed_keys = BATTERY_SETTING_KEYS
        elif setting_key in SYSTEM_MODE_SETTING_KEYS:
            allowed_keys = SYSTEM_MODE_SETTING_KEYS
        else:
            allowed_keys = frozenset([setting_key])

        payload: dict[str, Any] = {
            k: v for k, v in current_settings.items() if k in allowed_keys
        }
        payload[setting_key] = value
        payload["sn"] = serial

        try:
            await client.async_write_settings(session, serial, payload)
        except SunsynkApiError as err:
            raise UpdateFailed(f"Failed to write setting {setting_key}: {err}") from err

        await self.async_request_refresh()

    async def async_close(self) -> None:
        """Close the aiohttp session."""
        if self._session and not self._session.closed:
            await self._session.close()


class SolarForecastCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Fetches solar irradiance and weather forecast from Open-Meteo (no API key)."""

    _URL = "https://api.open-meteo.com/v1/forecast"

    def __init__(
        self,
        hass: HomeAssistant,
        latitude: float,
        longitude: float,
        panel_kwp: float,
        performance_ratio: float,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}_forecast",
            update_interval=timedelta(minutes=SOLAR_FORECAST_UPDATE_INTERVAL),
        )
        self._latitude = latitude
        self._longitude = longitude
        self._panel_kwp = panel_kwp
        self._performance_ratio = performance_ratio
        self._session: aiohttp.ClientSession | None = None

    async def _async_get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session

    async def _async_update_data(self) -> dict[str, Any]:
        session = await self._async_get_session()
        params = {
            "latitude": self._latitude,
            "longitude": self._longitude,
            "hourly": "shortwave_radiation,direct_normal_irradiance,cloud_cover,precipitation",
            "forecast_days": 2,
            "timezone": "auto",
        }
        try:
            async with session.get(
                self._URL,
                params=params,
                timeout=aiohttp.ClientTimeout(total=30),
            ) as resp:
                resp.raise_for_status()
                raw = await resp.json()
        except Exception as err:
            raise UpdateFailed(f"Open-Meteo request failed: {err}") from err

        return self._parse(raw)

    def _parse(self, raw: dict[str, Any]) -> dict[str, Any]:
        hourly = raw.get("hourly", {})
        times: list[str] = hourly.get("time", [])
        ghi: list[Any] = hourly.get("shortwave_radiation", [])
        dni: list[Any] = hourly.get("direct_normal_irradiance", [])
        cloud: list[Any] = hourly.get("cloud_cover", [])
        precip: list[Any] = hourly.get("precipitation", [])

        now = datetime.now()
        today = now.date()
        tomorrow = today + timedelta(days=1)

        today_kwh = 0.0
        tomorrow_kwh = 0.0
        for i, ts in enumerate(times):
            try:
                dt = datetime.fromisoformat(ts)
            except ValueError:
                continue
            g = float(ghi[i]) if i < len(ghi) and ghi[i] is not None else 0.0
            contribution = g / 1000.0 * self._panel_kwp * self._performance_ratio
            if dt.date() == today:
                today_kwh += contribution
            elif dt.date() == tomorrow:
                tomorrow_kwh += contribution

        current_hour_str = now.strftime("%Y-%m-%dT%H:00")
        idx: int | None = None
        for i, ts in enumerate(times):
            if ts == current_hour_str:
                idx = i
                break

        def _val(lst: list[Any], i: int | None) -> float | None:
            if i is None or i >= len(lst) or lst[i] is None:
                return None
            return round(float(lst[i]), 1)

        return {
            "today_kwh": round(today_kwh, 2),
            "tomorrow_kwh": round(tomorrow_kwh, 2),
            "cloud_cover": _val(cloud, idx),
            "precipitation": _val(precip, idx),
            "ghi": _val(ghi, idx),
            "dni": _val(dni, idx),
        }

    async def async_close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()

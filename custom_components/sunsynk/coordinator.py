"""DataUpdateCoordinator for Sunsynk integration."""
from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

import aiohttp
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api.auth import SunsynkAuth, SunsynkAuthError
from .api.client import SunsynkApiError, SunsynkClient
from .const import BATTERY_SETTING_KEYS, DOMAIN, SYSTEM_MODE_SETTING_KEYS

_LOGGER = logging.getLogger(__name__)


class SunsynkCoordinator(DataUpdateCoordinator[dict[str, dict[str, Any]]]):
    """Manages data fetching for all inverters in one config entry."""

    def __init__(
        self,
        hass: HomeAssistant,
        auth: SunsynkAuth,
        serials: list[str],
        refresh_interval: int,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=refresh_interval),
        )
        self._auth = auth
        self.serials = serials
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
            raise UpdateFailed(f"Authentication failed: {err}") from err

        client = SunsynkClient(self._auth._api_server, token)

        result: dict[str, dict[str, Any]] = {}
        for serial in self.serials:
            try:
                result[serial] = await client.async_fetch_all(session, serial)
                _LOGGER.debug("Data updated for inverter %s", serial)
            except Exception as err:  # noqa: BLE001
                _LOGGER.error("Failed to fetch data for inverter %s: %s", serial, err)
                result[serial] = self.data.get(serial, {}) if self.data else {}

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

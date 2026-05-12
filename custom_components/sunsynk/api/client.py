"""Sunsynk API client - async, fetches all inverter data endpoints."""
from __future__ import annotations

import asyncio
import logging
from datetime import date
from typing import Any

import aiohttp

_LOGGER = logging.getLogger(__name__)


class SunsynkApiError(Exception):
    """Raised when an API call fails."""


class SunsynkClient:
    """Async client for the Sunsynk cloud API."""

    def __init__(self, api_server: str, token: str) -> None:
        self._base = f"https://{api_server}"
        self._token = token

    def _headers(self) -> dict[str, str]:
        return {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self._token}",
        }

    async def _get(
        self, session: aiohttp.ClientSession, url: str, params: dict | None = None
    ) -> dict[str, Any]:
        try:
            async with session.get(
                url,
                headers=self._headers(),
                params=params,
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                resp.raise_for_status()
                data = await resp.json()

            if data.get("msg") != "Success":
                raise SunsynkApiError(f"API error: {data.get('msg')} for {url}")

            return data.get("data", {})

        except aiohttp.ClientResponseError as err:
            raise SunsynkApiError(f"HTTP {err.status} for {url}") from err
        except aiohttp.ClientError as err:
            raise SunsynkApiError(f"Connection error for {url}: {err}") from err

    async def _post(
        self, session: aiohttp.ClientSession, url: str, payload: dict
    ) -> dict[str, Any]:
        try:
            async with session.post(
                url,
                headers=self._headers(),
                json=payload,
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                resp.raise_for_status()
                data = await resp.json()

            if data.get("msg") != "Success":
                raise SunsynkApiError(f"API error: {data.get('msg')} for {url}")

            return data.get("data", {})

        except aiohttp.ClientResponseError as err:
            raise SunsynkApiError(f"HTTP {err.status} for {url}") from err
        except aiohttp.ClientError as err:
            raise SunsynkApiError(f"Connection error for {url}: {err}") from err

    async def async_get_inverter_info(
        self, session: aiohttp.ClientSession, serial: str
    ) -> dict[str, Any]:
        url = f"{self._base}/api/v1/inverter/{serial}"
        return await self._get(session, url)

    async def async_get_pv_data(
        self, session: aiohttp.ClientSession, serial: str
    ) -> dict[str, Any]:
        url = f"{self._base}/api/v1/inverter/{serial}/realtime/input"
        return await self._get(session, url)

    async def async_get_grid_data(
        self, session: aiohttp.ClientSession, serial: str
    ) -> dict[str, Any]:
        url = f"{self._base}/api/v1/inverter/grid/{serial}/realtime"
        return await self._get(session, url, params={"sn": serial})

    async def async_get_battery_data(
        self, session: aiohttp.ClientSession, serial: str
    ) -> dict[str, Any]:
        url = f"{self._base}/api/v1/inverter/battery/{serial}/realtime"
        return await self._get(session, url, params={"sn": serial, "lan": "en"})

    async def async_get_load_data(
        self, session: aiohttp.ClientSession, serial: str
    ) -> dict[str, Any]:
        url = f"{self._base}/api/v1/inverter/load/{serial}/realtime"
        return await self._get(session, url, params={"sn": serial})

    async def async_get_output_data(
        self, session: aiohttp.ClientSession, serial: str
    ) -> dict[str, Any]:
        url = f"{self._base}/api/v1/inverter/{serial}/realtime/output"
        return await self._get(session, url)

    async def async_get_temp_data(
        self, session: aiohttp.ClientSession, serial: str
    ) -> dict[str, Any]:
        today = date.today().strftime("%Y-%m-%d")
        url = f"{self._base}/api/v1/inverter/{serial}/output/day"
        raw = await self._get(
            session, url, params={"lan": "en", "date": today, "column": "dc_temp,igbt_temp"}
        )
        result: dict[str, Any] = {}
        infos = raw.get("infos", [])
        for info in infos:
            records = info.get("records", [])
            if records:
                last = records[-1]
                column = info.get("label", "").lower().replace(" ", "_")
                if "dc" in column or column == "dc_temp":
                    result["dc_temp"] = last.get("value")
                elif "igbt" in column or "ac" in column or column == "igbt_temp":
                    result["igbt_temp"] = last.get("value")
        return result

    async def async_get_settings(
        self, session: aiohttp.ClientSession, serial: str
    ) -> dict[str, Any]:
        url = f"{self._base}/api/v1/common/setting/{serial}/read"
        return await self._get(session, url)

    async def async_write_settings(
        self, session: aiohttp.ClientSession, serial: str, payload: dict[str, Any]
    ) -> None:
        url = f"{self._base}/api/v1/common/setting/{serial}/set"
        await self._post(session, url, payload)
        _LOGGER.debug("Settings written to inverter %s", serial)

    async def async_fetch_all(
        self, session: aiohttp.ClientSession, serial: str
    ) -> dict[str, Any]:
        """Fetch all endpoints concurrently and return merged data dict."""
        results = await asyncio.gather(
            self.async_get_inverter_info(session, serial),
            self.async_get_pv_data(session, serial),
            self.async_get_grid_data(session, serial),
            self.async_get_battery_data(session, serial),
            self.async_get_load_data(session, serial),
            self.async_get_output_data(session, serial),
            self.async_get_temp_data(session, serial),
            self.async_get_settings(session, serial),
            return_exceptions=True,
        )

        keys = ["inverter", "pv", "grid", "battery", "load", "output", "temp", "settings"]
        data: dict[str, Any] = {}

        for key, result in zip(keys, results):
            if isinstance(result, Exception):
                _LOGGER.warning("Failed to fetch %s data for %s: %s", key, serial, result)
                data[key] = {}
            else:
                data[key] = result

        return data

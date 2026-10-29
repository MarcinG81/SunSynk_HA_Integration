"""Sunsynk API client - async, fetches all inverter data endpoints."""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import date
from typing import Any
from urllib.parse import quote

import aiohttp

_LOGGER = logging.getLogger(__name__)


def _is_success(msg: Any) -> bool:
    """Whether an API response's `msg` field indicates success.

    Most endpoints reply with exactly "Success", but at least one
    multi-inverter/parallel setup has been observed replying to a
    settings write with "send command success:{}" instead — a message
    that means the command DID succeed but doesn't equal the expected
    string exactly (#21). A strict `== "Success"` check treated that as
    a failure even though the setting was actually applied (confirmed by
    the resulting slot showing up correctly). Match "success" as a
    case-insensitive substring instead, since this API's success message
    isn't documented anywhere and apparently isn't fully consistent.
    """
    if not isinstance(msg, str):
        return False
    # Anchor the complete reply so explicit failures such as ``not success``
    # and ``unsuccessful`` can never be mistaken for acknowledgements.
    return (
        re.fullmatch(
            r"(?:success|send\s+command\s+success(?:\s*:\s*\{\s*\})?)",
            msg.strip(),
            flags=re.IGNORECASE,
        )
        is not None
    )


class SunsynkApiError(Exception):
    """Raised when an API call fails."""


class SunsynkAuthenticationError(SunsynkApiError):
    """Raised when the API rejects an access token."""


def _api_error(message: str, url: str) -> SunsynkApiError:
    """Classify authentication responses separately from other API errors."""
    lowered = message.lower()
    if (
        "invalid token" in lowered
        or "token expired" in lowered
        or "unauthorized" in lowered
    ):
        return SunsynkAuthenticationError(
            f"API authentication error: {message} for {url}"
        )
    return SunsynkApiError(f"API error: {message} for {url}")


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

            if not _is_success(data.get("msg")):
                raise _api_error(str(data.get("msg")), url)

            return data.get("data", {})

        except aiohttp.ClientResponseError as err:
            if err.status == 401:
                raise SunsynkAuthenticationError(f"HTTP 401 for {url}") from err
            raise SunsynkApiError(f"HTTP {err.status} for {url}") from err
        except aiohttp.ClientError as err:
            raise SunsynkApiError(f"Connection error for {url}: {err}") from err
        except TimeoutError as err:
            # aiohttp's total timeout is not a ClientError.
            raise SunsynkApiError(f"Timed out waiting for {url}") from err

    async def _post(
        self,
        session: aiohttp.ClientSession,
        url: str,
        payload: dict,
        params: dict | None = None,
    ) -> dict[str, Any]:
        try:
            # Writes carry the bearer token and a settings payload; don't let
            # a redirect send either to another host.
            async with session.post(
                url,
                headers=self._headers(),
                json=payload,
                params=params,
                timeout=aiohttp.ClientTimeout(total=15),
                allow_redirects=False,
            ) as resp:
                if 300 <= resp.status < 400:
                    raise SunsynkApiError(
                        f"Write to {url} was redirected (HTTP {resp.status}); "
                        "not following it"
                    )
                resp.raise_for_status()
                data = await resp.json()

            if not _is_success(data.get("msg")):
                raise _api_error(str(data.get("msg")), url)

            return data.get("data", {})

        except aiohttp.ClientResponseError as err:
            if err.status == 401:
                raise SunsynkAuthenticationError(f"HTTP 401 for {url}") from err
            raise SunsynkApiError(f"HTTP {err.status} for {url}") from err
        except aiohttp.ClientError as err:
            raise SunsynkApiError(f"Connection error for {url}: {err}") from err
        except TimeoutError as err:
            # aiohttp's total timeout is not a ClientError.
            raise SunsynkApiError(f"Timed out waiting for {url}") from err

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
        today = date.today().strftime("%Y-%m-%d")  # noqa: DTZ011 — intentionally local calendar date
        url = f"{self._base}/api/v1/inverter/{serial}/output/day"
        raw = await self._get(
            session,
            url,
            params={"lan": "en", "date": today, "column": "dc_temp,igbt_temp"},
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

    async def async_get_flow_data(
        self, session: aiohttp.ClientSession, serial: str
    ) -> dict[str, Any]:
        """Energy flow: PV/battery/grid/load/generator/micro-inverter power in one call.

        Same data behind the flow diagram in the Sunsynk Connect app. This is
        the only endpoint that reports generator port power (`genPower`) and
        micro-inverter power (`minPower`) — `existsGen`/`existsMin` indicate
        whether each is actually wired up for this inverter. (#17)
        """
        url = f"{self._base}/api/v1/inverter/{serial}/flow"
        return await self._get(session, url)

    async def async_get_settings(
        self, session: aiohttp.ClientSession, serial: str
    ) -> dict[str, Any]:
        url = f"{self._base}/api/v1/common/setting/{serial}/read"
        return await self._get(session, url)

    async def async_write_settings(
        self, session: aiohttp.ClientSession, serial: str, payload: dict[str, Any]
    ) -> None:
        url = f"{self._base}/api/v1/common/setting/{serial}/set"
        # Exactly which fields went in one request is what tells apart a
        # rejected write from a partial one (#32). "sn" is left out.
        _LOGGER.debug(
            "Writing settings to inverter %s: %s",
            serial,
            {key: value for key, value in payload.items() if key != "sn"},
        )
        await self._post(session, url, payload)
        _LOGGER.debug("Settings written to inverter %s", serial)

    async def async_get_plant_info(
        self, session: aiohttp.ClientSession, plant_id: str
    ) -> dict[str, Any]:
        # `lan` is required here — omitting it doesn't 404, the API rejects
        # the call outright: "Required request parameter 'lan' for method
        # parameter type String is not present" (#20).
        url = f"{self._base}/api/v1/plant/{plant_id}"
        return await self._get(session, url, params={"lan": "en"})

    async def async_set_plant_income(
        self, session: aiohttp.ClientSession, plant_id: str, payload: dict[str, Any]
    ) -> None:
        # Precautionary — not confirmed to need `lan` the way the GET above
        # does (we've never gotten far enough to find out), but every other
        # plant/lan-requiring endpoint takes it and it's harmless to include.
        url = f"{self._base}/api/v1/plant/{plant_id}/income"
        await self._post(session, url, payload, params={"lan": "en"})
        _LOGGER.debug("Plant income settings written for plant %s", plant_id)

    async def async_get_battery_packs(
        self, session: aiohttp.ClientSession, serial: str
    ) -> list[dict[str, str]]:
        """The Sunsynk batteries on an inverter: each bank, then its packs (#44).

        The cloud lists a bank (`<inverter serial>M01`) under Equipment →
        Battery; the packs behind it come from the bank's `slave` list, as
        the sunsynk.net battery page reads them. Batteries of other makes
        aren't listed.
        """
        listing = await self._get(
            session,
            f"{self._base}/api/v1/batteries",
            params={"pageNumber": 1, "pageSize": 10, "status": -1, "invSn": serial},
        )
        packs: list[dict[str, str]] = []
        for bank in listing.get("infos") or []:
            if not isinstance(bank, dict) or not bank.get("sn"):
                continue
            if bank.get("invSn") and str(bank["invSn"]) != serial:
                continue
            gsn = str(bank.get("gsn") or "")
            packs.append({"sn": str(bank["sn"]), "gsn": gsn})
            try:
                package_num = int(bank.get("packageNum") or 0)
            except (TypeError, ValueError):
                package_num = 0
            if bank.get("id") is None or not (bank.get("hasSlave") or package_num > 1):
                continue
            slaves = await self._get(
                session,
                f"{self._base}/api/v1/batteries/{quote(str(bank['id']), safe='')}/slave",
                params={"id": bank["id"]},
            )
            for slave in slaves if isinstance(slaves, list) else []:
                if isinstance(slave, dict) and slave.get("sn"):
                    packs.append(
                        {"sn": str(slave["sn"]), "gsn": str(slave.get("gsn") or gsn)}
                    )
        return packs

    async def async_get_battery_pack_realtime(
        self, session: aiohttp.ClientSession, serial: str, pack: dict[str, str]
    ) -> dict[str, Any]:
        """A battery bank's or pack's own readings: SOC, SOH, voltage... (#44)."""
        params = {"sn": pack["sn"], "invSn": serial}
        if pack.get("gsn"):
            params["gsn"] = pack["gsn"]
        url = f"{self._base}/api/v1/battery/{quote(pack['sn'], safe='')}/realtime"
        return await self._get(session, url, params=params)

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
            self.async_get_flow_data(session, serial),
            return_exceptions=True,
        )

        keys = [
            "inverter",
            "pv",
            "grid",
            "battery",
            "load",
            "output",
            "temp",
            "settings",
            "flow",
        ]
        data: dict[str, Any] = {}

        # Never turn an expired/revoked token into nine innocent-looking empty
        # payloads.  The coordinator needs the original signal so it can mark
        # the update failed and force a fresh authentication attempt.
        auth_error = next(
            (
                result
                for result in results
                if isinstance(result, SunsynkAuthenticationError)
            ),
            None,
        )
        if auth_error is not None:
            raise auth_error

        failures = [result for result in results if isinstance(result, Exception)]
        if len(failures) == len(results):
            # Partial endpoint outages remain usable, but a completely failed
            # poll must not be published as a successful empty update.
            raise failures[0]

        for key, result in zip(keys, results, strict=True):
            if isinstance(result, Exception):
                _LOGGER.warning(
                    "Failed to fetch %s data for %s: %s", key, serial, result
                )
                data[key] = {}
            else:
                data[key] = result

        if _LOGGER.isEnabledFor(logging.DEBUG):
            for key in ("inverter", "battery"):
                _LOGGER.debug(
                    "Sunsynk %s %s fields: %s",
                    serial,
                    key,
                    sorted(data.get(key, {}).keys()),
                )

        return data

"""Client for Deye's official cloud API, the DeyeCloud OpenAPI.

Deye inverters on SOLARMAN / LSW loggers report to Deye Cloud, not to
Sunsynk Connect or Inteless, so they can't use the Sunsynk API (#37). This
client talks to https://developer.deyecloud.com instead. It needs a
developer application (App ID and App Secret) and the user's Deye Cloud
login.

Two things work differently from the Sunsynk API:

- **Writes are orders.** Every change returns an `orderId` and is applied by
  the logger later. The outcome is read from `GET /order/{orderId}`: 666 is
  success, 0 and 100 are still pending, anything else failed.
- **Settings are grouped by purpose, not by page.** Work mode, energy
  pattern, solar sell, battery parameters, power limits and the six-slot
  time-of-use table each have their own order endpoint, and their own read
  endpoint under `/config/`.

Errors subclass the Sunsynk client's, so the rest of the integration handles
both clouds the same way.

Field names follow Deye's published Swagger definition
(https://eu1-developer.deyecloud.com/v2/api-docs), which is more complete
than the sample code: time-of-use slots, for instance, take an `enableSell`
flag the samples never use.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import time
from collections.abc import Callable
from typing import Any

import aiohttp

from ..api.client import SunsynkApiError, SunsynkAuthenticationError

_LOGGER = logging.getLogger(__name__)

# Data centres Deye publishes for the OpenAPI. Each account lives in one.
REGIONS: dict[str, str] = {
    "eu": "https://eu1-developer.deyecloud.com/v1.0",
    "us": "https://us1-developer.deyecloud.com/v1.0",
}

_TIMEOUT = aiohttp.ClientTimeout(total=30)
# Codes the API returns for success; it isn't consistent across endpoints.
_SUCCESS_CODES = frozenset({0, 1000000, 1106000})
# Codes for an expired or invalid token or login. An expired token can also
# come back as HTTP 200 with only a message, so those are matched too.
_AUTH_CODES = frozenset({1001, 1002, 1003, 2101017})
_AUTH_HINTS = ("invalid token", "token invalid", "token expired", "expired token")
# One order at a time per inverter: another one is still running. Retry
# later; it isn't a refusal.
_BUSY_CODES = frozenset({2104004})
# Deye has announced quotas but not their error codes, so match the message.
_RATE_LIMIT_HINTS = (
    "rate limit",
    "too many",
    "too frequent",
    "quota",
    "limit exceeded",
)
# Refresh a token this long before Deye says it expires.
_TOKEN_MARGIN = 3600
# The latest-data endpoint takes at most this many devices per request.
LATEST_BATCH = 10

# Order status: 0 to be sent, 100 sending, 300 during a firmware upgrade,
# 400 terminated, 500 failed, 666 succeeded.
ORDER_SUCCESS = 666
ORDER_PENDING = frozenset({0, 100, 300})

# System work modes, by the value the Sunsynk API uses for the same mode, so
# the rest of the integration can keep using `sysWorkMode` 0-2.
WORK_MODES: dict[int, str] = {
    0: "SELLING_FIRST",
    1: "ZERO_EXPORT_TO_LOAD",
    2: "ZERO_EXPORT_TO_CT",
}
ENERGY_PATTERNS: dict[int, str] = {0: "BATTERY_FIRST", 1: "LOAD_FIRST"}
TOU_SLOTS = 6
WEEKDAYS = (
    "SUNDAY",
    "MONDAY",
    "TUESDAY",
    "WEDNESDAY",
    "THURSDAY",
    "FRIDAY",
    "SATURDAY",
)


class DeyeCloudApiError(SunsynkApiError):
    """A DeyeCloud request failed."""


class DeyeCloudAuthError(SunsynkAuthenticationError):
    """The App ID, App Secret or login was refused, or the token expired."""


class DeyeCloudRateLimitError(DeyeCloudApiError):
    """Deye refused the request for exceeding a quota or rate limit."""

    def __init__(self, message: str, retry_after: float | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


class DeyeCloudOrderError(DeyeCloudApiError):
    """A setting change was accepted but the inverter didn't apply it."""


class DeyeCloudBusyError(DeyeCloudApiError):
    """Another change is still running on this inverter; try again shortly."""


def hash_password(password: str) -> str:
    """The SHA-256 hex digest the token endpoint expects instead of the password.

    Only this digest needs storing, never the password itself.
    """
    return hashlib.sha256(password.encode("utf-8")).hexdigest()


def _code(result: dict[str, Any]) -> int | None:
    try:
        return int(result.get("code"))
    except (TypeError, ValueError):
        return None


def _is_rate_limit(message: str) -> bool:
    lowered = message.lower()
    return any(hint in lowered for hint in _RATE_LIMIT_HINTS)


def tou_time(value: Any) -> str:
    """A slot time as "HH:MM", the format the update endpoint requires.

    `/config/tou` reports times as "HHMM" (e.g. "0130"), the update endpoint
    takes "HH:MM".
    """
    text = str(value or "").strip()
    if ":" in text:
        hours, _, minutes = text.partition(":")
    else:
        digits = text.zfill(4)
        hours, minutes = digits[:2], digits[2:4]
    hh, mm = int(hours), int(minutes or 0)
    if not (0 <= hh <= 23 and 0 <= mm <= 59):
        raise ValueError(f"Not a time of day: {value!r}")
    return f"{hh:02d}:{mm:02d}"


class DeyeCloudClient:
    """One Deye Cloud account in one data centre."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        region: str,
        app_id: str,
        app_secret: str,
        login: str,
        password_hash: str,
        *,
        company_id: str | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if region not in REGIONS:
            raise ValueError(f"Unknown Deye Cloud region {region!r}")
        self._session = session
        self._base = REGIONS[region]
        self._app_id = app_id
        self._app_secret = app_secret
        self._login = login
        self._password_hash = password_hash
        self._company_id = company_id
        self._clock = clock
        self._token: str | None = None
        self._token_expires_at = 0.0

    # ── Authentication ───────────────────────────────────────────────────

    def invalidate_token(self) -> None:
        self._token = None

    async def async_get_token(self) -> str:
        """A valid access token, logging in again when needed."""
        if self._token and self._clock() < self._token_expires_at:
            return self._token
        body: dict[str, Any] = {
            "appSecret": self._app_secret,
            "password": self._password_hash,
        }
        # A login is an email address or a Deye Cloud username.
        body["email" if "@" in self._login else "username"] = self._login
        if self._company_id:
            body["companyId"] = self._company_id
        result = await self._post_json(
            f"{self._base}/account/token", body, params={"appId": self._app_id}
        )
        if _code(result) not in _SUCCESS_CODES and result.get("success") is not True:
            message = str(result.get("msg") or "Login failed")
            if _is_rate_limit(message):
                raise DeyeCloudRateLimitError(message)
            raise DeyeCloudAuthError(f"Deye Cloud login failed: {message}")
        token = str(result.get("accessToken") or "")
        if not token:
            raise DeyeCloudAuthError("Deye Cloud login returned no access token")
        # Some responses prefix the scheme; the header adds its own.
        self._token = token.split(" ", 1)[-1]
        try:
            expires_in = float(result.get("expiresIn"))
        except (TypeError, ValueError):
            expires_in = 24 * 3600.0
        self._token_expires_at = self._clock() + max(60.0, expires_in - _TOKEN_MARGIN)
        return self._token

    # ── Transport ────────────────────────────────────────────────────────

    async def _post_json(
        self,
        url: str,
        body: dict[str, Any] | None,
        *,
        params: dict[str, str] | None = None,
        token: str | None = None,
        method: str = "POST",
    ) -> dict[str, Any]:
        headers = {"Content-Type": "application/json"}
        if token:
            headers["Authorization"] = f"bearer {token}"
        call = self._session.get if method == "GET" else self._session.post
        try:
            # Credentials and tokens must never follow a redirect elsewhere.
            async with call(
                url,
                json=body if method != "GET" else None,
                params=params,
                headers=headers,
                timeout=_TIMEOUT,
                allow_redirects=False,
            ) as resp:
                if 300 <= resp.status < 400:
                    raise DeyeCloudApiError(
                        f"Deye Cloud redirected {url} (HTTP {resp.status}); not following it"
                    )
                resp.raise_for_status()
                result = await resp.json()
        except aiohttp.ClientResponseError as err:
            if err.status == 429:
                raise DeyeCloudRateLimitError(f"HTTP 429 for {url}") from err
            if err.status == 401:
                raise DeyeCloudAuthError(f"HTTP 401 for {url}") from err
            raise DeyeCloudApiError(f"HTTP {err.status} for {url}") from err
        except aiohttp.ClientError as err:
            raise DeyeCloudApiError(f"Connection error for {url}: {err}") from err
        except TimeoutError as err:
            raise DeyeCloudApiError(f"Timed out waiting for {url}") from err
        if not isinstance(result, dict):
            raise DeyeCloudApiError(f"Unexpected response from {url}")
        return result

    async def _request(
        self, path: str, body: dict[str, Any] | None = None, *, method: str = "POST"
    ) -> dict[str, Any]:
        """Call an authenticated endpoint, logging in again once if refused."""
        for attempt in (1, 2):
            token = await self.async_get_token()
            result = await self._post_json(
                f"{self._base}{path}", body, token=token, method=method
            )
            code = _code(result)
            if code is None or code in _SUCCESS_CODES:
                data = result.get("data")
                return data if isinstance(data, dict) else result
            message = str(result.get("msg") or "Unknown error")
            if code in _AUTH_CODES or any(
                hint in message.lower() for hint in _AUTH_HINTS
            ):
                self.invalidate_token()
                if attempt == 1:
                    continue
                raise DeyeCloudAuthError(f"{message} (code {code})")
            if code in _BUSY_CODES:
                raise DeyeCloudBusyError(f"{path}: {message} (code {code})")
            if _is_rate_limit(message):
                raise DeyeCloudRateLimitError(f"{message} (code {code})")
            raise DeyeCloudApiError(f"{path}: {message} (code {code})")
        raise DeyeCloudAuthError(f"{path}: login refused")  # pragma: no cover

    # ── Reading ──────────────────────────────────────────────────────────

    async def async_list_inverters(self) -> list[dict[str, Any]]:
        """Every inverter on the account, with the station it belongs to."""
        inverters: list[dict[str, Any]] = []
        page = 1
        while True:
            result = await self._request(
                "/station/listWithDevice",
                {"page": page, "size": 50, "deviceType": "INVERTER"},
            )
            stations = result.get("stationList") or []
            for station in stations:
                for device in station.get("deviceListItems") or []:
                    if not device.get("deviceSn"):
                        continue
                    inverters.append(
                        {
                            **device,
                            "stationId": station.get("id"),
                            "stationName": station.get("name"),
                        }
                    )
            total = result.get("total")
            if not stations or not isinstance(total, int) or page * 50 >= total:
                return inverters
            page += 1

    async def async_get_latest(self, serials: list[str]) -> dict[str, dict[str, Any]]:
        """Latest measure points per inverter: {serial: {key: value}}."""
        latest: dict[str, dict[str, Any]] = {}
        for start in range(0, len(serials), LATEST_BATCH):
            batch = serials[start : start + LATEST_BATCH]
            result = await self._request("/device/latest", {"deviceList": batch})
            for device in result.get("deviceDataList") or []:
                serial = device.get("deviceSn")
                if not serial:
                    continue
                latest[serial] = {
                    item["key"]: item.get("value")
                    for item in device.get("dataList") or []
                    if item.get("key")
                }
        return latest

    async def async_get_config(self, serial: str, kind: str) -> dict[str, Any]:
        """A settings group: "system", "battery" or "tou"."""
        if kind not in ("system", "battery", "tou"):
            raise ValueError(f"Unknown Deye Cloud config group {kind!r}")
        return await self._request(f"/config/{kind}", {"deviceSn": serial})

    # ── Writing ──────────────────────────────────────────────────────────

    async def _order(
        self,
        path: str,
        body: dict[str, Any],
        *,
        poll_interval: float = 2.0,
        timeout: float = 60.0,
    ) -> dict[str, Any]:
        """Send a setting change and wait until the inverter applies it."""
        _LOGGER.debug("Deye Cloud order %s: %s", path, body)
        result = await self._request(path, body)
        order_id = result.get("orderId")
        if not order_id:
            return result
        deadline = self._clock() + timeout
        while True:
            status_result = await self._request(f"/order/{order_id}", method="GET")
            try:
                status = int(status_result.get("status"))
            except (TypeError, ValueError):
                status = None
            if status == ORDER_SUCCESS:
                return status_result
            if status not in ORDER_PENDING:
                reason = status_result.get("error") or status_result.get("msg")
                raise DeyeCloudOrderError(
                    f"{path}: the inverter didn't apply the change "
                    f"(order {order_id}, status {status}"
                    + (f", {reason}" if reason else "")
                    + ")"
                )
            if self._clock() >= deadline:
                raise DeyeCloudOrderError(
                    f"{path}: no answer from the inverter within {timeout:.0f} s "
                    f"(order {order_id})"
                )
            await asyncio.sleep(poll_interval)

    async def async_set_work_mode(self, serial: str, mode: int) -> dict[str, Any]:
        return await self._order(
            "/order/sys/workMode/update",
            {"deviceSn": serial, "workMode": WORK_MODES[mode]},
        )

    async def async_set_energy_pattern(
        self, serial: str, pattern: int
    ) -> dict[str, Any]:
        return await self._order(
            "/order/sys/energyPattern/update",
            {"deviceSn": serial, "energyPattern": ENERGY_PATTERNS[pattern]},
        )

    async def async_set_solar_sell(self, serial: str, enabled: bool) -> dict[str, Any]:
        return await self._order(
            "/order/sys/solarSell/control",
            {"deviceSn": serial, "action": "on" if enabled else "off"},
        )

    async def async_set_battery_current(
        self, serial: str, direction: str, amps: int
    ) -> dict[str, Any]:
        """The battery's maximum charge or discharge current, in A."""
        parameter = {
            "charge": "MAX_CHARGE_CURRENT",
            "discharge": "MAX_DISCHARGE_CURRENT",
        }[direction]
        # "paramterType" is how the API spells it.
        return await self._order(
            "/order/battery/parameter/update",
            {"deviceSn": serial, "paramterType": parameter, "value": int(amps)},
        )

    async def async_set_max_sell_power(self, serial: str, watts: int) -> dict[str, Any]:
        return await self._order(
            "/order/sys/power/update",
            {"deviceSn": serial, "powerType": "MAX_SELL_POWER", "value": int(watts)},
        )

    async def async_set_tou(
        self, serial: str, slots: list[dict[str, Any]]
    ) -> dict[str, Any]:
        """Write the whole six-slot time-of-use table in one order.

        Each slot: time, power (W), soc (%), enableGridCharge, enableSell,
        enableGeneration. Every flag goes on every slot: the same hardware
        behind the Sunsynk cloud drops a slot's flags when any is missing.
        """
        if len(slots) != TOU_SLOTS:
            raise ValueError(f"The time-of-use table has {TOU_SLOTS} slots")
        items = [
            {
                "time": tou_time(slot.get("time")),
                "power": int(slot.get("power", 0)),
                "soc": int(slot.get("soc", 0)),
                "enableGridCharge": bool(slot.get("enableGridCharge")),
                "enableSell": bool(slot.get("enableSell")),
                "enableGeneration": bool(slot.get("enableGeneration")),
            }
            for slot in slots
        ]
        return await self._order(
            "/order/sys/tou/update",
            {"deviceSn": serial, "timeUseSettingItems": items, "timeoutSeconds": 30},
        )

    async def async_set_tou_enabled(self, serial: str, enabled: bool) -> dict[str, Any]:
        """Turn the timer on for every day, or off.

        Turned on without days, the timer keeps whatever days it had, and a
        timer with no day for today is stored but never applied.
        """
        body: dict[str, Any] = {
            "deviceSn": serial,
            "action": "on" if enabled else "off",
        }
        if enabled:
            body["days"] = list(WEEKDAYS)
        return await self._order("/order/sys/tou/switch", body)

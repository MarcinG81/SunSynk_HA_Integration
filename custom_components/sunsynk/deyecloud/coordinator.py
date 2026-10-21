"""Coordinator for inverters on Deye Cloud.

Same data, entities and write targets as the Sunsynk coordinator, read from
Deye Cloud instead (#37). It reads only, for now: Deye Cloud applies changes
as orders, and whether a personal account may send them hasn't been tested
on a real inverter yet, so every write is refused with a clear message
rather than half working.

Live data comes in one request for up to ten inverters. Settings take three
requests per inverter and rarely change, so they are read every
`_CONFIG_MAX_AGE` seconds rather than on every refresh, to stay within
Deye's quotas.
"""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.update_coordinator import UpdateFailed
from homeassistant.util import dt as dt_util

from ..const import DOMAIN
from ..coordinator import SunsynkCoordinator
from . import mapping
from .client import (
    DEVICE_OFFLINE,
    DEVICE_STATE,
    DeyeCloudApiError,
    DeyeCloudAuthError,
    DeyeCloudClient,
    DeyeCloudUnsupportedError,
)

_LOGGER = logging.getLogger(__name__)

_CONFIG_GROUPS = ("system", "battery", "tou")
_CONFIG_MAX_AGE = 1800
# Deye Cloud keeps answering with an offline logger's last points. Older
# than this, they are treated as no data: loggers upload about every 5
# minutes, and two inverters in #37 showed the same values for hours.
_STALE_AFTER = timedelta(minutes=30)

READ_ONLY_MESSAGE = (
    "Changing settings through Deye Cloud isn't supported yet; "
    "this inverter is read-only for now."
)


class DeyeCloudCoordinator(SunsynkCoordinator):
    """All Deye Cloud inverters in one config entry."""

    def __init__(
        self,
        hass: HomeAssistant,
        *,
        region: str,
        app_id: str,
        app_secret: str,
        login: str,
        password_hash: str,
        serials: list[str],
        refresh_interval: int,
        company_id: str | None = None,
        entry_id: str = "",
    ) -> None:
        super().__init__(
            hass,
            auth=None,  # type: ignore[arg-type]
            serials=serials,
            refresh_interval=refresh_interval,
            entry_id=entry_id,
        )
        self._credentials = {
            "region": region,
            "app_id": app_id,
            "app_secret": app_secret,
            "login": login,
            "password_hash": password_hash,
            "company_id": company_id,
        }
        self._client: DeyeCloudClient | None = None
        self._config: dict[str, dict[str, dict[str, Any]]] = {}
        self._config_read_at: dict[str, float] = {}
        # Config groups Deye Cloud doesn't offer for an inverter: not asked
        # for again until Home Assistant restarts.
        self._unsupported: dict[str, set[str]] = {}

    async def _async_client(self) -> DeyeCloudClient:
        session = await self._async_get_session()
        if self._client is None or self._client.session is not session:
            credentials = dict(self._credentials)
            self._client = DeyeCloudClient(
                session,
                credentials.pop("region"),
                credentials.pop("app_id"),
                credentials.pop("app_secret"),
                credentials.pop("login"),
                credentials.pop("password_hash"),
                **credentials,
            )
        return self._client

    def can_configure(self, serial: str) -> bool:
        """Whether Deye Cloud offers any of this inverter's settings."""
        return self._unsupported.get(serial, set()) != set(_CONFIG_GROUPS)

    def _auth_failed(self, err: Exception) -> UpdateFailed:
        ir.async_create_issue(
            self.hass,
            DOMAIN,
            f"auth_failed_{self._entry_id}",
            is_fixable=False,
            severity=ir.IssueSeverity.ERROR,
            translation_key="auth_failed",
        )
        return UpdateFailed(f"Deye Cloud login failed: {err}")

    async def _async_update_data(self) -> dict[str, dict[str, Any]]:
        client = await self._async_client()
        try:
            latest = await client.async_get_latest(list(self.serials))
        except DeyeCloudAuthError as err:
            raise self._auth_failed(err) from err
        except DeyeCloudApiError as err:
            raise UpdateFailed(f"Deye Cloud: {err}") from err
        ir.async_delete_issue(self.hass, DOMAIN, f"auth_failed_{self._entry_id}")

        result: dict[str, dict[str, Any]] = {}
        missing: list[str] = []
        for serial in self.serials:
            points = latest.get(serial)
            if not points:
                missing.append(serial)
                _LOGGER.error("Deye Cloud reported no data for inverter %s", serial)
                # As for Sunsynk: keep the rest, never a stale SOC.
                result[serial] = dict((self.data or {}).get(serial, {}))
                result[serial]["battery"] = {}
                self._offline(serial)
                continue
            try:
                config = await self._async_config(client, serial)
            except DeyeCloudAuthError as err:
                raise self._auth_failed(err) from err
            result[serial] = mapping.sections(serial, points, config)
            result[serial]["plant"] = {}
            if self._is_stale(serial, points):
                # Shown as last reported, with when; but never a stale SOC
                # for Tariff Manager to act on.
                result[serial]["battery"] = {}
                self._offline(serial)
            else:
                ir.async_delete_issue(self.hass, DOMAIN, f"inverter_offline_{serial}")

        if self.serials and len(missing) == len(self.serials):
            raise UpdateFailed(
                f"Deye Cloud reported no data for any inverter ({', '.join(missing)})"
            )
        return result

    def _is_stale(self, serial: str, points: dict[str, Any]) -> bool:
        if points.get(DEVICE_STATE) == DEVICE_OFFLINE:
            _LOGGER.warning("Deye Cloud reports inverter %s offline", serial)
            return True
        when = mapping.collected_at(points)
        if when is not None and dt_util.utcnow() - when > _STALE_AFTER:
            _LOGGER.warning(
                "Inverter %s last reported to Deye Cloud at %s",
                serial,
                dt_util.as_local(when).isoformat(),
            )
            return True
        return False

    def _offline(self, serial: str) -> None:
        ir.async_create_issue(
            self.hass,
            DOMAIN,
            f"inverter_offline_{serial}",
            is_fixable=False,
            severity=ir.IssueSeverity.WARNING,
            translation_key="inverter_offline",
            translation_placeholders={"serial": serial},
        )

    async def _async_config(
        self, client: DeyeCloudClient, serial: str
    ) -> dict[str, dict[str, Any]]:
        """The inverter's config groups, read at most every _CONFIG_MAX_AGE s."""
        now = self.hass.loop.time()
        cached = self._config.get(serial, {})
        read_at = self._config_read_at.get(serial)
        if read_at is not None and now - read_at < _CONFIG_MAX_AGE:
            return cached
        unsupported = self._unsupported.setdefault(serial, set())
        config: dict[str, dict[str, Any]] = {}
        for kind in _CONFIG_GROUPS:
            if kind in unsupported:
                continue
            try:
                config[kind] = await client.async_get_config(serial, kind)
            except DeyeCloudUnsupportedError:
                unsupported.add(kind)
                _LOGGER.info(
                    "Deye Cloud doesn't offer the %s settings of inverter %s",
                    kind,
                    serial,
                )
            except DeyeCloudAuthError:
                raise
            except DeyeCloudApiError as err:
                # Keep what was read last time rather than showing nothing.
                _LOGGER.debug(
                    "Could not read the %s settings of %s: %s", kind, serial, err
                )
                if kind in cached:
                    config[kind] = cached[kind]
        self._config[serial] = config
        self._config_read_at[serial] = now
        return config

    # ── Writes: not yet ──────────────────────────────────────────────────

    async def async_write_setting(
        self, serial: str, setting_key: str, value: Any
    ) -> None:
        raise HomeAssistantError(READ_ONLY_MESSAGE)

    async def async_write_settings(self, serial: str, settings: dict[str, Any]) -> None:
        raise HomeAssistantError(READ_ONLY_MESSAGE)

    async def async_write_plant_price(self, serial: str, price: float) -> None:
        raise HomeAssistantError(READ_ONLY_MESSAGE)

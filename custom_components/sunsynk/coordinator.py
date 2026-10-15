"""DataUpdateCoordinator for Sunsynk integration."""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any

import aiohttp
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api.auth import SunsynkAuth, SunsynkAuthError
from .api.client import SunsynkApiError, SunsynkClient
from .calibration import PerformanceRatioCalibrator
from .const import (
    BATTERY_SETTING_KEYS,
    DOMAIN,
    SOLAR_FORECAST_UPDATE_INTERVAL,
    SYSTEM_MODE_PAYLOAD_KEYS,
)

_LOGGER = logging.getLogger(__name__)

# The write endpoint acknowledges ("send command success:{}") before the
# inverter has applied a value, and how long that takes varies a lot: a
# single Deye inverter (#31) still reported the previous value 2 s after
# every write, yet had applied it a few minutes later. Writes are therefore
# confirmed in the background at these offsets (seconds after the write),
# stopping at the first read-back that matches. Only a value that still
# hasn't arrived after the last offset is reported as rejected.
_VERIFY_WRITE_OFFSETS = (5, 10, 15, 30, 60)


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
        # serial -> setting -> (write id, value sent) for writes the inverter
        # hasn't confirmed yet. The write id lets a newer write of the same
        # setting supersede an older one's background confirmation.
        self._unconfirmed_writes: dict[str, dict[str, tuple[int, Any]]] = {}
        self._write_seq = 0
        self._verify_tasks: set[asyncio.Task[None]] = set()

    async def _async_get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session

    @property
    def write_target_serials(self) -> list[str]:
        """`self.serials`, collapsing a parallel group's slave into its
        master.

        Callers that write the same setting to "every configured inverter"
        (Tariff Manager, Virtual Slot Scheduler) used to do that literally —
        fine for genuinely independent inverters, but for a parallel group
        `async_write_setting` already redirects the slave's write to the
        master (#21), so iterating both meant writing the same setting to
        the master twice per tick. A second write landing right behind the
        first was itself enough to make the master briefly reject/revert
        one of them — the master's own repairs, not just the slave's, is
        what gave this away. Use this instead of `self.serials` for any
        write loop; keep using `self.serials` for reads (fetching data
        still needs every configured serial).
        """
        # A slave is dropped only when it has a master to cover it. With no
        # confirmed master (e.g. a momentary bad poll) or an ambiguous one,
        # it resolves to itself and stays.
        return [
            serial
            for serial in self.serials
            if SunsynkCoordinator._resolve_parallel_write_target(self, serial, "")
            == serial
        ]

    def _parallel_role(self, serial: str) -> str | None:
        """Return "master", "slave", or None outside a parallel group.

        The API reports `parallel` and `equipMode` (1 = master, 0 = slave) in
        the *settings* payload: a real parallel diagnostics dump (#21) had
        them nowhere else, so reading them from the inverter info meant no
        parallel group was ever recognised. The inverter info is still
        checked as a fallback. (Backported from 2.0.0-beta.4.)
        """
        data = (self.data or {}).get(serial, {})
        for section in ("settings", "inverter"):
            info = data.get(section) or {}
            if "equipMode" not in info:
                continue
            try:
                parallel = int(info.get("parallel") or 0)
                equip_mode = int(info["equipMode"])
            except (TypeError, ValueError):
                continue
            if not parallel:
                return None
            return {1: "master", 0: "slave"}.get(equip_mode)
        return None

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
                SunsynkCoordinator._overlay_unconfirmed_writes(
                    self, serial, result[serial]
                )
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
                continue

            plant_id = result[serial].get("inverter", {}).get("plant", {}).get("id")
            if plant_id:
                try:
                    result[serial]["plant"] = await client.async_get_plant_info(
                        session, str(plant_id)
                    )
                except Exception as err:  # noqa: BLE001
                    _LOGGER.debug(
                        "Could not fetch plant info for %s (plant %s): %s",
                        serial, plant_id, err,
                    )
                    result[serial]["plant"] = (
                        (self.data or {}).get(serial, {}).get("plant", {})
                    )
            else:
                result[serial]["plant"] = {}

        return result

    def _resolve_parallel_write_target(self, serial: str, setting_key: str) -> str:
        """For a parallel-group slave, all settings should be written to the
        master's serial instead.

        #21: a parallel/multi-inverter account showed chargeCurrent and
        dischargeCurrent corrupted on BOTH units (0 on the slave, a wildly
        out-of-range value on the master) after this integration wrote them
        to each configured serial independently. `equipMode` (0 = slave,
        1 = master) and `parallel` are already present in the `inverter`
        data fetched every poll; non-parallel accounts don't have `parallel`
        set, so they're unaffected.

        Originally scoped to battery settings only — an earlier diagnostics
        dump showed System Mode Timer slot settings (time1on/sellTime1/etc.)
        verifying correctly when written to each unit independently. That
        turned out to be an artifact of the verification delay (2s) being
        shorter than the actual sync window: the same reporter later wrote a
        slot's start time directly to the slave *on the Sunsynk portal
        itself* (bypassing this integration entirely) and watched the
        portal silently revert it back to the master's value 10-15 seconds
        later. So a 2-second verification read can land before that revert
        and look successful, while the value doesn't actually stick. Since
        the slave was never going to keep an independent value for *any*
        setting, redirecting only some categories was an artificially
        narrow fix — now applied to every setting.
        """
        if SunsynkCoordinator._parallel_role(self, serial) != "slave":
            return serial

        # Only a master of the slave's own plant can be its group's master;
        # one account can hold several installations.
        plant = SunsynkCoordinator._plant_id(self, serial)
        all_masters = [
            other_serial
            for other_serial in self.serials
            if other_serial != serial
            and SunsynkCoordinator._parallel_role(self, other_serial) == "master"
        ]
        plants = {m: SunsynkCoordinator._plant_id(self, m) for m in all_masters}
        if plant is None or all(p is None for p in plants.values()):
            # Plants not known yet (e.g. before the first plant lookup).
            masters = all_masters
        else:
            # Once plants are known, only an exact match counts: a master
            # whose plant is unknown could belong to another installation.
            masters = [m for m in all_masters if plants[m] == plant]
        if len(masters) == 1:
            return masters[0]
        if masters:
            # Never guess between groups: the slave's own write is reverted
            # by its master, a write to another group's master is not.
            _LOGGER.debug(
                "Not redirecting %s: more than one possible master (%s)",
                serial,
                ", ".join(masters),
            )
        return serial

    def _plant_id(self, serial: str) -> str | None:
        plant = (self.data or {}).get(serial, {}).get("inverter", {}).get("plant") or {}
        plant_id = plant.get("id") if isinstance(plant, dict) else None
        return str(plant_id) if plant_id not in (None, "") else None

    async def async_write_setting(
        self, serial: str, setting_key: str, value: Any
    ) -> None:
        """Write a single setting to the inverter."""
        await SunsynkCoordinator.async_write_settings(self, serial, {setting_key: value})

    @staticmethod
    def _allowed_setting_group(setting_key: str) -> frozenset[str]:
        """Return the API payload group that owns one setting."""
        if setting_key in BATTERY_SETTING_KEYS:
            return BATTERY_SETTING_KEYS
        if setting_key in SYSTEM_MODE_PAYLOAD_KEYS:
            return SYSTEM_MODE_PAYLOAD_KEYS
        return frozenset([setting_key])

    @staticmethod
    def _as_reported(value: Any, reported: Any) -> Any:
        """Encode a changed value the way the inverter reports that field.

        Predbat confirmed live that the API silently drops a flag sent in a
        different form from its read-back (e.g. 1 for a time{n}on reported
        as "true") while keeping the rest of the write (#32). Deye-built
        inverters report flags as "true"/"false" and sellTime{n}En as "0"/"1";
        some Sunsynk models report real booleans.
        """
        if reported is None:
            return value
        if isinstance(value, bool):
            value = int(value)
        if isinstance(value, float) and value.is_integer():
            value = int(value)
        if isinstance(reported, bool):
            return bool(value)
        if isinstance(reported, str):
            if reported.strip().lower() in ("true", "false") and value in (0, 1):
                return "true" if value else "false"
            return str(value)
        return value

    async def async_write_settings(self, serial: str, settings: dict[str, Any]) -> None:
        """Write one or more settings, one request per settings page.

        Read-modify-write from a fresh read, as Predbat and other clients do:
        building on the poll cache would send back, and so undo, anything
        changed in the Sunsynk app since the last poll. Values written but
        not yet confirmed by the inverter are kept over the fresh read.
        """
        if not settings:
            return
        serial = SunsynkCoordinator._resolve_parallel_write_target(
            self, serial, next(iter(settings))
        )
        session = await self._async_get_session()

        try:
            token = await self._auth.async_get_token(session)
        except SunsynkAuthError as err:
            raise UpdateFailed(f"Authentication failed: {err}") from err

        client = SunsynkClient(self._auth._api_server, token)

        cached_settings = dict((self.data or {}).get(serial, {}).get("settings", {}))
        try:
            current_settings = dict(await client.async_get_settings(session, serial))
        except Exception as err:
            if not cached_settings:
                raise UpdateFailed(f"Cannot read settings for {serial}: {err}") from err
            _LOGGER.debug(
                "Fresh settings read for %s failed, using cached settings: %s",
                serial, err,
            )
            current_settings = cached_settings
        else:
            if current_settings:
                SunsynkCoordinator._overlay_unconfirmed_writes(
                    self, serial, {"settings": current_settings}
                )
            else:
                current_settings = cached_settings

        groups: dict[frozenset[str], dict[str, Any]] = {}
        for key, value in settings.items():
            groups.setdefault(SunsynkCoordinator._allowed_setting_group(key), {})[key] = value

        for allowed_keys, changes in groups.items():
            payload: dict[str, Any] = {
                k: v
                for k, v in current_settings.items()
                if k in allowed_keys and v is not None
            }
            for key, value in changes.items():
                payload[key] = SunsynkCoordinator._as_reported(
                    value, current_settings.get(key)
                )
            payload["sn"] = serial

            try:
                await client.async_write_settings(session, serial, payload)
            except SunsynkApiError as err:
                raise UpdateFailed(
                    f"Failed to write setting {', '.join(changes)}: {err}"
                ) from err

            # Hold what we just sent in the cache until the inverter confirms
            # it, so later writes and polls don't fall back to the old value.
            if self.data is not None and serial in self.data:
                self.data[serial].setdefault("settings", {}).update(payload)
            for key in changes:
                SunsynkCoordinator._track_unconfirmed_write(
                    self, serial, key, payload[key]
                )

        await self.async_request_refresh()

    def _ensure_verify_state(self) -> None:
        """Initialise confirmation state lazily for lightweight test doubles."""
        if not hasattr(self, "_unconfirmed_writes"):
            self._unconfirmed_writes = {}
        if not hasattr(self, "_write_seq"):
            self._write_seq = 0
        if not hasattr(self, "_verify_tasks"):
            self._verify_tasks = set()

    def _track_unconfirmed_write(self, serial: str, setting_key: str, value: Any) -> None:
        """Hold a just-written value until the inverter confirms it.

        Until confirmation, regular polls keep the value we sent rather than
        an older one read before it arrived. Otherwise the next write to the
        same group (e.g. a slot's limit after its start time) would resend
        the old value and undo the earlier change (#31).
        """
        SunsynkCoordinator._ensure_verify_state(self)
        self._write_seq += 1
        write_id = self._write_seq
        self._unconfirmed_writes.setdefault(serial, {})[setting_key] = (
            write_id,
            value,
        )
        task = asyncio.get_running_loop().create_task(
            SunsynkCoordinator._async_confirm_write(
                self, serial, setting_key, write_id, value
            )
        )
        self._verify_tasks.add(task)
        task.add_done_callback(self._verify_tasks.discard)

    def _is_unconfirmed(self, serial: str, setting_key: str, write_id: int) -> bool:
        entry = self._unconfirmed_writes.get(serial, {}).get(setting_key)
        return entry is not None and entry[0] == write_id

    def _overlay_unconfirmed_writes(self, serial: str, data: dict[str, Any]) -> None:
        """Keep unconfirmed values over a poll that may predate them."""
        SunsynkCoordinator._ensure_verify_state(self)
        unconfirmed = self._unconfirmed_writes.get(serial)
        if unconfirmed:
            settings = data.setdefault("settings", {})
            settings.update({key: value for key, (_id, value) in unconfirmed.items()})

    async def _async_read_settings(self, serial: str) -> dict[str, Any]:
        session = await self._async_get_session()
        token = await self._auth.async_get_token(session)
        client = SunsynkClient(self._auth._api_server, token)
        return await client.async_get_settings(session, serial)

    async def _async_confirm_write(
        self, serial: str, setting_key: str, write_id: int, value: Any
    ) -> None:
        """Confirm one write in the background, stopping at the first match.

        The write endpoint returns success even when the inverter silently
        ignores a value (out-of-range, conflicting with another setting,
        dongle offline), so a 200 response alone doesn't prove the change
        took. A Repair is raised only for a value that still hasn't arrived
        after the last offset. A newer write of the same setting supersedes
        this confirmation.
        """
        try:
            await SunsynkCoordinator._async_confirm_write_until_settled(
                self, serial, setting_key, write_id, value
            )
        except Exception:
            _LOGGER.exception("Could not confirm write of %s for %s", setting_key, serial)
        finally:
            # Whatever ended the confirmation, never leave this value
            # overlaying fresh polls with nothing left to settle it. A newer
            # write of the same key has its own id and is left alone.
            if SunsynkCoordinator._is_unconfirmed(self, serial, setting_key, write_id):
                del self._unconfirmed_writes[serial][setting_key]

    async def _async_confirm_write_until_settled(
        self, serial: str, setting_key: str, write_id: int, value: Any
    ) -> None:
        loop = asyncio.get_running_loop()
        started = loop.time()
        actual: Any = None
        read_back = False
        for offset in _VERIFY_WRITE_OFFSETS:
            await asyncio.sleep(max(0.0, started + offset - loop.time()))
            if not SunsynkCoordinator._is_unconfirmed(
                self, serial, setting_key, write_id
            ):
                return
            try:
                fresh_settings = await SunsynkCoordinator._async_read_settings(
                    self, serial
                )
            except (SunsynkAuthError, SunsynkApiError) as err:
                _LOGGER.debug(
                    "Could not confirm write of %s for %s: %s", setting_key, serial, err
                )
                continue
            read_back = True
            actual = fresh_settings.get(setting_key)
            if SunsynkCoordinator._values_match(value, actual):
                break

        if not SunsynkCoordinator._is_unconfirmed(self, serial, setting_key, write_id):
            return
        del self._unconfirmed_writes[serial][setting_key]
        if not read_back:
            # Never managed to read back: we know nothing, so don't claim a
            # rejection. The next poll shows the real value.
            _LOGGER.warning(
                "Could not confirm write of %s on inverter %s", setting_key, serial
            )
            return

        issue_id = f"setting_write_mismatch_{serial}_{setting_key}"
        if SunsynkCoordinator._values_match(value, actual):
            ir.async_delete_issue(self.hass, DOMAIN, issue_id)
            return

        if self.data is not None and serial in self.data and actual is not None:
            self.data[serial].setdefault("settings", {})[setting_key] = actual
        _LOGGER.warning(
            "Write verification failed for %s on inverter %s: sent %r, "
            "inverter still reports %r after %ss",
            setting_key, serial, value, actual, _VERIFY_WRITE_OFFSETS[-1],
        )
        ir.async_create_issue(
            self.hass,
            DOMAIN,
            issue_id,
            is_fixable=False,
            severity=ir.IssueSeverity.WARNING,
            translation_key="setting_write_mismatch",
            translation_placeholders={
                "serial": serial,
                "setting_key": setting_key,
                "expected": str(value),
                "actual": str(actual),
            },
        )

    _TRUE_STRINGS = frozenset({"true", "1"})
    _FALSE_STRINGS = frozenset({"false", "0"})

    @classmethod
    def _values_match(cls, sent: Any, actual: Any) -> bool:
        """Type-tolerant comparison between what we sent and what the API echoes back.

        The API returns booleans as "true"/"false" strings (we send 1/0)
        and numbers as strings with inconsistent formatting (e.g. "90.0"
        for an int we sent as 90), so a naive `!=` would false-positive on
        a successful write.
        """
        if actual is None:
            return sent is None

        sent_str = str(sent).strip().lower()
        actual_str = str(actual).strip().lower()
        if sent_str == actual_str:
            return True
        if sent_str in cls._TRUE_STRINGS and actual_str in cls._TRUE_STRINGS:
            return True
        if sent_str in cls._FALSE_STRINGS and actual_str in cls._FALSE_STRINGS:
            return True
        try:
            return float(sent_str) == float(actual_str)
        except (TypeError, ValueError):
            return False

    async def async_write_plant_price(self, serial: str, price: float) -> None:
        """Set a manual constant electricity price for the inverter's plant.

        This is a plant-level (not inverter-level) setting, reached via a
        different API endpoint than async_write_setting. It replaces the
        plant's *entire* pricing configuration with a single Constant
        Price (type 1) entry covering the full day — any existing
        Time-of-Use or Live Price configuration on the Sunsynk portal for
        this plant is overwritten. Currency and investment figures are
        read fresh and passed straight through unchanged.
        """
        session = await self._async_get_session()

        try:
            token = await self._auth.async_get_token(session)
        except SunsynkAuthError as err:
            raise UpdateFailed(f"Authentication failed: {err}") from err

        client = SunsynkClient(self._auth._api_server, token)

        plant_id = (self.data or {}).get(serial, {}).get("plant", {}).get("id")
        if not plant_id:
            # Cache may simply predate a successful plant lookup (e.g. right
            # after startup, or a previous refresh's plant fetch failed) —
            # try once more against a fresh inverter-info fetch before
            # concluding there's genuinely no plant linked to this inverter.
            try:
                inverter_info = await client.async_get_inverter_info(session, serial)
            except SunsynkApiError as err:
                raise UpdateFailed(f"Cannot read inverter info for {serial}: {err}") from err
            plant_id = (inverter_info.get("plant") or {}).get("id")
            if not plant_id:
                raise UpdateFailed(
                    f"No plant found for inverter {serial} — this Sunsynk/Deye "
                    "account may not have a plant linked to this inverter."
                )

        try:
            plant = await client.async_get_plant_info(session, str(plant_id))
        except SunsynkApiError as err:
            raise UpdateFailed(f"Cannot read plant info for {plant_id}: {err}") from err

        payload = {
            "id": plant.get("id"),
            "currency": (plant.get("currency") or {}).get("id"),
            "invest": plant.get("invest"),
            "charges": [
                {"price": price, "type": 1, "startRange": "00:00", "endRange": "24:00"}
            ],
        }

        try:
            await client.async_set_plant_income(session, str(plant_id), payload)
        except SunsynkApiError as err:
            raise UpdateFailed(f"Failed to write plant price for {plant_id}: {err}") from err

        await self.async_request_refresh()

    async def async_close(self) -> None:
        """Cancel pending write confirmations, then close the aiohttp session."""
        SunsynkCoordinator._ensure_verify_state(self)
        verify_tasks = list(self._verify_tasks)
        for task in verify_tasks:
            task.cancel()
        if verify_tasks:
            await asyncio.gather(*verify_tasks, return_exceptions=True)
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
        calibrator: PerformanceRatioCalibrator | None = None,
        actual_energy_fn: Callable[[], float | None] | None = None,
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
        self._calibrator = calibrator
        self._actual_energy_fn = actual_energy_fn
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

        return await self._parse(raw)

    async def _parse(self, raw: dict[str, Any]) -> dict[str, Any]:
        hourly = raw.get("hourly", {})
        times: list[str] = hourly.get("time", [])
        ghi: list[Any] = hourly.get("shortwave_radiation", [])
        dni: list[Any] = hourly.get("direct_normal_irradiance", [])
        cloud: list[Any] = hourly.get("cloud_cover", [])
        precip: list[Any] = hourly.get("precipitation", [])

        now = dt_util.now()
        today = now.date()
        tomorrow = today + timedelta(days=1)

        performance_ratio = (
            self._calibrator.get_ratio(now.month)
            if self._calibrator is not None
            else self._performance_ratio
        )

        today_raw_kwh = 0.0  # ratio=1 irradiance model, used to calibrate the ratio
        today_kwh = 0.0
        tomorrow_kwh = 0.0
        for i, ts in enumerate(times):
            try:
                dt = datetime.fromisoformat(ts)
            except ValueError:
                continue
            g = float(ghi[i]) if i < len(ghi) and ghi[i] is not None else 0.0
            raw_contribution = g / 1000.0 * self._panel_kwp
            if dt.date() == today:
                today_raw_kwh += raw_contribution
                today_kwh += raw_contribution * performance_ratio
            elif dt.date() == tomorrow:
                tomorrow_kwh += raw_contribution * performance_ratio

        if self._calibrator is not None and self._actual_energy_fn is not None:
            await self._calibrator.async_update(
                today, today_raw_kwh, self._actual_energy_fn()
            )

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
            "performance_ratio": round(performance_ratio, 3),
        }

    async def async_close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()

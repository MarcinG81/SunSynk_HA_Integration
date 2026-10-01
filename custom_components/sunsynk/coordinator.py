"""DataUpdateCoordinator for Sunsynk integration."""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from typing import Any

import aiohttp
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api.auth import SunsynkAuth, SunsynkAuthError
from .api.client import SunsynkApiError, SunsynkAuthenticationError, SunsynkClient
from .calibration import PerformanceRatioCalibrator
from .const import (
    BATTERY_SETTING_KEYS,
    DOMAIN,
    SLOT_SETTING_KEY_GROUPS,
    SOLAR_FORECAST_UPDATE_INTERVAL,
    SYSTEM_MODE_SETTING_KEYS,
)
from .write_validation import (
    WRITABLE_SETTING_KEYS,
    SunsynkSettingValidationError,
    validate_plant_price,
    validate_setting_batch,
    validate_setting_value,
)

_LOGGER = logging.getLogger(__name__)

# Some accounts (observed on a parallel/multi-inverter setup, #21) apparently
# relay a settings write to the physical inverter asynchronously — the write
# The write endpoint acknowledges before a command has necessarily propagated.
# Verify adaptively instead of blocking every per-inverter write queue for a
# fixed two seconds: most writes settle after the first short delay, while
# slower relays still get the same two-second propagation window.
_VERIFY_WRITE_RETRY_DELAYS = (0.25, 0.5, 1.25)


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
        self._write_locks: dict[str, asyncio.Lock] = {}
        self._pending_setting_writes: dict[str, dict[str, Any]] = {}
        self._pending_setting_waiters: dict[
            str, list[tuple[asyncio.Future[None], frozenset[str]]]
        ] = {}
        self._write_drain_scheduled: set[str] = set()
        self._write_drain_tasks: dict[str, asyncio.Task[None]] = {}

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

        def _info(serial: str) -> dict[str, Any]:
            return (self.data or {}).get(serial, {}).get("inverter", {})

        has_master = any(
            _info(s).get("parallel") and _info(s).get("equipMode") == 1
            for s in self.serials
        )
        if not has_master:
            # No confirmed master anywhere (e.g. a momentary bad poll) —
            # don't guess at dropping a serial with nothing left to cover it.
            return list(self.serials)

        return [
            serial
            for serial in self.serials
            if not (
                _info(serial).get("parallel") and _info(serial).get("equipMode") == 0
            )
        ]

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
        inverter_failures: list[tuple[str, Exception]] = []
        for serial in self.serials:
            try:
                result[serial] = await client.async_fetch_all(session, serial)
                _LOGGER.debug("Data updated for inverter %s", serial)
                ir.async_delete_issue(self.hass, DOMAIN, f"inverter_offline_{serial}")
            except SunsynkAuthenticationError as err:
                self._auth.invalidate_token()
                ir.async_create_issue(
                    self.hass,
                    DOMAIN,
                    f"auth_failed_{self._entry_id}",
                    is_fixable=False,
                    severity=ir.IssueSeverity.ERROR,
                    translation_key="auth_failed",
                )
                raise UpdateFailed(f"Authentication failed: {err}") from err
            except Exception as err:  # noqa: BLE001
                _LOGGER.error("Failed to fetch data for inverter %s: %s", serial, err)
                inverter_failures.append((serial, err))
                # Retain non-safety-critical cached values for continuity, but
                # never carry a stale SOC into the tariff state machine.  An
                # empty battery payload deliberately triggers its fail-closed
                # path for this inverter.
                result[serial] = dict(self.data.get(serial, {})) if self.data else {}
                result[serial]["battery"] = {}
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
                except SunsynkAuthenticationError as err:
                    self._auth.invalidate_token()
                    ir.async_create_issue(
                        self.hass,
                        DOMAIN,
                        f"auth_failed_{self._entry_id}",
                        is_fixable=False,
                        severity=ir.IssueSeverity.ERROR,
                        translation_key="auth_failed",
                    )
                    raise UpdateFailed(f"Authentication failed: {err}") from err
                except Exception as err:  # noqa: BLE001
                    _LOGGER.debug(
                        "Could not fetch plant info for %s (plant %s): %s",
                        serial,
                        plant_id,
                        err,
                    )
                    result[serial]["plant"] = (
                        (self.data or {}).get(serial, {}).get("plant", {})
                    )
            else:
                result[serial]["plant"] = {}

        if self.serials and len(inverter_failures) == len(self.serials):
            serial, err = inverter_failures[0]
            raise UpdateFailed(
                f"All inverter updates failed; first failure for {serial}: {err}"
            ) from err

        return result

    def resolve_write_target(self, serial: str) -> str:
        """Return the physical inverter that owns writes for ``serial``.

        Independent inverters resolve to themselves.  A parallel-group slave
        resolves to the group's master, because settings written to the slave
        are synchronised back from the master and do not persist.

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
        inverter_info = (self.data or {}).get(serial, {}).get("inverter", {})
        if not inverter_info.get("parallel") or inverter_info.get("equipMode") == 1:
            return serial

        for other_serial in self.serials:
            if other_serial == serial:
                continue
            other_info = (self.data or {}).get(other_serial, {}).get("inverter", {})
            if other_info.get("parallel") and other_info.get("equipMode") == 1:
                return other_serial

        return serial

    def _resolve_parallel_write_target(self, serial: str, setting_key: str) -> str:
        """Backward-compatible internal wrapper for write routing."""
        return SunsynkCoordinator.resolve_write_target(self, serial)

    def _ensure_write_state(self) -> None:
        """Initialise write coordination lazily for lightweight test doubles."""
        if not hasattr(self, "_write_locks"):
            self._write_locks = {}
        if not hasattr(self, "_pending_setting_writes"):
            self._pending_setting_writes = {}
        if not hasattr(self, "_pending_setting_waiters"):
            self._pending_setting_waiters = {}
        if not hasattr(self, "_write_drain_scheduled"):
            self._write_drain_scheduled = set()
        if not hasattr(self, "_write_drain_tasks"):
            self._write_drain_tasks = {}

    def _write_lock_for(self, serial: str) -> asyncio.Lock:
        SunsynkCoordinator._ensure_write_state(self)
        return self._write_locks.setdefault(serial, asyncio.Lock())

    @staticmethod
    def _allowed_setting_group(setting_key: str) -> frozenset[str]:
        """Return the API payload group that owns one setting."""
        if setting_key in BATTERY_SETTING_KEYS:
            return BATTERY_SETTING_KEYS
        slot_keys = next(
            (keys for keys in SLOT_SETTING_KEY_GROUPS.values() if setting_key in keys),
            None,
        )
        if slot_keys is not None:
            return slot_keys
        if setting_key in SYSTEM_MODE_SETTING_KEYS:
            return SYSTEM_MODE_SETTING_KEYS
        return frozenset([setting_key])

    async def async_write_setting(
        self, serial: str, setting_key: str, value: Any
    ) -> None:
        """Queue one setting write through the shared coalescing pipeline."""
        await SunsynkCoordinator.async_write_settings(
            self, serial, {setting_key: value}
        )

    async def async_write_settings(self, serial: str, settings: dict[str, Any]) -> None:
        """Coalesce and serialize setting writes for one physical inverter.

        Calls made during the same event-loop turn are merged. Settings from
        the same API group (notably all fields of one timer slot) are emitted
        in one payload. A per-target lock prevents services, entities, tariff
        evaluation and virtual-slot ticks from racing each other.
        """
        if not settings:
            return
        # Validate the complete caller-supplied batch before mutating queue
        # state. A bad field must not allow valid siblings to leak to the API.
        settings = validate_setting_batch(settings)
        serial = SunsynkCoordinator.resolve_write_target(self, serial)
        SunsynkCoordinator._ensure_write_state(self)

        loop = asyncio.get_running_loop()
        waiter: asyncio.Future[None] = loop.create_future()
        pending = self._pending_setting_writes.setdefault(serial, {})
        pending.update(settings)
        self._pending_setting_waiters.setdefault(serial, []).append(
            (waiter, frozenset(settings))
        )

        if serial not in self._write_drain_scheduled:
            self._write_drain_scheduled.add(serial)
            loop.call_soon(
                SunsynkCoordinator._launch_write_drain,
                self,
                serial,
            )

        await waiter

    def _launch_write_drain(self, serial: str) -> None:
        """Launch a queued drain after same-turn callers have coalesced."""
        task = asyncio.get_running_loop().create_task(
            SunsynkCoordinator._async_drain_write_queue(self, serial)
        )
        self._write_drain_tasks[serial] = task

    async def _async_drain_write_queue(self, serial: str) -> None:
        """Drain all queued batches for one target under its write lock."""
        current_waiters: list[tuple[asyncio.Future[None], frozenset[str]]] = []
        try:
            while updates := self._pending_setting_writes.pop(serial, None):
                current_waiters = self._pending_setting_waiters.pop(serial, [])
                async with SunsynkCoordinator._write_lock_for(self, serial):
                    failures = await SunsynkCoordinator._async_execute_setting_batch(
                        self, serial, updates
                    )

                for waiter, keys in current_waiters:
                    if waiter.done():
                        continue
                    error = next(
                        (failures[key] for key in keys if key in failures),
                        None,
                    )
                    if error is None:
                        waiter.set_result(None)
                    else:
                        waiter.set_exception(error)
                current_waiters = []
        except Exception as err:
            # A coordinator-internal failure must never strand callers.
            pending_waiters = self._pending_setting_waiters.pop(serial, [])
            for waiter, _keys in [*current_waiters, *pending_waiters]:
                if not waiter.done():
                    waiter.set_exception(err)
            _LOGGER.exception("Setting write queue failed for inverter %s", serial)
        finally:
            self._write_drain_tasks.pop(serial, None)
            self._write_drain_scheduled.discard(serial)
            if self._pending_setting_writes.get(serial):
                self._write_drain_scheduled.add(serial)
                asyncio.get_running_loop().call_soon(
                    SunsynkCoordinator._launch_write_drain,
                    self,
                    serial,
                )

    async def _async_execute_setting_batch(
        self, serial: str, updates: dict[str, Any]
    ) -> dict[str, Exception]:
        """Write one coalesced batch and return failures keyed by setting."""
        session = await self._async_get_session()
        try:
            token = await self._auth.async_get_token(session)
        except SunsynkAuthError as err:
            error = UpdateFailed(f"Authentication failed: {err}")
            return {key: error for key in updates}

        client = SunsynkClient(self._auth._api_server, token)
        # Never put an optimistic value in the coordinator cache: the write
        # endpoint can acknowledge a command which the inverter then rejects.
        current_settings = dict((self.data or {}).get(serial, {}).get("settings", {}))
        if not current_settings:
            try:
                current_settings = dict(
                    await client.async_get_settings(session, serial)
                )
            except SunsynkAuthenticationError as err:
                self._auth.invalidate_token()
                error = UpdateFailed(f"Authentication failed: {err}")
                return {key: error for key in updates}
            except SunsynkApiError as err:
                error = UpdateFailed(f"Cannot read settings for {serial}: {err}")
                return {key: error for key in updates}

        grouped_updates: dict[frozenset[str], dict[str, Any]] = {}
        for key, value in updates.items():
            allowed_keys = SunsynkCoordinator._allowed_setting_group(key)
            grouped_updates.setdefault(allowed_keys, {})[key] = value

        failures: dict[str, Exception] = {}
        written: dict[str, Any] = {}
        for allowed_keys, group_updates in grouped_updates.items():
            payload: dict[str, Any] = {
                key: value
                for key, value in current_settings.items()
                if key in allowed_keys and value is not None
            }
            payload.update(group_updates)
            payload["sn"] = serial
            try:
                # Group writes also resend cached sibling values. Validate all
                # writable siblings, not just the caller's changed fields, so
                # a corrupt cached current/SOC/power cannot hitch a ride.
                for key in payload.keys() & WRITABLE_SETTING_KEYS:
                    payload[key] = validate_setting_value(key, payload[key])
            except SunsynkSettingValidationError as err:
                error = UpdateFailed(
                    f"Unsafe cached setting blocks write of "
                    f"{', '.join(group_updates)}: {err}"
                )
                failures.update({key: error for key in group_updates})
                continue
            try:
                await client.async_write_settings(session, serial, payload)
            except SunsynkAuthenticationError as err:
                self._auth.invalidate_token()
                error = UpdateFailed(f"Authentication failed: {err}")
                return {key: error for key in updates}
            except SunsynkApiError as err:
                error = UpdateFailed(
                    f"Failed to write settings {', '.join(group_updates)}: {err}"
                )
                failures.update({key: error for key in group_updates})
                continue

            written.update(group_updates)
            current_settings.update(payload)

        if written:
            try:
                await SunsynkCoordinator._async_verify_writes(
                    self, client, session, serial, written
                )
            except UpdateFailed as err:
                failures.update({key: err for key in written})
            else:
                await self.async_request_refresh()
        return failures

    async def _async_verify_write(
        self,
        client: SunsynkClient,
        session: aiohttp.ClientSession,
        serial: str,
        setting_key: str,
        value: Any,
    ) -> None:
        """Backward-compatible single-setting verification wrapper."""
        await SunsynkCoordinator._async_verify_writes(
            self, client, session, serial, {setting_key: value}
        )

    async def _async_verify_writes(
        self,
        client: SunsynkClient,
        session: aiohttp.ClientSession,
        serial: str,
        expected: dict[str, Any],
    ) -> None:
        """Fail-safe: verify a whole batch with adaptive delayed API reads.

        The write endpoint returns success even when the inverter silently
        ignores a value (out-of-range, conflicting with another setting,
        dongle briefly offline), so a 200 response alone doesn't prove the
        change actually took. Re-reading once confirms every field in the
        coalesced batch and raises a Repair for each mismatch. Verification
        starts after a short delay and retries only while propagation is still
        pending, avoiding a fixed two-second delay on every successful write.
        """
        fresh_settings: dict[str, Any] = {}
        for delay in _VERIFY_WRITE_RETRY_DELAYS:
            await asyncio.sleep(delay)
            try:
                fresh_settings = await client.async_get_settings(session, serial)
            except SunsynkAuthenticationError as err:
                self._auth.invalidate_token()
                raise UpdateFailed(f"Authentication failed: {err}") from err
            except SunsynkApiError as err:
                raise UpdateFailed(
                    f"Could not verify writes of {', '.join(expected)} for "
                    f"{serial}: {err}"
                ) from err

            if all(
                SunsynkCoordinator._values_match(value, fresh_settings.get(key))
                for key, value in expected.items()
            ):
                break

        # Replace any assumptions with the authoritative read-back before
        # notifying consumers. This also rolls the cache back when the device
        # silently ignored one or more values.
        if self.data is not None and serial in self.data:
            self.data[serial].setdefault("settings", {}).update(fresh_settings)

        mismatches: list[str] = []
        for setting_key, value in expected.items():
            issue_id = f"setting_write_mismatch_{serial}_{setting_key}"
            actual = fresh_settings.get(setting_key)
            if SunsynkCoordinator._values_match(value, actual):
                ir.async_delete_issue(self.hass, DOMAIN, issue_id)
                continue

            _LOGGER.warning(
                "Write verification failed for %s on inverter %s: sent %r, "
                "inverter reports %r",
                setting_key,
                serial,
                value,
                actual,
            )
            mismatches.append(f"{setting_key}: expected {value!r}, actual {actual!r}")
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

        if mismatches:
            raise UpdateFailed(
                f"Inverter {serial} rejected setting write(s): " + "; ".join(mismatches)
            )

    _TRUE_STRINGS = frozenset({"true", "1"})
    _FALSE_STRINGS = frozenset({"false", "0"})
    _TIME_RE = re.compile(r"([01]?\d|2[0-3]):([0-5]\d)")

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

        sent_time = cls._TIME_RE.fullmatch(sent_str)
        actual_time = cls._TIME_RE.fullmatch(actual_str)
        if sent_time is not None and actual_time is not None:
            sent_minutes = int(sent_time[1]) * 60 + int(sent_time[2])
            actual_minutes = int(actual_time[1]) * 60 + int(actual_time[2])
            return sent_minutes == actual_minutes

        try:
            return float(sent_str) == float(actual_str)
        except (TypeError, ValueError):
            return False

    async def async_write_plant_price(self, serial: str, price: float) -> None:
        """Serialize a plant-price write with every inverter setting write."""
        price = validate_plant_price(price)
        serial = SunsynkCoordinator.resolve_write_target(self, serial)
        async with SunsynkCoordinator._write_lock_for(self, serial):
            await SunsynkCoordinator._async_write_plant_price_locked(
                self, serial, price
            )

    async def _async_write_plant_price_locked(self, serial: str, price: float) -> None:
        """Set a manual constant electricity price for the inverter's plant.

        This is a plant-level (not inverter-level) setting. For safety it
        only edits an already-existing single Constant Price entry. A
        Time-of-Use, live-price or multi-entry configuration is rejected
        rather than destructively replaced. Currency and investment figures
        are read fresh and passed through unchanged.
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
            except SunsynkAuthenticationError as err:
                self._auth.invalidate_token()
                raise UpdateFailed(f"Authentication failed: {err}") from err
            except SunsynkApiError as err:
                raise UpdateFailed(
                    f"Cannot read inverter info for {serial}: {err}"
                ) from err
            plant_id = (inverter_info.get("plant") or {}).get("id")
            if not plant_id:
                raise UpdateFailed(
                    f"No plant found for inverter {serial} — this Sunsynk/Deye "
                    "account may not have a plant linked to this inverter."
                )

        try:
            plant = await client.async_get_plant_info(session, str(plant_id))
        except SunsynkAuthenticationError as err:
            self._auth.invalidate_token()
            raise UpdateFailed(f"Authentication failed: {err}") from err
        except SunsynkApiError as err:
            raise UpdateFailed(f"Cannot read plant info for {plant_id}: {err}") from err

        charges = plant.get("charges")
        only_charge = (
            charges[0] if isinstance(charges, list) and len(charges) == 1 else None
        )
        if not isinstance(only_charge, dict) or str(only_charge.get("type")) != "1":
            raise UpdateFailed(
                "Plant price can only be changed when the Sunsynk plant already "
                "uses exactly one Constant Price entry. The existing Time-of-Use, "
                "live-price or multi-entry tariff was left unchanged."
            )

        plant_response_id = plant.get("id")
        currency_id = (plant.get("currency") or {}).get("id")
        if (
            plant_response_id is None
            or str(plant_response_id) != str(plant_id)
            or currency_id is None
            or "invest" not in plant
        ):
            raise UpdateFailed(
                f"Plant {plant_id} pricing metadata is incomplete; no changes were made"
            )

        updated_charge = dict(only_charge)
        updated_charge["price"] = price

        payload = {
            "id": plant_response_id,
            "currency": currency_id,
            "invest": plant.get("invest"),
            "charges": [updated_charge],
        }

        try:
            await client.async_set_plant_income(session, str(plant_id), payload)
        except SunsynkAuthenticationError as err:
            self._auth.invalidate_token()
            raise UpdateFailed(f"Authentication failed: {err}") from err
        except SunsynkApiError as err:
            raise UpdateFailed(
                f"Failed to write plant price for {plant_id}: {err}"
            ) from err

        await self.async_request_refresh()

    async def async_close(self) -> None:
        """Flush active write drains, then close the aiohttp session."""
        SunsynkCoordinator._ensure_write_state(self)
        await asyncio.sleep(0)
        tasks = list(self._write_drain_tasks.values())
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
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
        if not (
            -90 <= latitude <= 90
            and -180 <= longitude <= 180
            and panel_kwp > 0
            and 0 < performance_ratio <= 1
        ):
            raise ValueError("Invalid solar forecast configuration")
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
        offset = raw.get("utc_offset_seconds")
        if isinstance(offset, (int, float)) and -86400 < offset < 86400:
            # Open-Meteo's hourly timestamps are naive wall-clock values in
            # the requested location. Compare them against that location's
            # date/hour rather than Home Assistant's potentially different
            # timezone.
            now = now.astimezone(timezone(timedelta(seconds=offset)))
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

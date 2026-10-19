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
    SOLAR_FORECAST_UPDATE_INTERVAL,
    SYSTEM_MODE_PAYLOAD_KEYS,
)
from .write_validation import (
    WRITABLE_SETTING_KEYS,
    SunsynkSettingValidationError,
    validate_cached_sibling,
    validate_plant_price,
    validate_setting_batch,
    validate_setting_value,
)

_LOGGER = logging.getLogger(__name__)

# The write endpoint acknowledges before a command has necessarily reached the
# inverter, and how long that takes varies a lot between installations: a
# single Deye inverter (#31) still reported the previous value 2 s after every
# write, yet had applied it a few minutes later. Writes are therefore
# confirmed in the background at these offsets (seconds after the write),
# stopping at the first read-back that matches. Only a value that still hasn't
# arrived after the last offset is reported as rejected.
_VERIFY_WRITE_OFFSETS = (5, 10, 15, 30, 60)

# A write the inverter still hasn't applied after the last offset, while it
# still reports the value from before the write, was lost rather than
# refused: two writes close together can cross, and one of them never lands
# (#36: a discharge left Sell ticked and the inverter exported over 20 kW).
# It is sent again this many times before it is reported. A different value
# by then means something else changed it, e.g. the Sunsynk app, and it is
# left alone.
_MAX_RESENDS = 3
_UNKNOWN = object()


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
            if SunsynkCoordinator.resolve_write_target(self, serial) == serial
        ]

    def _parallel_role(self, serial: str) -> str | None:
        """Return "master", "slave", or None outside a parallel group.

        The API reports `parallel` and `equipMode` (1 = master, 0 = slave) in
        the *settings* payload: a real parallel diagnostics dump (#21) had
        them nowhere else. Reading them from the inverter info, as before,
        meant no parallel group was ever recognised. The inverter info is
        still checked as a fallback.
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
        inverter_failures: list[tuple[str, Exception]] = []
        for serial in self.serials:
            try:
                result[serial] = await client.async_fetch_all(session, serial)
                SunsynkCoordinator._overlay_unconfirmed_writes(
                    self, serial, result[serial]
                )
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
            # whose plant is unknown could belong to another installation
            # (2.0 audit N2).
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
        if not hasattr(self, "_unconfirmed_writes"):
            self._unconfirmed_writes = {}
        if not hasattr(self, "_write_seq"):
            self._write_seq = 0
        if not hasattr(self, "_verify_tasks"):
            self._verify_tasks = set()
        if not hasattr(self, "_resends"):
            self._resends = {}

    def _write_lock_for(self, serial: str) -> asyncio.Lock:
        SunsynkCoordinator._ensure_write_state(self)
        return self._write_locks.setdefault(serial, asyncio.Lock())

    @staticmethod
    def _allowed_setting_group(setting_key: str) -> frozenset[str]:
        """Return the API payload group that owns one setting."""
        if setting_key in BATTERY_SETTING_KEYS:
            return BATTERY_SETTING_KEYS
        if setting_key in SYSTEM_MODE_PAYLOAD_KEYS:
            return SYSTEM_MODE_PAYLOAD_KEYS
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
        requested = serial
        serial = SunsynkCoordinator.resolve_write_target(self, serial)
        if serial != requested:
            _LOGGER.debug(
                "Write for parallel slave %s goes to its master %s: %s",
                requested,
                serial,
                settings,
            )
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
        # Read-modify-write from a fresh read, as Predbat and other clients
        # do: building on a cache up to one poll old would send back, and so
        # undo, anything changed in the Sunsynk app since that poll. Values we
        # wrote that the inverter hasn't confirmed yet are kept over the read.
        cached_settings = dict((self.data or {}).get(serial, {}).get("settings", {}))
        try:
            current_settings = dict(await client.async_get_settings(session, serial))
        except SunsynkAuthenticationError as err:
            self._auth.invalidate_token()
            error = UpdateFailed(f"Authentication failed: {err}")
            return {key: error for key in updates}
        except Exception as err:  # noqa: BLE001
            if not cached_settings:
                error = UpdateFailed(f"Cannot read settings for {serial}: {err}")
                return {key: error for key in updates}
            _LOGGER.debug(
                "Fresh settings read for %s failed, using cached settings: %s",
                serial,
                err,
            )
            current_settings = cached_settings
        else:
            if current_settings:
                SunsynkCoordinator._overlay_unconfirmed_writes(
                    self, serial, {"settings": current_settings}
                )
            else:
                current_settings = cached_settings

        grouped_updates: dict[frozenset[str], dict[str, Any]] = {}
        for key, value in updates.items():
            allowed_keys = SunsynkCoordinator._allowed_setting_group(key)
            grouped_updates.setdefault(allowed_keys, {})[key] = value

        failures: dict[str, Exception] = {}
        written: dict[str, Any] = {}
        before: dict[str, Any] = {}
        requested: dict[str, Any] = {}
        for allowed_keys, group_updates in grouped_updates.items():
            payload: dict[str, Any] = {
                key: value
                for key, value in current_settings.items()
                if key in allowed_keys and value is not None
            }
            payload.update(group_updates)
            payload["sn"] = serial
            # Siblings are the inverter's own current values (from the fresh
            # read) and are resent exactly as reported, as the Sunsynk portal
            # and Predbat do. An out-of-range one is the inverter's business,
            # not something to refuse over: a parallel master reporting
            # chargeCurrent 1040 blocked every battery write and with it all
            # of Tariff Manager (#21), as battMode -1 had before (#32). Only
            # the values being changed must pass validation.
            try:
                for key in group_updates.keys() & WRITABLE_SETTING_KEYS:
                    payload[key] = SunsynkCoordinator._as_reported(
                        validate_setting_value(key, payload[key]),
                        current_settings.get(key),
                    )
            except SunsynkSettingValidationError as err:
                error = UpdateFailed(
                    f"Refusing to write {', '.join(group_updates)}: {err}"
                )
                failures.update({key: error for key in group_updates})
                continue
            for key in (payload.keys() & WRITABLE_SETTING_KEYS) - group_updates.keys():
                try:
                    validate_cached_sibling(key, payload[key])
                except SunsynkSettingValidationError as err:
                    _LOGGER.debug(
                        "Inverter %s reports an unusual %s, resent unchanged: %s",
                        serial,
                        key,
                        err,
                    )
            # The changed values only; the rest of the group is resent as read.
            _LOGGER.debug(
                "Writing to inverter %s: %s (%d fields in the request)",
                serial,
                {key: payload[key] for key in group_updates},
                len(payload),
            )
            try:
                await client.async_write_settings(session, serial, payload)
            except SunsynkAuthenticationError as err:
                self._auth.invalidate_token()
                _LOGGER.warning(
                    "Write to inverter %s refused, logging in again on the next "
                    "attempt: %s",
                    serial,
                    err,
                )
                error = UpdateFailed(f"Authentication failed: {err}")
                return {key: error for key in updates}
            except SunsynkApiError as err:
                _LOGGER.warning(
                    "Write of %s to inverter %s failed: %s",
                    ", ".join(group_updates),
                    serial,
                    err,
                )
                error = UpdateFailed(
                    f"Failed to write settings {', '.join(group_updates)}: {err}"
                )
                failures.update({key: error for key in group_updates})
                continue

            # Exactly what went out, in the inverter's own format, so the
            # cache and the confirmation compare like with like.
            written.update({key: payload[key] for key in group_updates})
            before.update(
                {
                    key: current_settings[key]
                    for key in group_updates
                    if key in current_settings
                }
            )
            requested.update(group_updates)
            current_settings.update(payload)

        if written:
            SunsynkCoordinator._track_unconfirmed_writes(
                self, serial, written, before, requested
            )
            await self.async_request_refresh()
        return failures

    @staticmethod
    def _as_reported(value: Any, reported: Any) -> Any:
        """Encode a changed value the way the inverter reports that field.

        Predbat confirmed live that the API silently drops a flag sent in a
        different form from its read-back (e.g. 1 or a bare boolean for a
        time{n}on reported as "true") while accepting the rest of the write
        (#32). Deye reports flags as "true"/"false" and sellTime{n}En as
        "0"/"1"; some Sunsynk models report real booleans.
        """
        if reported is None:
            return value
        if isinstance(value, bool):
            value = int(value)
        if isinstance(reported, bool):
            return bool(value)
        if isinstance(reported, str):
            if reported.strip().lower() in ("true", "false") and value in (0, 1):
                return "true" if value else "false"
            return str(value)
        return value

    def _track_unconfirmed_writes(
        self,
        serial: str,
        written: dict[str, Any],
        before: dict[str, Any] | None = None,
        requested: dict[str, Any] | None = None,
    ) -> None:
        """Hold just-written values until the inverter confirms them.

        Until confirmation, the cache keeps the value we sent rather than an
        older one read back too early. Otherwise the next write to the same
        group (e.g. a slot's limit after its start time) would resend the old
        sibling value and undo the earlier change (#31).
        """
        SunsynkCoordinator._ensure_write_state(self)
        write_ids: dict[str, int] = {}
        unconfirmed = self._unconfirmed_writes.setdefault(serial, {})
        for key, value in written.items():
            self._write_seq += 1
            write_ids[key] = self._write_seq
            unconfirmed[key] = (self._write_seq, value)
        if self.data is not None and serial in self.data:
            self.data[serial].setdefault("settings", {}).update(written)
            SunsynkCoordinator._notify_settings_changed(self)

        task = asyncio.get_running_loop().create_task(
            SunsynkCoordinator._async_confirm_writes(
                self,
                serial,
                write_ids,
                dict(written),
                dict(before or {}),
                dict(requested or written),
            )
        )
        self._verify_tasks.add(task)
        task.add_done_callback(self._verify_tasks.discard)

    def _notify_settings_changed(self) -> None:
        notify = getattr(self, "async_update_listeners", None)
        if notify is not None:
            notify()

    def _is_unconfirmed(self, serial: str, setting_key: str, write_id: int) -> bool:
        entry = self._unconfirmed_writes.get(serial, {}).get(setting_key)
        return entry is not None and entry[0] == write_id

    def _overlay_unconfirmed_writes(self, serial: str, data: dict[str, Any]) -> None:
        """Keep unconfirmed values over a poll that may predate them."""
        SunsynkCoordinator._ensure_write_state(self)
        unconfirmed = self._unconfirmed_writes.get(serial)
        if unconfirmed:
            settings = data.setdefault("settings", {})
            settings.update({key: value for key, (_id, value) in unconfirmed.items()})

    async def _async_read_settings(self, serial: str) -> dict[str, Any]:
        session = await self._async_get_session()
        token = await self._auth.async_get_token(session)
        client = SunsynkClient(self._auth._api_server, token)
        return await client.async_get_settings(session, serial)

    async def _async_confirm_writes(
        self,
        serial: str,
        write_ids: dict[str, int],
        expected: dict[str, Any],
        before: dict[str, Any] | None = None,
        requested: dict[str, Any] | None = None,
    ) -> None:
        """Confirm a batch in the background, stopping at the first match.

        The write endpoint returns success even when the inverter silently
        ignores a value (out-of-range, conflicting with another setting,
        dongle offline), so a 200 response alone doesn't prove the change
        took. A Repair is raised only for a value that still hasn't arrived
        after the last offset. A newer write of the same setting supersedes
        this confirmation.
        """
        try:
            await SunsynkCoordinator._async_confirm_writes_until_settled(
                self, serial, write_ids, expected, before, requested
            )
        except Exception:
            _LOGGER.exception("Could not confirm writes for %s", serial)
        finally:
            # Whatever ended the confirmation, never leave this batch's values
            # overlaying fresh polls with nothing left to settle them. A newer
            # write of the same key has its own id and is left alone.
            for key, write_id in write_ids.items():
                if SunsynkCoordinator._is_unconfirmed(self, serial, key, write_id):
                    del self._unconfirmed_writes[serial][key]

    async def _async_confirm_writes_until_settled(
        self,
        serial: str,
        write_ids: dict[str, int],
        expected: dict[str, Any],
        before: dict[str, Any] | None = None,
        requested: dict[str, Any] | None = None,
    ) -> None:
        loop = asyncio.get_running_loop()
        started = loop.time()
        remaining = dict(expected)
        fresh_settings: dict[str, Any] | None = None
        for offset in _VERIFY_WRITE_OFFSETS:
            await asyncio.sleep(max(0.0, started + offset - loop.time()))
            remaining = {
                key: value
                for key, value in remaining.items()
                if SunsynkCoordinator._is_unconfirmed(self, serial, key, write_ids[key])
            }
            if not remaining:
                return
            try:
                fresh_settings = await SunsynkCoordinator._async_read_settings(
                    self, serial
                )
            except SunsynkAuthenticationError as err:
                self._auth.invalidate_token()
                _LOGGER.debug("Could not confirm writes for %s: %s", serial, err)
                continue
            except (SunsynkAuthError, SunsynkApiError) as err:
                _LOGGER.debug("Could not confirm writes for %s: %s", serial, err)
                continue
            for key, value in list(remaining.items()):
                if SunsynkCoordinator._values_match(value, fresh_settings.get(key)):
                    SunsynkCoordinator._settle_write(
                        self,
                        serial,
                        key,
                        write_ids[key],
                        value,
                        fresh_settings.get(key),
                    )
                    del remaining[key]
            if not remaining:
                return

        resend: dict[str, Any] = {}
        for key, value in remaining.items():
            if fresh_settings is not None and SunsynkCoordinator._was_lost(
                self,
                serial,
                key,
                value,
                fresh_settings.get(key),
                (before or {}).get(key, _UNKNOWN),
                write_ids[key],
            ):
                resend[key] = (requested or expected).get(key, value)
                continue
            if fresh_settings is None:
                # Never managed to read back: we know nothing, so don't claim a
                # rejection. The next poll shows the real value.
                if SunsynkCoordinator._is_unconfirmed(
                    self, serial, key, write_ids[key]
                ):
                    del self._unconfirmed_writes[serial][key]
                _LOGGER.warning(
                    "Could not confirm write of %s on inverter %s", key, serial
                )
                continue
            SunsynkCoordinator._settle_write(
                self, serial, key, write_ids[key], value, fresh_settings.get(key)
            )
        if resend:
            _LOGGER.warning(
                "Inverter %s still reports the old %s after %ss, the write was "
                "lost: sending it again",
                serial,
                ", ".join(resend),
                _VERIFY_WRITE_OFFSETS[-1],
            )
            # The lost values no longer overlay what the inverter reports, so
            # the next attempt is checked against what it really has.
            for key in resend:
                if SunsynkCoordinator._is_unconfirmed(
                    self, serial, key, write_ids[key]
                ):
                    del self._unconfirmed_writes[serial][key]
            try:
                await SunsynkCoordinator.async_write_settings(self, serial, resend)
            except Exception as err:  # noqa: BLE001
                _LOGGER.error(
                    "Could not send %s to inverter %s again: %s",
                    ", ".join(resend),
                    serial,
                    err,
                )

    def _was_lost(
        self,
        serial: str,
        key: str,
        sent: Any,
        actual: Any,
        before: Any,
        write_id: int,
    ) -> bool:
        """Whether a write never landed and should be sent again.

        Only while the inverter still reports the value from before the
        write, the write is still the latest for that setting, and it hasn't
        been sent _MAX_RESENDS times already.
        """
        if (
            before is _UNKNOWN
            or actual is None
            or not SunsynkCoordinator._is_unconfirmed(self, serial, key, write_id)
            or SunsynkCoordinator._values_match(sent, actual)
            or SunsynkCoordinator._values_match(sent, before)
            or not SunsynkCoordinator._values_match(before, actual)
        ):
            return False
        SunsynkCoordinator._ensure_write_state(self)
        last_sent, count = self._resends.get((serial, key), (_UNKNOWN, 0))
        if last_sent is _UNKNOWN or not SunsynkCoordinator._values_match(
            last_sent, sent
        ):
            count = 0
        if count >= _MAX_RESENDS:
            self._resends.pop((serial, key), None)
            return False
        self._resends[(serial, key)] = (sent, count + 1)
        return True

    def _settle_write(
        self,
        serial: str,
        setting_key: str,
        write_id: int,
        sent: Any,
        actual: Any,
    ) -> None:
        """Record the inverter's final answer for one unconfirmed write."""
        if not SunsynkCoordinator._is_unconfirmed(self, serial, setting_key, write_id):
            return
        del self._unconfirmed_writes[serial][setting_key]
        if self.data is not None and serial in self.data and actual is not None:
            self.data[serial].setdefault("settings", {})[setting_key] = actual
            SunsynkCoordinator._notify_settings_changed(self)

        issue_id = f"setting_write_mismatch_{serial}_{setting_key}"
        SunsynkCoordinator._ensure_write_state(self)
        self._resends.pop((serial, setting_key), None)
        if SunsynkCoordinator._values_match(sent, actual):
            ir.async_delete_issue(self.hass, DOMAIN, issue_id)
            return

        _LOGGER.warning(
            "Write verification failed for %s on inverter %s: sent %r, "
            "inverter still reports %r after %ss",
            setting_key,
            serial,
            sent,
            actual,
            _VERIFY_WRITE_OFFSETS[-1],
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
                "expected": str(sent),
                "actual": str(actual),
            },
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

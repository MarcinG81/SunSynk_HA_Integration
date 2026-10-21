"""Back up an inverter's settings to a file, and put them back.

A backup is everything the inverter reports in its settings, saved as JSON in
`<config>/sunsynk_backups/<serial>_<date>.json`. A restore writes back only
the settings this integration knows how to write and check, and only those
that differ from what the inverter has now. It shows what it would change
first (a dry run is the default), and refuses while Tariff Manager or the
Virtual Slot Scheduler are driving the inverter, so a restore can't fight
them.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.util import dt as dt_util

from .coordinator import SunsynkCoordinator
from .deyecloud.coordinator import DeyeCloudCoordinator
from .write_validation import (
    WRITABLE_SETTING_KEYS,
    SunsynkSettingValidationError,
    validate_setting_value,
)

_LOGGER = logging.getLogger(__name__)

BACKUP_DIR = "sunsynk_backups"
FORMAT_VERSION = 1
# A file name in the backup folder, never a path.
_FILE_NAME = re.compile(r"^[A-Za-z0-9_.-]+\.json$")


def backup_dir(hass: HomeAssistant) -> Path:
    return Path(hass.config.path(BACKUP_DIR))


async def _async_current_settings(
    coordinator: SunsynkCoordinator, serial: str
) -> dict[str, Any]:
    """The inverter's settings now: a fresh read where the cloud allows."""
    cached = dict((coordinator.data or {}).get(serial, {}).get("settings", {}))
    if isinstance(coordinator, DeyeCloudCoordinator):
        return cached  # read every refresh; no separate settings read
    try:
        fresh = dict(await coordinator._async_read_settings(serial))
    except Exception as err:
        raise ServiceValidationError(
            f"Couldn't read the settings of inverter {serial}: {err}"
        ) from err
    return fresh or cached


async def async_backup(
    hass: HomeAssistant, coordinator: SunsynkCoordinator, serial: str
) -> dict[str, Any]:
    """Save the inverter's settings; returns the file name and how many."""
    settings = await _async_current_settings(coordinator, serial)
    if not settings:
        raise ServiceValidationError(f"Inverter {serial} reports no settings yet")
    created = dt_util.now()
    name = f"{serial}_{created:%Y%m%d-%H%M%S}.json"
    payload = {
        "format": FORMAT_VERSION,
        "serial": serial,
        "created": created.isoformat(),
        "settings": settings,
    }

    def _write() -> None:
        folder = backup_dir(hass)
        folder.mkdir(exist_ok=True)
        (folder / name).write_text(
            json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False),
            "utf-8",
        )

    await hass.async_add_executor_job(_write)
    _LOGGER.info("Saved %s settings of inverter %s to %s", len(settings), serial, name)
    return {"file": name, "settings": len(settings)}


async def _async_load(hass: HomeAssistant, file: str) -> dict[str, Any]:
    if not _FILE_NAME.fullmatch(file):
        raise ServiceValidationError(
            f"{file!r} isn't a backup file name, e.g. 2301316667_20261021-180000.json"
        )
    path = backup_dir(hass) / file

    def _read() -> dict[str, Any]:
        return json.loads(path.read_text("utf-8"))

    try:
        data = await hass.async_add_executor_job(_read)
    except FileNotFoundError as err:
        raise ServiceValidationError(f"No backup {file} in {BACKUP_DIR}") from err
    except (OSError, ValueError) as err:
        raise ServiceValidationError(f"Couldn't read backup {file}: {err}") from err
    if not isinstance(data, dict) or not isinstance(data.get("settings"), dict):
        raise ServiceValidationError(f"{file} isn't a Sunsynk settings backup")
    return data


def plan_restore(
    saved: dict[str, Any], current: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, str]]:
    """What a restore would write, and what it leaves out and why."""
    changes: dict[str, Any] = {}
    skipped: dict[str, str] = {}
    for key, value in sorted(saved.items()):
        if key not in WRITABLE_SETTING_KEYS:
            continue  # reported only, or not something this integration writes
        if SunsynkCoordinator._values_match(value, current.get(key)):
            continue
        try:
            validate_setting_value(key, value)
        except SunsynkSettingValidationError as err:
            skipped[key] = str(err)
            continue
        changes[key] = value
    return changes, skipped


async def async_restore(
    hass: HomeAssistant,
    coordinator: SunsynkCoordinator,
    serial: str,
    file: str,
    *,
    dry_run: bool,
    busy: str | None,
) -> dict[str, Any]:
    """Put a backup's settings back, or with `dry_run` only list them.

    `busy` says why the inverter can't be changed now (Tariff Manager or the
    Virtual Slot Scheduler driving it), if so.
    """
    backup = await _async_load(hass, file)
    if backup.get("serial") not in (None, serial):
        raise ServiceValidationError(
            f"{file} is a backup of inverter {backup['serial']}, not {serial}"
        )
    current = await _async_current_settings(coordinator, serial)
    changes, skipped = plan_restore(backup["settings"], current)
    result = {
        "file": file,
        "dry_run": dry_run,
        "changes": {
            key: {"now": current.get(key), "backup": value}
            for key, value in changes.items()
        },
        "skipped": skipped,
    }
    if dry_run or not changes:
        return result
    if busy:
        raise ServiceValidationError(f"Not restoring now: {busy}")
    await coordinator.async_write_settings(serial, changes)
    _LOGGER.warning(
        "Restored %s settings of inverter %s from %s: %s",
        len(changes),
        serial,
        file,
        ", ".join(changes),
    )
    return result

"""Backing up an inverter's settings, and putting them back."""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.exceptions import ServiceValidationError

from custom_components import sunsynk as integration
from custom_components.sunsynk.backup import (
    async_backup,
    async_restore,
    plan_restore,
)
from custom_components.sunsynk.const import DOMAIN
from custom_components.sunsynk.deyecloud.coordinator import DeyeCloudCoordinator

SETTINGS = {
    "sn": "INV1",
    "cap1": "50",
    "sellTime1": "06:30",
    "time1on": "true",
    "batteryMaxCurrentCharge": "120",
    "battMode": "-1",
    "unknownField": "x",
}


@pytest.fixture
def hass(tmp_path):
    hass = MagicMock()
    hass.config.path = lambda *parts: str(tmp_path.joinpath(*parts))

    async def run(function, *args):
        return function(*args)

    hass.async_add_executor_job = run
    return hass


@pytest.fixture
def coordinator():
    coordinator = MagicMock()
    coordinator.data = {"INV1": {"settings": dict(SETTINGS)}}
    coordinator._async_read_settings = AsyncMock(return_value=dict(SETTINGS))
    coordinator.async_write_settings = AsyncMock()
    return coordinator


@pytest.mark.asyncio
async def test_a_backup_saves_everything_the_inverter_reports(hass, coordinator, tmp_path):
    result = await async_backup(hass, coordinator, "INV1")

    assert result["settings"] == len(SETTINGS)
    assert result["file"].startswith("INV1_") and result["file"].endswith(".json")
    saved = json.loads((tmp_path / "sunsynk_backups" / result["file"]).read_text())
    assert saved["serial"] == "INV1"
    assert saved["format"] == 1
    assert saved["settings"] == SETTINGS


@pytest.mark.asyncio
async def test_a_backup_uses_a_fresh_read(hass, coordinator, tmp_path):
    coordinator._async_read_settings.return_value = {**SETTINGS, "cap1": "77"}

    result = await async_backup(hass, coordinator, "INV1")

    saved = json.loads((tmp_path / "sunsynk_backups" / result["file"]).read_text())
    assert saved["settings"]["cap1"] == "77"


@pytest.mark.asyncio
async def test_a_failed_read_is_reported(hass, coordinator):
    coordinator._async_read_settings.side_effect = RuntimeError("cloud down")

    with pytest.raises(ServiceValidationError, match="Couldn't read"):
        await async_backup(hass, coordinator, "INV1")


@pytest.mark.asyncio
async def test_no_settings_yet_is_refused(hass, coordinator):
    coordinator.data = {}
    coordinator._async_read_settings.return_value = {}

    with pytest.raises(ServiceValidationError, match="no settings yet"):
        await async_backup(hass, coordinator, "INV1")


@pytest.mark.asyncio
async def test_a_deye_cloud_inverter_is_backed_up_from_what_it_reported(hass, tmp_path):
    coordinator = MagicMock(spec=DeyeCloudCoordinator)
    coordinator.data = {"INV1": {"settings": {"sysWorkMode": 1}}}

    result = await async_backup(hass, coordinator, "INV1")

    saved = json.loads((tmp_path / "sunsynk_backups" / result["file"]).read_text())
    assert saved["settings"] == {"sysWorkMode": 1}


def test_a_restore_writes_only_known_settings_that_differ():
    saved = {**SETTINGS, "cap1": "90", "sellTime1": "06:30", "battMode": "-1"}
    current = {**SETTINGS, "battMode": "2"}

    changes, skipped = plan_restore(saved, current)

    # cap1 differs; sellTime1 and time1on are the same; sn and unknownField
    # aren't settings this integration writes; battMode -1 is reported only.
    assert changes == {"cap1": "90"}
    assert set(skipped) == {"battMode"}


async def _save(hass, coordinator, **overrides):
    coordinator._async_read_settings.return_value = {**SETTINGS, **overrides}
    result = await async_backup(hass, coordinator, "INV1")
    coordinator._async_read_settings.return_value = dict(SETTINGS)
    return result["file"]


@pytest.mark.asyncio
async def test_a_dry_run_lists_the_changes_and_writes_nothing(hass, coordinator):
    file = await _save(hass, coordinator, cap1="90")

    result = await async_restore(
        hass, coordinator, "INV1", file, dry_run=True, busy=None
    )

    assert result["changes"] == {"cap1": {"now": "50", "backup": "90"}}
    assert result["dry_run"] is True
    coordinator.async_write_settings.assert_not_called()


@pytest.mark.asyncio
async def test_a_restore_writes_the_changes(hass, coordinator):
    file = await _save(hass, coordinator, cap1="90")

    await async_restore(hass, coordinator, "INV1", file, dry_run=False, busy=None)

    coordinator.async_write_settings.assert_awaited_once_with("INV1", {"cap1": "90"})


@pytest.mark.asyncio
async def test_nothing_to_change_writes_nothing_even_when_busy(hass, coordinator):
    file = await _save(hass, coordinator)

    result = await async_restore(
        hass, coordinator, "INV1", file, dry_run=False, busy="driving"
    )

    assert result["changes"] == {}
    coordinator.async_write_settings.assert_not_called()


@pytest.mark.asyncio
async def test_no_restore_while_something_drives_the_inverter(hass, coordinator):
    file = await _save(hass, coordinator, cap1="90")

    with pytest.raises(ServiceValidationError, match="Not restoring now: VSS"):
        await async_restore(
            hass, coordinator, "INV1", file, dry_run=False, busy="VSS"
        )

    coordinator.async_write_settings.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("file", "match"),
    [
        ("../secrets.yaml", "isn't a backup file name"),
        ("missing.json", "No backup missing.json"),
    ],
)
async def test_only_backup_files_can_be_restored(hass, coordinator, file, match):
    with pytest.raises(ServiceValidationError, match=match):
        await async_restore(hass, coordinator, "INV1", file, dry_run=True, busy=None)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("content", "match"),
    [
        ("not json", "Couldn't read backup"),
        ('{"settings": []}', "isn't a Sunsynk settings backup"),
        ('{"serial": "OTHER", "settings": {}}', "a backup of inverter OTHER"),
    ],
)
async def test_a_wrong_file_is_refused(hass, coordinator, tmp_path, content, match):
    folder = tmp_path / "sunsynk_backups"
    folder.mkdir()
    (folder / "x.json").write_text(content)

    with pytest.raises(ServiceValidationError, match=match):
        await async_restore(hass, coordinator, "INV1", "x.json", dry_run=True, busy=None)


# ── The actions ────────────────────────────────────────────────────────────


@pytest.fixture
async def actions():
    handlers = {}
    hass = MagicMock()
    hass.http.async_register_static_paths = AsyncMock()
    hass.services.async_register.side_effect = (
        lambda domain, name, handler, schema, **kwargs: handlers.__setitem__(
            name, handler
        )
    )
    await integration.async_setup(hass, {})
    return hass, handlers


def _call(data):
    return SimpleNamespace(data=data, context=SimpleNamespace(user_id=None))


@pytest.mark.asyncio
async def test_the_backup_action(actions):
    hass, handlers = actions
    coordinator = MagicMock()

    with (
        patch.object(integration, "_async_ensure_control_allowed", AsyncMock()),
        patch.object(integration, "_find_coordinator", return_value=coordinator),
        patch.object(
            integration, "async_backup", AsyncMock(return_value={"file": "f.json"})
        ) as backup,
    ):
        result = await handlers["backup_settings"](_call({"serial": "INV1"}))

    assert result == {"file": "f.json"}
    backup.assert_awaited_once_with(hass, coordinator, "INV1")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("charging", "owns", "busy"),
    [
        (False, False, None),
        (True, False, "Tariff Manager is charging or discharging this inverter"),
        (False, True, "the Virtual Slot Scheduler is driving its timer slots"),
    ],
)
async def test_the_restore_action_goes_to_the_write_target(actions, charging, owns, busy):
    hass, handlers = actions
    coordinator = MagicMock()
    coordinator.resolve_write_target.return_value = "MASTER"
    manager = MagicMock()
    manager.is_charging_active_for.return_value = charging
    manager.is_discharging_active_for.return_value = False
    hass.data = {
        DOMAIN: {
            "entry_tariff": manager,
            "entry_vslots": {"MASTER": MagicMock(owns_timer=owns)},
        }
    }

    with (
        patch.object(integration, "_async_ensure_control_allowed", AsyncMock()),
        patch.object(integration, "_find_coordinator", return_value=coordinator),
        patch.object(integration, "_find_entry_id_for_serial", return_value="entry"),
        patch.object(integration, "async_restore", AsyncMock(return_value={})) as restore,
    ):
        await handlers["restore_settings"](
            _call({"serial": "SLAVE", "file": "f.json", "dry_run": False})
        )

    restore.assert_awaited_once_with(
        hass, coordinator, "MASTER", "f.json", dry_run=False, busy=busy
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("service", ["backup_settings", "restore_settings"])
async def test_the_actions_refuse_an_unknown_inverter(actions, service):
    _hass, handlers = actions

    with (
        patch.object(integration, "_async_ensure_control_allowed", AsyncMock()),
        patch.object(integration, "_find_coordinator", return_value=None),
        pytest.raises(ValueError, match="No Sunsynk inverter"),
    ):
        await handlers[service](
            _call({"serial": "NOPE", "file": "f.json", "dry_run": True})
        )


def test_the_scheduler_says_when_it_drives_the_timer():
    from custom_components.sunsynk.virtual_slots import VirtualSlotScheduler

    scheduler = VirtualSlotScheduler(MagicMock(), MagicMock(), "entry", "INV1")
    scheduler._original_settings = {"cap1": 10}
    scheduler._standing_by = False
    assert scheduler.owns_timer is False  # switched off
    scheduler._enabled = True
    assert scheduler.owns_timer is True
    scheduler._standing_by = True
    assert scheduler.owns_timer is False
